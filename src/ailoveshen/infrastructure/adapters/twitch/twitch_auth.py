"""
Twitch のユーザーアクセストークンを、取るところから更新までまかなうアダプター（設計書 71 の追記）。

フォロー通知（EventSub）のトークンは 4 時間で切れる。手で作って `.env` に書く代わりに、public クライアント
（Client Secret なし）でも使えるデバイスコード方式で取り、更新トークンで更新して、ファイルに残す:

1. 保存したトークンがあって有効（`expires_at` の 10 分前まで）なら、そのまま使う
2. 期限が近い・切れているなら、更新トークンで更新する（**更新トークンは使い捨て**: 更新のたびに、新しい
   ものを先に（一時ファイルに書いて置き換えで）保存する。30 日使わないと失効する）
3. 更新トークンがない・無効なら、デバイスコード方式でやり直す: ログに URL とコードを出し、ブラウザを開き
   （`open_url`）、声でも知らせる（`notify`）ので、**配信者本人が許可を押す**（ここだけは人の操作が要る。
   許可されるまで配信は止めずに待つ）。ブラウザと声は、許可を求め始めたとき 1 回だけ（コードが切れて
   取り直すときはログだけ。何度もウィンドウを出さない）

Client Secret があれば（confidential クライアント）、更新の要求に付ける。ログにトークンは出さない。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Optional, Protocol

import httpx
from loguru import logger

DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# フォロー通知（channel.follow v2）に要るスコープ。チャットへの書き込み（chat:edit）は IRC 側で別に持つ
FOLLOW_SCOPES = ["moderator:read:followers"]
# 期限のこれだけ前から、更新する
REFRESH_MARGIN_SECONDS = 600.0
# Twitch はトークンを 1 時間ごとに validate するよう求めている
VALIDATE_INTERVAL_SECONDS = 3600.0
# デバイスコードのポーリングで `slow_down` と言われたら、この秒数だけ間隔を延ばす
SLOW_DOWN_SECONDS = 5.0


class TwitchAuthError(Exception):
    """人が直さないと取れない（許可されなかった、Client-Id が違う、手で書いたトークンは更新できない、など）。"""


class TokenSource(Protocol):
    """EventSub などが使うトークンの出どころ。"""

    async def get(self) -> str:
        """今使えるトークンを返す（必要なら更新・取得し直す）。"""

    async def refresh(self) -> str:
        """今のトークンを使わずに、取り直す（401 を返されたとき）。"""

    async def maintain(self) -> None:
        """接続している間、たまに呼ぶ（1 時間ごとの validate、期限が近ければ更新）。"""


class StaticToken:
    """手で作ったトークン（`TWITCH_EVENTSUB_TOKEN`）。更新はできない。"""

    def __init__(self, token: str) -> None:
        self._token = token.strip().removeprefix("oauth:")
        if not self._token:
            raise ValueError("a token is needed")

    async def get(self) -> str:
        return self._token

    async def refresh(self) -> str:
        raise TwitchAuthError(
            "手で書いたトークン（TWITCH_EVENTSUB_TOKEN）は更新できない: 空にすると、自動で取って更新する（docs/streaming.md）"
        )

    async def maintain(self) -> None:
        return None


class TwitchUserToken:
    """デバイスコード方式で取り、更新して、ファイルに残すトークン。"""

    def __init__(
        self,
        client_id: str,
        scopes: list[str],
        path: str | Path,
        client_secret: str = "",
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        refresh_margin_seconds: float = REFRESH_MARGIN_SECONDS,
        validate_interval_seconds: float = VALIDATE_INTERVAL_SECONDS,
        open_url: Callable[[str], Any] | None = None,
        notify: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        """
        Args:
            client_id: アプリの Client-Id（public でも confidential でもよい）
            scopes: 要るスコープ（保存したトークンに足りなければ、取り直す）
            path: トークンを残すファイル（`data/twitch_token.json`。git に入らない）
            client_secret: confidential クライアントの Secret（空なら付けない）
            http: Twitch の HTTP API を呼ぶもの（テストで差し替える）
            clock: 今の時刻（秒。テストで差し替える）
            sleep: 待つもの（テストで差し替える）
            open_url: 許可の URL を開くもの（`webbrowser.open`。None なら開かない）。許可を求め始めたとき 1 回だけ
            notify: 許可が要ることを知らせるもの（配信の声。None なら知らせない）。同じく 1 回だけ
        """
        if not client_id.strip():
            raise ValueError("a client id is needed")
        self._client_id = client_id.strip()
        self._scopes = list(scopes)
        self._path = Path(path)
        self._secret = client_secret.strip()
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._clock = clock
        self._sleep = sleep
        self._margin = refresh_margin_seconds
        self._validate_every = validate_interval_seconds
        self._lock = asyncio.Lock()
        self._loaded = False
        self._access = ""
        self._refresh = ""
        self._expires_at = 0.0
        self._validated_at = 0.0
        self._open_url = open_url
        self._notify = notify

    # ---- 保存 ----

    def _load(self) -> None:
        self._loaded = True
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            logger.warning(f"[twitch] 保存したトークンを読めない（{self._path}: {e}）: 取り直す")
            return
        if data.get("client_id") != self._client_id:
            logger.warning("[twitch] 保存したトークンは別の Client-Id で作られている: 取り直す")
            return
        missing = [s for s in self._scopes if s not in (data.get("scopes") or [])]
        if missing:
            logger.warning(f"[twitch] 保存したトークンにスコープ {', '.join(missing)} がない: 取り直す")
            return
        self._access = str(data.get("access_token") or "")
        self._refresh = str(data.get("refresh_token") or "")
        self._expires_at = float(data.get("expires_at") or 0.0)

    def _save(self, scopes: list[str]) -> None:
        """一時ファイルに書いて置き換える（途中で止まっても、使い捨ての更新トークンを失わない）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        data = {
            "client_id": self._client_id,
            "access_token": self._access,
            "refresh_token": self._refresh,
            "expires_at": self._expires_at,
            "scopes": scopes,
        }
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self._path)

    def _store(self, body: dict[str, Any]) -> None:
        """トークンの応答（access_token、refresh_token、expires_in、scope）を覚えて、保存する。"""
        self._access = str(body["access_token"])
        self._refresh = str(body.get("refresh_token") or "")
        self._expires_at = self._clock() + float(body.get("expires_in") or 0)
        scopes = body.get("scope")
        if isinstance(scopes, str):
            scopes = scopes.split()
        self._validated_at = self._clock()
        self._save(list(scopes or self._scopes))

    # ---- 使う側 ----

    def _fresh(self) -> bool:
        return bool(self._access) and self._expires_at - self._clock() > self._margin

    async def get(self) -> str:
        async with self._lock:
            if not self._loaded:
                self._load()
            if not self._fresh():
                await self._renew()
            return self._access

    async def refresh(self) -> str:
        async with self._lock:
            if not self._loaded:
                self._load()
            await self._renew()
            return self._access

    async def maintain(self) -> None:
        async with self._lock:
            if not self._loaded:
                self._load()
            if self._access and not self._fresh():
                await self._renew()
                return
            if self._access and self._clock() - self._validated_at >= self._validate_every:
                r = await self._http.get(VALIDATE_URL, headers={"Authorization": f"OAuth {self._access}"})
                if r.status_code == 401:
                    logger.warning("[twitch] トークンが無効になっていた: 更新する")
                    await self._renew()
                    return
                r.raise_for_status()
                self._validated_at = self._clock()

    # ---- 取る・更新する ----

    async def _renew(self) -> None:
        """更新トークンで更新する。なければ・無効なら、デバイスコード方式でやり直す。"""
        if self._refresh and await self._refresh_token():
            return
        await self._authorize()

    async def _refresh_token(self) -> bool:
        """更新できたら True。更新トークンが無効なら False（transient な失敗は例外のまま: 更新トークンは捨てない）。"""
        form = {"grant_type": "refresh_token", "refresh_token": self._refresh, "client_id": self._client_id}
        if self._secret:
            form["client_secret"] = self._secret
        r = await self._http.post(TOKEN_URL, data=form)
        if r.status_code in (400, 401):
            logger.warning(f"[twitch] 更新トークンが使えない（{r.status_code}: {r.text[:160]}）: 許可からやり直す")
            self._access = self._refresh = ""
            return False
        r.raise_for_status()
        self._store(r.json())
        logger.info("[twitch] トークンを更新した")
        return True

    async def _authorize(self) -> None:
        """デバイスコード方式: URL とコードを出して、配信者本人の許可を待つ（コードが切れたら取り直す）。"""
        scopes = " ".join(self._scopes)
        first = True
        while True:
            r = await self._http.post(DEVICE_URL, data={"client_id": self._client_id, "scopes": scopes})
            if r.status_code >= 400:
                raise TwitchAuthError(f"デバイスコードを取れない（{r.status_code}: {r.text[:200]}）: TWITCH_CLIENT_ID を確かめる")
            code = r.json()
            interval = float(code.get("interval") or 5)
            deadline = self._clock() + float(code.get("expires_in") or 1800)
            logger.warning(
                "[twitch] フォロー通知のトークンを取るため、配信者本人が次の URL を開いて許可してください: "
                f"{code.get('verification_uri')} （コード {code.get('user_code')}、"
                f"{int(float(code.get('expires_in') or 1800) // 60)} 分以内）。許可されるまで、フォロー通知だけ待つ"
            )
            if first:
                first = False
                await self._announce(str(code.get("verification_uri") or ""))
            while self._clock() < deadline:
                await self._sleep(interval)
                # 公式の表は `scopes`、例は `scope`: どちらでも通るよう両方送る
                r = await self._http.post(
                    TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "scopes": scopes,
                        "scope": scopes,
                        "device_code": code["device_code"],
                        "grant_type": DEVICE_GRANT,
                    },
                )
                if r.status_code == 200:
                    self._store(r.json())
                    logger.info("[twitch] トークンを取った（許可された）")
                    return
                message = _message(r)
                if "authorization_pending" in message:
                    continue
                if "slow_down" in message:
                    interval += SLOW_DOWN_SECONDS
                    continue
                if "expired" in message:
                    break  # 新しいコードを取り直す
                raise TwitchAuthError(f"許可されなかった（{r.status_code}: {r.text[:200]}）")
            else:
                logger.warning("[twitch] コードの期限が切れた: 新しいコードを取り直す")


    async def _announce(self, uri: str) -> None:
        """ブラウザで許可の画面を開き、声で知らせる（どちらの失敗も、許可を待つことを止めない）。"""
        if self._open_url is not None and uri:
            try:
                opened = await asyncio.to_thread(self._open_url, uri)
                if opened is False:
                    logger.warning("[twitch] ブラウザを開けなかった: 上の URL を自分で開く")
            except Exception as e:  # noqa: BLE001 - 開けなくても、URL はログにある
                logger.warning(f"[twitch] ブラウザを開けなかった（{type(e).__name__}: {e}）: 上の URL を自分で開く")
        if self._notify is not None:
            try:
                await self._notify()
            except Exception as e:  # noqa: BLE001 - 知らせられなくても、許可は待てる
                logger.warning(f"[twitch] 許可が要ることを声で知らせられなかった（{type(e).__name__}: {e}）")


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message") or "")
    except ValueError:
        return response.text


def build_token_source(
    client_id: str,
    static_token: str,
    path: str | Path,
    scopes: list[str],
    client_secret: str = "",
    open_url: Callable[[str], Any] | None = None,
    notify: Callable[[], Awaitable[Any]] | None = None,
) -> Optional[TokenSource]:
    """手で書いたトークンがあればそれを、なければ自動で取って更新するものを返す（Client-Id がなければ None）。"""
    if not client_id.strip():
        return None
    if static_token.strip():
        return StaticToken(static_token)
    return TwitchUserToken(
        client_id, scopes, path, client_secret=client_secret, open_url=open_url, notify=notify
    )
