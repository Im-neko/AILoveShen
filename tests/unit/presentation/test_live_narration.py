"""行動の途中の一言のテスト（docs/design/43_live_narration.md）。"""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.domain.events import GameActionExecutedEvent
from ailoveshen.domain.value_objects import (
    Activity,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
)
from ailoveshen.presentation.services.live_narration import LiveNarration, SpokenLines, similarity

GOAL = Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=16), "木を集める")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _obs(inventory, x=0.0):
    return GameObservation(
        state={"inventory": inventory, "self": {"position": {"x": x, "y": 64, "z": 0}}},
        candidates=(),
        health=20,
        food=20,
    )


def _setup(lines=(), intent="北の森で原木を集めてくるね", obs=None):
    clock = Clock()
    spoken = SpokenLines(clock=clock)
    said = []
    tts = {"busy": False}

    async def say(text):
        if spoken.repeats(text) is None:  # factories/stream.py の say と同じ
            spoken.remember(text)
            said.append(text)

    llm = AsyncMock()
    llm.generate_commentary.side_effect = list(lines)
    state = {"activity": Activity(goal=GOAL, intent=intent, observation=obs or _obs({}))}
    live = LiveNarration(
        llm,
        activity=lambda: state["activity"],
        say=say,
        spoken=spoken,
        is_idle=lambda: not tts["busy"],
        interval_seconds=10,
        clock=clock,
    )
    return live, clock, said, tts, llm, state


def test_similar_lines_are_the_same_content():
    assert similarity("原木を集めてくるね！", "原木を集めてくるね") == 1.0
    assert similarity("北の森で原木を集めてくるね", "南の洞窟で鉄を掘るよ") < 0.2


@pytest.mark.asyncio
async def test_quiet_is_measured_from_the_end_of_speech():
    """読み上げている間は数えない。終わってから 10 秒で話す（新しい intent は LLM なしで）。"""
    live, clock, said, tts, llm, _ = _setup()
    tts["busy"] = True
    clock.now = 30
    await live.tick()  # 話している
    tts["busy"] = False
    clock.now = 35
    await live.tick()
    assert said == []  # 終わってから 5 秒
    clock.now = 40
    await live.tick()
    assert said == ["北の森で原木を集めてくるね"]
    llm.generate_commentary.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_long_action_gets_new_lines_with_what_changed_and_no_repeats():
    """60 秒続く 1 つの行動: intent は 1 回だけ、あとは変わったことを渡す。似た行は言わない。"""
    live, clock, said, _, llm, state = _setup(
        lines=["森が見えてきた！", "森が見えてきたよ！", "原木が 4 本になった、あと少し！"]
    )
    await live.on_action(GameActionExecutedEvent(action_id="dig oak_log", ok=True, result="got"))
    for t, logs, x in [(10, 0, 0), (20, 0, 0), (30, 0, 12), (40, 4, 12)]:
        state["activity"] = Activity(
            goal=GOAL, intent="北の森で原木を集めてくるね", observation=_obs({"oak_log": logs}, x)
        )
        clock.now = t
        await live.tick()
    assert said == [
        "北の森で原木を集めてくるね",
        "森が見えてきた！",
        "原木が 4 本になった、あと少し！",
    ]
    last = llm.generate_commentary.await_args_list[-1].kwargs
    assert last["purpose"] == "live" and last["record"] is False
    events = last["recent_events"]
    assert "行動の結果: dig oak_log → 成功: got" in events
    assert "持ち物の増減: oak_log +4" in events
    assert "森が見えてきた！" in events[-1]  # 直前に話したこととは違う内容に
    # 直前の実況と食い違わない（2026-09-27:「パンをクラフトするよ」の 30 秒後に「パンは明日作れる」）
    assert "食い違うことも言わない" in events[-1]
    assert events[0] == f"今の小目標: {GOAL.describe()}（{GOAL.reason}）"


@pytest.mark.asyncio
async def test_a_line_made_while_someone_started_speaking_is_dropped():
    live, clock, said, tts, llm, _ = _setup(lines=["今日はいい天気だね"], intent="dig oak_log")

    async def slow(**kwargs):
        tts["busy"] = True  # 作っている間に返事が始まった
        return "今日はいい天気だね"

    llm.generate_commentary.side_effect = slow
    clock.now = 10
    await live.tick()
    assert said == []


@pytest.mark.asyncio
async def test_nothing_while_not_playing_and_the_english_ids_of_jev_are_not_read():
    live, clock, said, _, llm, state = _setup(
        lines=["よし、掘るよ！"], intent="dig oak_log at 1,2,3"
    )
    state["activity"] = None
    clock.now = 10
    await live.tick()
    assert said == []
    state["activity"] = Activity(goal=GOAL, intent="dig oak_log at 1,2,3", observation=_obs({}))
    clock.now = 20
    await live.tick()
    assert said == ["よし、掘るよ！"]  # id はそのまま読まず、一言を作らせる
