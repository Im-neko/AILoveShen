"""TwitchIrcChat のテスト（手元の偽の IRC サーバー）。"""

import asyncio

import pytest

from ailoveshen.domain.value_objects import SupportKind
from ailoveshen.infrastructure.adapters.twitch.twitch_irc_chat import (
    TwitchIrcChat,
    parse_privmsg,
    parse_support,
)

TAGGED = (
    "@badge-info=;display-name=まめ;user-id=12345;mod=0 "
    ":mame!mame@mame.tmi.twitch.tv PRIVMSG #shen :ベッド作って！"
)


def test_a_tagged_privmsg_becomes_a_comment():
    c = parse_privmsg(TAGGED)
    assert (c.user_name, c.message, c.user_id) == ("まめ", "ベッド作って！", "12345")


def test_without_tags_the_nick_is_the_name_and_other_lines_are_not_comments():
    c = parse_privmsg(":neko!neko@neko.tmi.twitch.tv PRIVMSG #shen :hi : there")
    assert (c.user_name, c.message, c.user_id) == ("neko", "hi : there", None)
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome") is None
    assert parse_privmsg("PING :tmi.twitch.tv") is None
    assert parse_privmsg(":neko!neko@neko.tmi.twitch.tv PRIVMSG #shen :") is None


class FakeTwitch:
    """接続ごとに決まった行を送る偽のサーバー。受け取った行を残す。"""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.received = []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        lines = self.scripts.pop(0) if self.scripts else []
        for _ in range(3):  # CAP、NICK、JOIN
            self.received.append((await reader.readline()).decode().strip())
        for line in lines:
            if line == "<wait-pong>":
                self.received.append((await reader.readline()).decode().strip())
                continue
            writer.write(f"{line}\r\n".encode())
            await writer.drain()
        writer.close()


@pytest.mark.asyncio
async def test_reads_comments_answers_ping_and_reconnects():
    fake = FakeTwitch(
        [
            [
                ":justinfan1!justinfan1@justinfan1.tmi.twitch.tv JOIN #shen",
                "PING :tmi.twitch.tv",
                "<wait-pong>",
                TAGGED,
                "RECONNECT",
            ],
            [":neko!neko@neko.tmi.twitch.tv PRIVMSG #shen :こんばんは"],
        ]
    )
    port = await fake.start()
    chat = TwitchIrcChat(
        "#Shen",
        opener=lambda: asyncio.open_connection("127.0.0.1", port),
        retry_seconds=0.01,
    )
    got = []

    async def read():
        async for c in chat.comments():
            got.append((c.user_name, c.message))
            if len(got) == 2:
                await chat.close()

    await asyncio.wait_for(read(), timeout=5)
    fake.server.close()

    assert got == [("まめ", "ベッド作って！"), ("neko", "こんばんは")]
    assert fake.received[0] == "CAP REQ :twitch.tv/tags twitch.tv/commands"
    assert fake.received[1].startswith("NICK justinfan")
    assert fake.received[2] == "JOIN #shen"
    assert "PONG :tmi.twitch.tv" in fake.received


@pytest.mark.asyncio
async def test_a_refused_connection_is_retried_not_raised():
    attempts = 0

    async def opener():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionRefusedError("no network")
        await chat.close()
        raise ConnectionRefusedError("closed")

    chat = TwitchIrcChat("shen", opener=opener, retry_seconds=0.01)
    assert [c async for c in chat.comments()] == []
    assert attempts == 3


def test_a_channel_is_required():
    with pytest.raises(ValueError):
        TwitchIrcChat(" # ")


@pytest.mark.asyncio
async def test_a_hanging_connection_times_out_and_is_retried():
    attempts = 0

    async def opener():
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            await chat.close()
            raise ConnectionRefusedError("closed")
        await asyncio.Event().wait()  # 応答しない

    chat = TwitchIrcChat("shen", opener=opener, retry_seconds=0.01, connect_timeout_seconds=0.05)
    assert [c async for c in chat.comments()] == []
    assert attempts == 2


@pytest.mark.asyncio
async def test_an_unexpected_error_is_retried_too():
    attempts = 0

    async def opener():
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            await chat.close()
        raise RuntimeError("unexpected")

    chat = TwitchIrcChat("shen", opener=opener, retry_seconds=0.01)
    assert [c async for c in chat.comments()] == []
    assert attempts == 2


def test_the_login_is_kept_in_lower_case():
    assert parse_privmsg(TAGGED).login == "mame"


@pytest.mark.asyncio
async def test_with_a_token_it_logs_in_and_can_post():
    fake = FakeTwitch(
        # 偽のサーバーは最初の 3 行だけ先に読む: JOIN と書き込みは続けて読む
        [[":shenbot!shenbot@shenbot.tmi.twitch.tv JOIN #shen", "<wait-pong>", "<wait-pong>"]]
    )
    port = await fake.start()
    chat = TwitchIrcChat(
        "shen",
        opener=lambda: asyncio.open_connection("127.0.0.1", port),
        login="ShenBot",
        token="oauth:secret",
    )
    assert not chat.can_post

    async def read():
        async for _ in chat.comments():
            pass

    task = asyncio.create_task(read())
    for _ in range(100):
        if chat.can_post:
            break
        await asyncio.sleep(0.01)
    assert await chat.post("使えるコマンド:\n !commands")
    for _ in range(100):
        if len(fake.received) >= 5:
            break
        await asyncio.sleep(0.01)
    await chat.close()
    task.cancel()
    fake.server.close()

    # CAP の後に PASS と NICK（FakeTwitch は最初の 3 行を読む）、JOIN、書き込み
    assert fake.received[:3] == ["CAP REQ :twitch.tv/tags twitch.tv/commands", "PASS oauth:secret", "NICK shenbot"]
    assert fake.received[3] == "JOIN #shen"
    assert fake.received[4] == "PRIVMSG #shen :使えるコマンド: !commands"


