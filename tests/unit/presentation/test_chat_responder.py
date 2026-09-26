"""ChatResponder のテスト。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from ailoveshen.domain.value_objects import ChatComment
from ailoveshen.presentation.services.chat_responder import ChatResponder


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _responder(llm, said, clock=None, **kwargs):
    async def say(text):
        said.append(text)

    return ChatResponder(llm, session=lambda: "SESSION", say=say, clock=clock or Clock(), **kwargs)


@pytest.mark.asyncio
async def test_replies_one_at_a_time_with_the_session():
    llm = AsyncMock()
    llm.generate_response.side_effect = lambda user, msg, user_id=None, session=None: (
        f"{user}:{msg}"
    )
    said = []
    responder = _responder(llm, said)
    responder.accept(ChatComment("まめ", "ベッド作って！", user_id="1"))
    assert await responder.answer_next()
    assert said == ["まめ:ベッド作って！"]
    llm.generate_response.assert_awaited_with(
        "まめ", "ベッド作って！", user_id="1", session="SESSION"
    )
    assert not await responder.answer_next()


@pytest.mark.asyncio
async def test_keeps_only_the_newest_comments_and_skips_commands():
    llm = AsyncMock()
    llm.generate_response.side_effect = lambda user, msg, **kw: msg
    said = []
    responder = _responder(llm, said, backlog=2)
    for text in ["1", "!dice", "2", "3"]:
        responder.accept(ChatComment("u", text))
    while await responder.answer_next():
        pass
    assert said == ["2", "3"]
    assert responder.dropped == 1


@pytest.mark.asyncio
async def test_waits_between_replies_and_says_nothing_on_an_empty_reply(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    llm = AsyncMock()
    llm.generate_response.side_effect = ["", "ok"]
    said = []
    clock = Clock()
    responder = _responder(llm, said, clock=clock, min_interval_seconds=5.0)
    responder.accept(ChatComment("a", "x"))
    responder.accept(ChatComment("b", "y"))
    await responder.answer_next()
    clock.now += 2.0
    await responder.answer_next()
    assert said == ["ok"]
    assert waits == [3.0]


@pytest.mark.asyncio
async def test_run_reads_the_source_until_cancelled():
    class Source:
        async def comments(self):
            yield ChatComment("neko", "こんばんは")
            await asyncio.Event().wait()

        async def close(self):
            pass

    llm = AsyncMock()
    llm.generate_response.return_value = "こんばんは！"
    said = []
    responder = _responder(llm, said)
    task = asyncio.create_task(responder.run(Source()))
    for _ in range(50):
        await asyncio.sleep(0)
        if said:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert said == ["こんばんは！"]


@pytest.mark.asyncio
async def test_a_failed_reply_does_not_stop_the_chat():
    class Source:
        async def comments(self):
            yield ChatComment("a", "1")
            yield ChatComment("b", "2")
            await asyncio.Event().wait()

        async def close(self):
            pass

    llm = AsyncMock()
    llm.generate_response.side_effect = [RuntimeError("boom"), "ok"]
    said = []
    responder = _responder(llm, said, min_interval_seconds=0)
    task = asyncio.create_task(responder.run(Source()))
    for _ in range(100):
        await asyncio.sleep(0)
        if said:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert said == ["ok"]


class FakeSink:
    def __init__(self, can_post=True):
        self.can_post = can_post
        self.posted = []

    async def post(self, text):
        self.posted.append(text)
        return True


@pytest.mark.asyncio
async def test_own_account_gets_no_reply_but_commands_and_bots_get_nothing():
    llm = AsyncMock()
    llm.generate_response.side_effect = lambda user, msg, **kw: msg
    said = []
    sink = FakeSink()
    responder = _responder(llm, said, chat=sink, quiet=("Shen",), ignore=("StreamElements",))
    responder.accept(ChatComment("しぇん", "テスト中", login="shen"))
    responder.accept(ChatComment("StreamElements", "Follow!", login="streamelements"))
    responder.accept(ChatComment("StreamElements", "!commands", login="streamelements"))
    responder.accept(ChatComment("しぇん", "!commands", login="shen"))
    responder.accept(ChatComment("neko", "こんにちは", login="neko"))
    await asyncio.sleep(0)
    while await responder.answer_next():
        pass
    assert said == ["こんにちは"]
    assert sink.posted == ["使えるコマンド: !commands（この一覧）"]


@pytest.mark.asyncio
async def test_commands_list_goes_to_chat_once_per_cooldown_and_is_spoken_without_chat():
    from ailoveshen.application.use_cases.readings import NameReadings

    store = type("S", (), {"load": lambda self: (), "save": lambda self, r: None})()
    clock = Clock()
    said = []
    sink = FakeSink()
    responder = _responder(AsyncMock(), said, clock=clock, chat=sink, readings=NameReadings(store))
    responder.accept(ChatComment("neko", "!help"))
    responder.accept(ChatComment("tama", "!コマンド"))  # 30 秒以内: 飛ばす
    await asyncio.sleep(0)
    assert len(sink.posted) == 1
    assert sink.posted[0].startswith("使えるコマンド: !yomi <よみ>")
    assert not sink.posted[0].startswith("!")

    sink.can_post = False
    clock.now += 31
    responder.accept(ChatComment("neko", "!commands"))
    await asyncio.sleep(0)
    assert len(sink.posted) == 1
    assert said == [sink.posted[0]]


@pytest.mark.asyncio
async def test_refreshes_the_game_before_each_reply():
    """返事の前にゲームの様子を取り直す（行動の途中で道具が壊れた、など）。"""
    order = []
    llm = AsyncMock()

    async def generate(user, msg, **kw):
        order.append("reply")
        return "ok"

    async def refresh():
        order.append("refresh")

    llm.generate_response.side_effect = generate
    responder = _responder(llm, [], refresh=refresh)
    responder.accept(ChatComment("a", "ツルハシ壊れてるよ"))
    await responder.answer_next()
    assert order == ["refresh", "reply"]


@pytest.mark.asyncio
async def test_triage_skips_what_needs_no_reply_and_answers_urgent_ones_first():
    """仕分け（docs/design/34 §5）: 取り下げ・頼みを先に、返事の要らないものは飛ばす。"""
    from ailoveshen.application.use_cases.comment_triage import Triage

    kinds = {"こんにちは": Triage(False, "noise", 0.9, "jev"), "やっぱりいいや": Triage(True, "withdraw", 0.9, "jev"), "草": Triage(True, "chat", 0.9, "jev")}

    async def triage(comment):
        return kinds[comment.message]

    llm = AsyncMock()
    llm.generate_response.side_effect = lambda user, msg, **kw: f"re:{msg}"
    said = []
    responder = _responder(llm, said, backlog=5, triage=triage, min_interval_seconds=0)
    for text in ["草", "こんにちは", "やっぱりいいや"]:
        responder.accept(ChatComment("u", text))
    while await responder.answer_next():
        pass
    assert said == ["re:やっぱりいいや", "re:草"]
    assert responder.skipped == 1


@pytest.mark.asyncio
async def test_comments_that_arrive_while_triaging_are_triaged_too():
    from ailoveshen.application.use_cases.comment_triage import Triage

    responder = None

    async def triage(comment):
        if comment.message == "1":
            responder.accept(ChatComment("u", "2"))  # 仕分けを待つ間に届いた
        return Triage(True, "chat", 0.9, "jev")

    llm = AsyncMock()
    llm.generate_response.side_effect = lambda user, msg, **kw: msg
    said = []
    responder = _responder(llm, said, backlog=5, triage=triage, min_interval_seconds=0)
    responder.accept(ChatComment("u", "1"))
    while await responder.answer_next():
        pass
    assert said == ["1", "2"]
