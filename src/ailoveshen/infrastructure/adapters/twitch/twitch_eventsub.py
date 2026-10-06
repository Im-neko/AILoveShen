"""
Twitch の EventSub（WebSocket）で、フォローを受け取るアダプター。

フォローは IRC に流れてこないので、EventSub で受ける（サブスク、ギフト、レイド、ビッツは IRC の
USERNOTICE で足りる: `TwitchIrcChat.supports()`）。要るもの:

- Client-Id（`TWITCH_CLIENT_ID`）と、**その Client-Id で作った**ユーザーアクセストークンで、スコープ
  `moderator:read:followers` があるもの。トークンの持ち主は、配信者本人かそのチャンネルのモデレーター。
  トークンは `TokenSource`（`twitch_auth.py`）から受け取る: 手で書いたもの（更新できない）か、自動で取って
  更新するもの（4 時間で切れるので、401 を返されたら更新して 1 回だけやり直し、接続している間は
  1 時間ごとに確かめる）
- 起動時に、トークンを検証して（`id.twitch.tv/oauth2/validate`）、Client-Id の食い違い・スコープの不足を
  はっきり言う。直せない設定の誤りなら、購読せずに終わる（黙って何も届かないままにしない）

つながらない・切れたら、1 秒から倍々に最長 60 秒待ってつなぎ直す。`session_reconnect` は新しい URL へ先に
つないで、ようこそが来てから古い接続を閉じる（購読は引き継がれる）。例外は出さない。
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Optional

import httpx
from loguru import logger

from ailoveshen.application.ports.output.support_source import ISupportSource
from ailoveshen.domain.value_objects import SupportEvent, SupportKind
from ailoveshen.infrastructure.adapters.twitch.twitch_auth import StaticToken, TokenSource, TwitchAuthError

WS_URL = "wss://eventsub.wss.twitch.tv/ws"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
HELIX = "https://api.twitch.tv/helix"
FOLLOW_SCOPE = "moderator:read:followers"
# キープアライブの間隔にこれだけ足して黙っていたら、切れたとみなす
KEEPALIVE_GRACE_SECONDS = 10.0
DEFAULT_KEEPALIVE_SECONDS = 10.0
SEEN_MESSAGES = 200

Connector = Callable[[str], Awaitable[Any]]  # (url) -> 接続（recv() -> str、close()）


class EventSubSetupError(Exception):
    """直さないと届かない設定の誤り（トークン、スコープ、Client-Id、チャンネル）。"""


async def _connect(url: str) -> Any:
    import websockets

    return await websockets.connect(url)


def parse_follow(event: dict[str, Any]) -> Optional[SupportEvent]:
    """channel.follow の event を SupportEvent にする。"""
    name = str(event.get("user_name") or event.get("user_login") or "").strip()
    if not name:
        return None
    return SupportEvent(SupportKind.FOLLOW, name, str(event.get("user_login") or "").lower() or None)


class TwitchEventSub(ISupportSource):
    """
    Twitch のチャンネルのフォローを EventSub で受ける。

    Raises:
        ValueError: channel か client_id か token が空のとき。
    """

    def __init__(
        self,
        channel: str,
        client_id: str,
        token: str | TokenSource,
        connector: Connector | None = None,
        http: httpx.AsyncClient | None = None,
        ws_url: str = WS_URL,
        retry_seconds: float = 1.0,
        retry_max_seconds: float = 60.0,
    ) -> None:
        """
        Args:
            channel: チャンネルのログイン名（`#` は付けても付けなくてもよい）
            client_id: トークンを作ったアプリの Client-Id
            token: ユーザーアクセストークン（`moderator:read:followers`。`oauth:` は付けても付けなくてもよい）か、
                トークンの出どころ（`TokenSource`）。ログには出さない
            connector: WebSocket を開くもの（テストで差し替える）
            http: Twitch の HTTP API を呼ぶもの（テストで差し替える）
            ws_url: 最初につなぐ URL
            retry_seconds: つなぎ直す前の最初の待ち
            retry_max_seconds: つなぎ直す前の最長の待ち
        """
        channel = channel.strip().lstrip("#").lower()
        if isinstance(token, str):
            token = token.strip().removeprefix("oauth:")
            if not token:
                raise ValueError("EventSub needs a channel, a client id and a user access token")
            token = StaticToken(token)
        if not channel or not client_id.strip():
            raise ValueError("EventSub needs a channel, a client id and a user access token")
        self._channel = channel
        self._client_id = client_id.strip()
        self._token: TokenSource = token
        self._connect = connector or _connect
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._url = ws_url
        self._retry = retry_seconds
        self._retry_max = retry_max_seconds
        self._closed = False
        self._seen: deque[str] = deque(maxlen=SEEN_MESSAGES)
        self._ws: Any = None
        self._broadcaster_id = ""
        self._moderator_id = ""

    @property
    def channel(self) -> str:
        return self._channel

    async def _call(self, method: str, url: str, **kw: Any) -> httpx.Response:
        """トークンを付けて呼ぶ。401 なら、トークンを取り直して 1 回だけやり直す（4 時間で切れる）。"""
        for attempt in (1, 2):
            token = await self._token.get() if attempt == 1 else await self._token.refresh()
            headers = {"Authorization": f"Bearer {token}", "Client-Id": self._client_id}
            r = await self._http.request(method, url, headers=headers, **kw)
            if r.status_code != 401 or attempt == 2:
                return r
        raise AssertionError("unreachable")

    async def prepare(self) -> None:
        """
        トークンを検証して、配信者とトークンの持ち主の id を調べる。

        Raises:
            EventSubSetupError: 直さないと届かない誤り（トークンが無効、Client-Id の食い違い、
                スコープの不足、チャンネルが見つからない）
            httpx.HTTPError: Twitch に届かなかったとき（あとでやり直せる）
        """
        r = await self._http.get(VALIDATE_URL, headers={"Authorization": f"OAuth {await self._token.get()}"})
        if r.status_code == 401:
            # 期限切れ・無効: 取り直してもう一度（手で書いたトークンは取り直せない）
            try:
                r = await self._http.get(VALIDATE_URL, headers={"Authorization": f"OAuth {await self._token.refresh()}"})
            except TwitchAuthError as e:
                raise EventSubSetupError(
                    f"トークンが無効か期限切れ（TWITCH_EVENTSUB_TOKEN。docs/streaming.md）: {e}"
                ) from e
        if r.status_code == 401:
            raise EventSubSetupError("トークンが無効か期限切れ（TWITCH_EVENTSUB_TOKEN。docs/streaming.md）")
        r.raise_for_status()
        info = r.json()
        if str(info.get("client_id", "")) != self._client_id:
            raise EventSubSetupError(
                f"トークンは別の Client-Id（{info.get('client_id')}）で作られている: TWITCH_CLIENT_ID "
                "（同じアプリ）でトークンを作り直す。食い違うと購読が 401 になる"
            )
        if FOLLOW_SCOPE not in (info.get("scopes") or []):
            raise EventSubSetupError(
                f"フォロー通知が届かない: トークンにスコープ {FOLLOW_SCOPE} がない"
                f"（今のスコープ: {', '.join(info.get('scopes') or []) or 'なし'}）"
            )
        self._moderator_id = str(info.get("user_id") or "")
        users = await self._call("GET", f"{HELIX}/users", params={"login": self._channel})
        users.raise_for_status()
        data = users.json().get("data") or []
        if not data:
            raise EventSubSetupError(f"チャンネル {self._channel} が Twitch に見つからない")
        self._broadcaster_id = str(data[0]["id"])

    async def _subscribe(self, session_id: str) -> None:
        """フォローの購読を作る（ようこそから 10 秒以内）。"""
        body = {
            "type": "channel.follow",
            "version": "2",
            "condition": {
                "broadcaster_user_id": self._broadcaster_id,
                "moderator_user_id": self._moderator_id,
            },
            "transport": {"method": "websocket", "session_id": session_id},
        }
        r = await self._call("POST", f"{HELIX}/eventsub/subscriptions", json=body)
        if r.status_code in (401, 403):
            raise EventSubSetupError(
                f"フォローの購読を断られた（{r.status_code}: {r.text[:120]}）。トークンの持ち主は"
                "配信者本人か、そのチャンネルのモデレーターでなければならない"
            )
        r.raise_for_status()
        logger.info(f"Twitch のフォロー通知を購読した（#{self._channel}）")

    async def supports(self) -> AsyncIterator[SupportEvent]:
        failures = 0
        prepared = False
        url = self._url
        while not self._closed:
            try:
                if not prepared:
                    await self.prepare()
                    prepared = True
                async for support in self._session(url):
                    failures = 0
                    yield support
                url = self._url
                if self._closed:
                    return
                logger.warning("Twitch のフォロー通知の接続が切れた")
            except (EventSubSetupError, TwitchAuthError) as e:
                logger.error(f"[eventsub] {e}")
                return  # 直さないと届かない: つなぎ直さない
            except Exception as e:  # noqa: BLE001 - どの失敗でもつなぎ直す（ポートの約束）
                if self._closed:
                    return
                logger.warning(f"Twitch のフォロー通知につながらない（{type(e).__name__}: {e}）")
            finally:
                await self._drop()
            failures += 1
            wait = min(self._retry_max, self._retry * 2 ** (failures - 1))
            logger.info(f"{wait:.0f} 秒後に Twitch のフォロー通知につなぎ直す")
            await asyncio.sleep(wait)

    async def _session(self, url: str) -> AsyncIterator[SupportEvent]:
        """1 つの接続を、切れるまで読む。"""
        self._ws = await self._connect(url)
        timeout = DEFAULT_KEEPALIVE_SECONDS + KEEPALIVE_GRACE_SECONDS
        subscribed = False
        while not self._closed:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            await self._token.maintain()  # 1 時間ごとの validate、期限が近ければ更新
            message = json.loads(raw)
            meta = message.get("metadata") or {}
            kind = meta.get("message_type")
            payload = message.get("payload") or {}
            if kind == "session_welcome":
                session = payload.get("session") or {}
                timeout = float(session.get("keepalive_timeout_seconds") or 10) + KEEPALIVE_GRACE_SECONDS
                if not subscribed:
                    await self._subscribe(str(session["id"]))
                    subscribed = True
            elif kind == "session_reconnect":
                await self._switch(str((payload.get("session") or {}).get("reconnect_url") or ""))
            elif kind == "notification":
                if meta.get("message_id") in self._seen:
                    continue
                self._seen.append(str(meta.get("message_id")))
                sub = payload.get("subscription") or {}
                if sub.get("type") == "channel.follow":
                    support = parse_follow(payload.get("event") or {})
                    if support is not None:
                        logger.info(f"[support] follow: {support.user_name}")
                        yield support
            elif kind == "revocation":
                sub = payload.get("subscription") or {}
                logger.warning(
                    f"Twitch が購読を取り消した（{sub.get('type')}: {sub.get('status')}）: 接続し直す"
                )
                return
            # session_keepalive はタイムアウトを延ばすだけ（読めた時点で延びている）

    async def _switch(self, url: str) -> None:
        """Twitch の保守: 新しい URL へ先につなぎ、ようこそが来てから古い接続を閉じる（購読は引き継がれる）。"""
        if not url:
            raise ConnectionError("session_reconnect without a url")
        new = await self._connect(url)
        try:
            while True:
                raw = await asyncio.wait_for(new.recv(), timeout=DEFAULT_KEEPALIVE_SECONDS + KEEPALIVE_GRACE_SECONDS)
                if (json.loads(raw).get("metadata") or {}).get("message_type") == "session_welcome":
                    break
        except BaseException:
            await new.close()
            raise
        old, self._ws = self._ws, new
        await old.close()

    async def _drop(self) -> None:
        ws, self._ws = self._ws, None
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 - 閉じるときの失敗は気にしない
            pass

    async def close(self) -> None:
        self._closed = True
        await self._drop()
        await self._http.aclose()
