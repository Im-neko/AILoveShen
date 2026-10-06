"""TwitchEventSub のテスト（偽の Twitch: HTTP は MockTransport、WebSocket は手元の偽物）。"""

import asyncio
import json

import httpx
import pytest

from ailoveshen.domain.value_objects import SupportKind
from ailoveshen.infrastructure.adapters.twitch.twitch_eventsub import (
    EventSubSetupError,
    TwitchEventSub,
    parse_follow,
)

CLIENT = "client123"
GOOD = {"client_id": CLIENT, "user_id": "77", "scopes": ["moderator:read:followers"], "login": "shen"}


def _msg(kind, payload=None, message_id="m1"):
    return json.dumps(
        {"metadata": {"message_type": kind, "message_id": message_id}, "payload": payload or {}}
    )


WELCOME = _msg("session_welcome", {"session": {"id": "sess1", "keepalive_timeout_seconds": 10}})


def _follow(name="ねこ", message_id="f1"):
    return _msg(
        "notification",
        {
            "subscription": {"type": "channel.follow"},
            "event": {"user_name": name, "user_login": name.lower()},
        },
        message_id,
    )


class FakeWs:
    """送られた順に返す偽の WebSocket。尽きたら、閉じるまで待つ。"""

    def __init__(self, messages):
        self.queue = asyncio.Queue()
        for m in messages:
            self.queue.put_nowait(m)
        self.closed = False

    async def recv(self):
        item = await self.queue.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    async def close(self):
        self.closed = True
        self.queue.put_nowait(None)


def _http(validate=GOOD, users=None, subscribe_status=202, log=None):
    log = log if log is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        log.append((request.method, str(request.url), request.content))
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200 if validate else 401, json=validate or {})
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json={"data": users if users is not None else [{"id": "99"}]})
        return httpx.Response(subscribe_status, json={"data": [{}]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), log


def _sub(ws_list, http, **kw):
    sockets = list(ws_list)
    urls = []

    async def connector(url):
        urls.append(url)
        return sockets.pop(0)

    sub = TwitchEventSub("#Shen", CLIENT, "oauth:tok", connector=connector, http=http, retry_seconds=0.01, **kw)
    return sub, urls


def test_parse_follow():
    e = parse_follow({"user_name": "ねこ", "user_login": "NEKO"})
    assert (e.kind, e.user_name, e.login) == (SupportKind.FOLLOW, "ねこ", "neko")
    assert parse_follow({}) is None


@pytest.mark.asyncio
async def test_subscribes_to_follows_v2_and_yields_them_once():
    http, log = _http()
    ws = FakeWs([WELCOME, _msg("session_keepalive"), _follow(), _follow(), _follow("まめ", "f2")])
    sub, _ = _sub([ws], http)
    got = []
    async for s in sub.supports():
        got.append(s.user_name)
        if len(got) == 2:
            await sub.close()
    assert got == ["ねこ", "まめ"]  # 同じ message_id は 1 回だけ
    post = next(c for c in log if c[0] == "POST")
    body = json.loads(post[2])
    assert body["type"] == "channel.follow" and body["version"] == "2"
    assert body["condition"] == {"broadcaster_user_id": "99", "moderator_user_id": "77"}
    assert body["transport"] == {"method": "websocket", "session_id": "sess1"}


@pytest.mark.asyncio
async def test_the_scope_and_the_client_id_are_checked_first_and_the_reason_is_said(caplog):
    for info, why in (
        ({**GOOD, "scopes": ["chat:edit"]}, "moderator:read:followers"),
        ({**GOOD, "client_id": "other"}, "別の Client-Id"),
    ):
        http, _ = _http(validate=info)
        sub, urls = _sub([FakeWs([WELCOME])], http)
        with pytest.raises(EventSubSetupError, match=why):
            await sub.prepare()
        assert [s async for s in sub.supports()] == []  # 直せない誤りは、つなぎ直さずに終わる
        assert urls == []  # つながない


@pytest.mark.asyncio
async def test_an_invalid_token_and_an_unknown_channel_are_setup_errors():
    http, _ = _http(validate=None)
    with pytest.raises(EventSubSetupError, match="無効か期限切れ"):
        await _sub([], http)[0].prepare()
    http, _ = _http(users=[])
    with pytest.raises(EventSubSetupError, match="見つからない"):
        await _sub([], http)[0].prepare()


@pytest.mark.asyncio
async def test_a_refused_subscription_ends_without_reconnecting():
    http, _ = _http(subscribe_status=403)
    sub, urls = _sub([FakeWs([WELCOME])], http)
    assert [s async for s in sub.supports()] == []
    assert len(urls) == 1


@pytest.mark.asyncio
async def test_a_dropped_connection_reconnects_and_subscribes_again():
    http, log = _http()
    first = FakeWs([WELCOME, _follow("あ", "a1"), None])  # 途中で切れる
    second = FakeWs([WELCOME, _follow("い", "b1")])
    sub, urls = _sub([first, second], http)
    got = []
    async for s in sub.supports():
        got.append(s.user_name)
        if len(got) == 2:
            await sub.close()
    assert got == ["あ", "い"]
    assert sum(1 for c in log if c[0] == "POST") == 2


@pytest.mark.asyncio
async def test_session_reconnect_switches_to_the_new_url_without_subscribing_again():
    http, log = _http()
    reconnect = _msg("session_reconnect", {"session": {"reconnect_url": "wss://new.example/ws"}})
    old = FakeWs([WELCOME, reconnect])
    new = FakeWs([WELCOME, _follow("う", "c1")])
    sub, urls = _sub([old, new], http)
    got = []
    async for s in sub.supports():
        got.append(s.user_name)
        await sub.close()
    assert got == ["う"]
    assert urls[1] == "wss://new.example/ws"
    assert old.closed  # ようこそが来てから、古い接続を閉じる
    assert sum(1 for c in log if c[0] == "POST") == 1


def test_needs_a_channel_a_client_id_and_a_token():
    for args in (("", CLIENT, "t"), ("shen", "", "t"), ("shen", CLIENT, "")):
        with pytest.raises(ValueError):
            TwitchEventSub(*args)
