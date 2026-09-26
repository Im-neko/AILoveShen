"""TwitchIrcChat のテスト（手元の偽の IRC サーバー）。"""

import asyncio

import pytest

from ailoveshen.infrastructure.adapters.twitch.twitch_irc_chat import TwitchIrcChat, parse_privmsg

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
    assert fake.received[0] == "CAP REQ :twitch.tv/tags"
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
    assert fake.received[:3] == ["CAP REQ :twitch.tv/tags", "PASS oauth:secret", "NICK shenbot"]
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
