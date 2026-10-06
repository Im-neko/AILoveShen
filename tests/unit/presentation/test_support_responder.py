"""SupportResponder のテスト。"""

import asyncio

import pytest

from ailoveshen.domain.events import SupportReceivedEvent
from ailoveshen.domain.value_objects import SupportEvent, SupportKind
from ailoveshen.presentation.services.support_responder import SupportResponder


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _follow(name, login=None):
    return SupportEvent(SupportKind.FOLLOW, name, login or name.lower())


def _responder(said, published=None, clock=None, **kwargs):
    async def say(text):
        said.append(text)

    async def publish(event):
        if published is not None:
            published.append(event)

    kwargs.setdefault("min_interval_seconds", 0)
    return SupportResponder(say, publish, clock=clock or Clock(), **kwargs)


@pytest.mark.asyncio
async def test_thanks_once_and_the_avatar_is_told_first():
    said, published = [], []
    r = _responder(said, published)
    r.accept(_follow("ねこ"))
    assert await r.thank_next()
    assert "ねこさん" in said[0] and "フォロー" in said[0]
    assert isinstance(published[0], SupportReceivedEvent)
    assert (published[0].support.user_name, published[0].count) == ("ねこ", 1)
    assert not await r.thank_next()


@pytest.mark.asyncio
async def test_follows_waiting_together_are_one_thanks_and_others_are_one_by_one():
    said, published = [], []
    r = _responder(said, published)
    for name in "あいうえお":
        r.accept(_follow(name))
    r.accept(SupportEvent(SupportKind.RAID, "らいど", amount=30))
    r.accept(_follow("か"))
    while await r.thank_next():
        pass
    assert len(said) == 2  # フォロー 6 人が 1 回、レイドが 1 回
    assert "ほか" in said[0] and "あさん" in said[0]
    assert "らいど" in said[1] and "30" in said[1]
    assert [e.count for e in published] == [6, 1]


@pytest.mark.asyncio
async def test_the_same_follower_is_not_thanked_again_within_the_window():
    said = []
    clock = Clock()
    r = _responder(said, clock=clock, dedupe_seconds=60)
    r.accept(_follow("ねこ"))
    r.accept(_follow("ねこ"))
    while await r.thank_next():
        pass
    assert len(said) == 1
    clock.now += 61
    r.accept(_follow("ねこ"))
    assert await r.thank_next()
    assert len(said) == 2


@pytest.mark.asyncio
async def test_ignored_accounts_get_nothing_and_a_full_backlog_drops_the_oldest():
    said = []
    r = _responder(said, ignore=["Shen", "nightbot"], backlog=2)
    r.accept(_follow("Shen"))
    r.accept(SupportEvent(SupportKind.CHEER, "bot", "nightbot", amount=100))
    assert not await r.thank_next()
    for n, name in enumerate(["Alpha", "Beta", "Gamma"]):
        r.accept(SupportEvent(SupportKind.SUBSCRIBE, name, f"u{n}"))
    while await r.thank_next():
        pass
    assert r.dropped == 1
    assert ["Alpha" in t for t in said] == [False, False]  # 古い 1 件（Alpha）が捨てられた
    assert "Beta" in said[0] and "Gamma" in said[1]


@pytest.mark.asyncio
async def test_waits_between_thanks(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    said = []
    r = _responder(said, min_interval_seconds=3)
    r.accept(SupportEvent(SupportKind.SUBSCRIBE, "あ", "a"))
    r.accept(SupportEvent(SupportKind.SUBSCRIBE, "い", "i"))
    while await r.thank_next():
        pass
    assert waits == [3.0]  # 2 回目の前だけ（時計が止まっているので、間の全部）


@pytest.mark.asyncio
async def test_a_failure_to_speak_does_not_stop_the_thanks():
    calls = []

    async def say(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("tts down")

    async def publish(event):
        pass

    r = SupportResponder(say, publish, min_interval_seconds=0)
    r.accept(SupportEvent(SupportKind.SUBSCRIBE, "あ", "a"))
    r.accept(SupportEvent(SupportKind.SUBSCRIBE, "い", "i"))
    while await r.thank_next():
        pass
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_run_reads_from_every_source_and_thanks():
    class Source:
        def __init__(self, events):
            self.events = events

        async def supports(self):
            for e in self.events:
                yield e
            await asyncio.Event().wait()  # つながったまま

        async def close(self):
            pass

    said = []
    r = _responder(said, batch_seconds=0)
    task = asyncio.create_task(
        r.run([Source([SupportEvent(SupportKind.CHEER, "あ", "a", amount=500)]), Source([_follow("い")])])
    )
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(said) == 2:
            break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(said) == 2
