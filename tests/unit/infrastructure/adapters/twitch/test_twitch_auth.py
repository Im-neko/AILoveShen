"""TwitchUserToken のテスト（偽の Twitch: HTTP は MockTransport、時計と待ちは手元の偽物）。"""

import json
import stat

import httpx
import pytest

from ailoveshen.infrastructure.adapters.twitch.twitch_auth import (
    FOLLOW_SCOPES,
    StaticToken,
    TwitchAuthError,
    TwitchUserToken,
    build_token_source,
)

CLIENT = "client123"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class FakeTwitch:
    """/oauth2/device、/oauth2/token、/oauth2/validate を答える偽の Twitch。"""

    def __init__(self, token_replies=None, device=None, validate_status=200):
        self.requests: list[tuple[str, dict]] = []
        self.token_replies = list(token_replies or [])
        self.device = device or {
            "device_code": "dev1",
            "user_code": "ABCDEFGH",
            "verification_uri": "https://www.twitch.tv/activate?device-code=ABCDEFGH",
            "expires_in": 1800,
            "interval": 5,
        }
        self.validate_status = validate_status
        self.validates = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        form = dict(httpx.QueryParams(request.content.decode())) if request.content else {}
        self.requests.append((path, form))
        if path == "/oauth2/device":
            return httpx.Response(200, json=self.device)
        if path == "/oauth2/validate":
            self.validates += 1
            return httpx.Response(self.validate_status, json={"client_id": CLIENT})
        status, body = self.token_replies.pop(0)
        return httpx.Response(status, json=body)

    def http(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def token_forms(self):
        return [form for path, form in self.requests if path == "/oauth2/token"]


def _ok(access="acc1", refresh="ref1", expires_in=14400, scope=("moderator:read:followers",)):
    return 200, {"access_token": access, "refresh_token": refresh, "expires_in": expires_in, "scope": list(scope)}


PENDING = (400, {"status": 400, "message": "authorization_pending"})


def _token(tmp_path, twitch, clock=None, **kw):
    clock = clock or Clock()
    return TwitchUserToken(
        CLIENT, FOLLOW_SCOPES, tmp_path / "token.json", http=twitch.http(), clock=clock, sleep=clock.sleep, **kw
    ), clock


def _saved(tmp_path):
    return json.loads((tmp_path / "token.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_the_first_start_asks_for_a_device_code_waits_for_the_approval_and_saves_the_token(tmp_path, caplog):
    twitch = FakeTwitch([PENDING, PENDING, _ok()])
    token, clock = _token(tmp_path, twitch)
    start = clock.now
    assert await token.get() == "acc1"
    # デバイスコードを要求し、interval（5 秒）ごとにポーリングした
    assert twitch.requests[0] == ("/oauth2/device", {"client_id": CLIENT, "scopes": "moderator:read:followers"})
    forms = twitch.token_forms()
    assert len(forms) == 3 and clock.now - start == 15
    assert forms[0]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code" and forms[0]["device_code"] == "dev1"
    assert forms[0]["scopes"] == forms[0]["scope"] == "moderator:read:followers"
    saved = _saved(tmp_path)
    assert saved["access_token"] == "acc1" and saved["refresh_token"] == "ref1" and saved["client_id"] == CLIENT
    assert saved["scopes"] == ["moderator:read:followers"] and saved["expires_at"] == clock.now + 14400
    assert stat.S_IMODE((tmp_path / "token.json").stat().st_mode) == 0o600
    assert "acc1" not in caplog.text and "ref1" not in caplog.text  # トークンはログに出さない


@pytest.mark.asyncio
async def test_the_url_and_the_code_are_logged_for_the_broadcaster(tmp_path):
    from loguru import logger

    messages = []
    sink = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
    try:
        token, _ = _token(tmp_path, FakeTwitch([_ok()]))
        await token.get()
    finally:
        logger.remove(sink)
    shown = " ".join(messages)
    assert "https://www.twitch.tv/activate?device-code=ABCDEFGH" in shown and "ABCDEFGH" in shown


@pytest.mark.asyncio
async def test_a_valid_saved_token_is_used_without_calling_twitch(tmp_path):
    twitch = FakeTwitch([_ok()])
    token, clock = _token(tmp_path, twitch)
    await token.get()
    twitch.requests.clear()
    # 再起動したつもり: 新しいオブジェクトがファイルから読む
    again, _ = _token(tmp_path, twitch, clock)
    assert await again.get() == "acc1"
    assert twitch.requests == []


@pytest.mark.asyncio
async def test_an_expiring_token_is_refreshed_and_the_one_time_refresh_token_is_replaced(tmp_path):
    twitch = FakeTwitch([_ok(), _ok(access="acc2", refresh="ref2")])
    token, clock = _token(tmp_path, twitch)
    await token.get()
    clock.now += 14400 - 300  # 期限の 5 分前 = 更新の余裕（10 分）の内側
    assert await token.get() == "acc2"
    refresh = twitch.token_forms()[-1]
    assert refresh == {"grant_type": "refresh_token", "refresh_token": "ref1", "client_id": CLIENT}  # secret なし（public）
    assert _saved(tmp_path)["refresh_token"] == "ref2"  # 使い捨て: すぐ新しいものを保存した
    # まだ新しいので、また呼んでも更新しない
    before = len(twitch.requests)
    assert await token.get() == "acc2" and len(twitch.requests) == before


@pytest.mark.asyncio
async def test_a_client_secret_is_sent_with_the_refresh_only_when_there_is_one(tmp_path):
    twitch = FakeTwitch([_ok(), _ok(access="acc2", refresh="ref2")])
    token, clock = _token(tmp_path, twitch, client_secret="s3cret")
    await token.get()
    clock.now += 14400
    await token.get()
    assert twitch.token_forms()[-1]["client_secret"] == "s3cret"


@pytest.mark.asyncio
async def test_a_dead_refresh_token_goes_back_to_the_device_flow(tmp_path):
    twitch = FakeTwitch([_ok(), (400, {"status": 400, "message": "Invalid refresh token"}), _ok(access="acc3", refresh="ref3")])
    token, clock = _token(tmp_path, twitch)
    await token.get()
    clock.now += 40 * 24 * 3600  # 30 日使わなかった
    assert await token.get() == "acc3"
    assert [p for p, _ in twitch.requests if p == "/oauth2/device"] == ["/oauth2/device"] * 2
    assert _saved(tmp_path)["refresh_token"] == "ref3"


@pytest.mark.asyncio
async def test_a_failure_that_is_not_the_token_keeps_the_refresh_token(tmp_path):
    twitch = FakeTwitch([_ok(), (503, {"message": "down"})])
    token, clock = _token(tmp_path, twitch)
    await token.get()
    clock.now += 14400
    with pytest.raises(httpx.HTTPStatusError):
        await token.get()
    assert _saved(tmp_path)["refresh_token"] == "ref1"  # 一時的な失敗で、使えるはずの更新トークンを捨てない


@pytest.mark.asyncio
async def test_slow_down_lengthens_the_wait_and_an_expired_code_is_asked_again(tmp_path):
    twitch = FakeTwitch(
        [
            (400, {"status": 400, "message": "slow_down"}),
            PENDING,
            (400, {"status": 400, "message": "expired_token"}),
            _ok(),
        ]
    )
    token, clock = _token(tmp_path, twitch)
    start = clock.now
    await token.get()
    assert clock.now - start == 5 + 10 + 10 + 5  # 5 → slow_down で 10 秒間隔、期限切れのあと新しいコードで 5 秒
    assert [p for p, _ in twitch.requests if p == "/oauth2/device"] == ["/oauth2/device"] * 2


@pytest.mark.asyncio
async def test_a_denied_approval_and_a_refused_device_request_are_auth_errors(tmp_path):
    token, _ = _token(tmp_path, FakeTwitch([(400, {"status": 400, "message": "access_denied"})]))
    with pytest.raises(TwitchAuthError, match="許可されなかった"):
        await token.get()

    class Refusing(FakeTwitch):
        def handler(self, request):
            return httpx.Response(400, json={"message": "invalid client"})

    token, _ = _token(tmp_path, Refusing())
    with pytest.raises(TwitchAuthError, match="TWITCH_CLIENT_ID"):
        await token.get()


@pytest.mark.asyncio
async def test_a_saved_token_for_another_client_or_missing_a_scope_is_not_used(tmp_path):
    (tmp_path / "token.json").write_text(
        json.dumps(
            {"client_id": "other", "access_token": "x", "refresh_token": "y", "expires_at": 9e12, "scopes": FOLLOW_SCOPES}
        )
    )
    twitch = FakeTwitch([_ok()])
    token, _ = _token(tmp_path, twitch)
    assert await token.get() == "acc1"
    (tmp_path / "token.json").write_text(
        json.dumps({"client_id": CLIENT, "access_token": "x", "refresh_token": "y", "expires_at": 9e12, "scopes": ["chat:edit"]})
    )
    twitch2 = FakeTwitch([_ok(access="acc9")])
    token, _ = _token(tmp_path, twitch2)
    assert await token.get() == "acc9"


@pytest.mark.asyncio
async def test_a_broken_file_is_not_fatal(tmp_path):
    (tmp_path / "token.json").write_text("{not json")
    token, _ = _token(tmp_path, FakeTwitch([_ok()]))
    assert await token.get() == "acc1"


@pytest.mark.asyncio
async def test_maintain_validates_hourly_and_refreshes_a_token_twitch_says_is_invalid(tmp_path):
    twitch = FakeTwitch([_ok(), _ok(access="acc2", refresh="ref2")])
    token, clock = _token(tmp_path, twitch)
    await token.get()
    await token.maintain()
    assert twitch.validates == 0  # 取ったばかり
    clock.now += 3601
    await token.maintain()
    assert twitch.validates == 1
    clock.now += 3601
    twitch.validate_status = 401
    await token.maintain()
    assert twitch.validates == 2 and await token.get() == "acc2"  # 無効と言われたので更新した


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_refresh(tmp_path):
    import asyncio

    twitch = FakeTwitch([_ok(), _ok(access="acc2", refresh="ref2")])
    token, clock = _token(tmp_path, twitch)
    await token.get()
    clock.now += 14400
    got = await asyncio.gather(*(token.get() for _ in range(5)))
    assert got == ["acc2"] * 5
    assert sum(1 for f in twitch.token_forms() if f.get("grant_type") == "refresh_token") == 1


@pytest.mark.asyncio
async def test_a_hand_made_token_is_used_as_it_is_and_cannot_be_refreshed():
    static = StaticToken("oauth:mine")
    assert await static.get() == "mine"
    await static.maintain()
    with pytest.raises(TwitchAuthError, match="更新できない"):
        await static.refresh()
    with pytest.raises(ValueError):
        StaticToken("  ")


def test_build_token_source_needs_the_client_id_and_prefers_a_hand_made_token(tmp_path):
    assert build_token_source("", "tok", tmp_path / "t.json", FOLLOW_SCOPES) is None
    assert isinstance(build_token_source("cid", "tok", tmp_path / "t.json", FOLLOW_SCOPES), StaticToken)
    assert isinstance(build_token_source("cid", "", tmp_path / "t.json", FOLLOW_SCOPES), TwitchUserToken)


@pytest.mark.asyncio
async def test_the_browser_is_opened_and_the_stream_says_so_once_per_approval(tmp_path):
    opened, said = [], []

    async def notify():
        said.append(1)

    twitch = FakeTwitch([(400, {"status": 400, "message": "expired_token"}), _ok()])
    clock = Clock()
    token = TwitchUserToken(
        CLIENT, FOLLOW_SCOPES, tmp_path / "token.json", http=twitch.http(), clock=clock, sleep=clock.sleep,
        open_url=lambda url: opened.append(url) or True, notify=notify,
    )
    await token.get()
    # コードが切れて取り直しても（デバイスコードを 2 回要求）、ブラウザと声は最初の 1 回だけ
    assert [p for p, _ in twitch.requests if p == "/oauth2/device"] == ["/oauth2/device"] * 2
    assert opened == ["https://www.twitch.tv/activate?device-code=ABCDEFGH"] and said == [1]


@pytest.mark.asyncio
async def test_a_browser_or_voice_that_fails_does_not_stop_the_wait(tmp_path):
    def broken_open(url):
        raise OSError("no display")

    async def broken_notify():
        raise RuntimeError("tts down")

    twitch = FakeTwitch([_ok()])
    clock = Clock()
    token = TwitchUserToken(
        CLIENT, FOLLOW_SCOPES, tmp_path / "token.json", http=twitch.http(), clock=clock, sleep=clock.sleep,
        open_url=broken_open, notify=broken_notify,
    )
    assert await token.get() == "acc1"


@pytest.mark.asyncio
async def test_a_valid_saved_token_opens_no_browser(tmp_path):
    twitch = FakeTwitch([_ok()])
    token, clock = _token(tmp_path, twitch)
    await token.get()
    opened = []
    again = TwitchUserToken(
        CLIENT, FOLLOW_SCOPES, tmp_path / "token.json", http=twitch.http(), clock=clock, sleep=clock.sleep,
        open_url=opened.append,
    )
    assert await again.get() == "acc1" and opened == []