@pytest.mark.asyncio
async def test_a_failed_login_falls_back_to_reading_anonymously():
    fake = FakeTwitch([[":tmi.twitch.tv NOTICE * :Login authentication failed"], []])
    port = await fake.start()
    chat = TwitchIrcChat(
        "shen",
        opener=lambda: asyncio.open_connection("127.0.0.1", port),
        retry_seconds=0.01,
        token="bad",
    )

    async def read():
        async for _ in chat.comments():
            pass

    task = asyncio.create_task(read())
    for _ in range(200):
        if len(fake.received) >= 6:
            break
        await asyncio.sleep(0.01)
    await chat.close()
    task.cancel()
    fake.server.close()

    assert fake.received[1] == "PASS oauth:bad"
    assert fake.received[4].startswith("NICK justinfan")  # 2 回目は匿名
    assert chat.login == "" and not chat.can_post


def _notice(**tags):
    body = ";".join(f"{k.replace('_', '-')}={v}" for k, v in tags.items())
    return f"@{body} :tmi.twitch.tv USERNOTICE #shen"


class TestParseSupport:
    """USERNOTICE と bits タグから、応援を読む。"""

    def test_sub_and_resub(self):
        sub = parse_support(_notice(msg_id="sub", display_name="ねこ", login="neko", msg_param_sub_plan="2000"))
        assert (sub.kind, sub.user_name, sub.tier) == (SupportKind.SUBSCRIBE, "ねこ", 2)
        resub = parse_support(
            _notice(msg_id="resub", display_name="ねこ", msg_param_cumulative_months="6", msg_param_sub_plan="Prime")
            + " :ずっと応援するよ"
        )
        assert (resub.kind, resub.months, resub.tier, resub.message) == (
            SupportKind.RESUBSCRIBE,
            6,
            1,
            "ずっと応援するよ",
        )

    def test_a_mass_gift_is_thanked_once_and_its_single_gifts_are_not_counted(self):
        mass = parse_support(_notice(msg_id="submysterygift", display_name="A", msg_param_mass_gift_count="5"))
        assert (mass.kind, mass.amount) == (SupportKind.GIFT, 5)
        part = _notice(msg_id="subgift", display_name="A", msg_param_community_gift_id="123")
        assert parse_support(part) is None
        one = parse_support(
            _notice(msg_id="subgift", display_name="A", msg_param_recipient_display_name="B")
        )
        assert (one.kind, one.amount, one.recipient) == (SupportKind.GIFT, 1, "B")

    def test_an_anonymous_gifter_has_a_name_to_thank(self):
        anon = parse_support(
            _notice(msg_id="submysterygift", display_name="AnAnonymousGifter", login="ananonymousgifter")
        )
        assert anon.user_name == "匿名"

    def test_raid_and_bits(self):
        raid = parse_support(_notice(msg_id="raid", display_name="らいど", msg_param_viewerCount="42"))
        assert (raid.kind, raid.user_name, raid.amount) == (SupportKind.RAID, "らいど", 42)
        cheer = parse_support(
            "@bits=100;display-name=ねこ :neko!neko@neko.tmi.twitch.tv PRIVMSG #shen :cheer100 いつも見てる"
        )
        assert (cheer.kind, cheer.amount, cheer.message) == (
            SupportKind.CHEER,
            100,
            "cheer100 いつも見てる",
        )

    def test_others_are_not_support(self):
        assert parse_support(TAGGED) is None  # bits のない普通のコメント
        assert parse_support(_notice(msg_id="giftpaidupgrade", display_name="A")) is None
        assert parse_support("PING :tmi.twitch.tv") is None


@pytest.mark.asyncio
async def test_supports_arrive_while_the_chat_is_read():
    fake = FakeTwitch(
        [
            [
                ":justinfan1!justinfan1@justinfan1.tmi.twitch.tv JOIN #shen",
                _notice(msg_id="raid", display_name="らいど", msg_param_viewerCount="9"),
                TAGGED,
            ]
        ]
    )
    port = await fake.start()
    chat = TwitchIrcChat("shen", opener=lambda: asyncio.open_connection("127.0.0.1", port))
    got = []

    async def comments():
        async for c in chat.comments():
            got.append(("comment", c.user_name))

    async def supports():
        async for s in chat.supports():
            got.append(("support", s.user_name))
            await chat.close()

    await asyncio.wait_for(asyncio.gather(comments(), supports()), timeout=5)
    assert ("support", "らいど") in got
    assert ("comment", "まめ") in got
