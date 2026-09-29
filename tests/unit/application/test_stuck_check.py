"""StuckCheck（行動の記録から行き詰まりを Jev が確かめる、docs/design/34 §9）のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.stuck_check import StuckCheck
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import FastAnswer, FastVerdict, Goal
from tests.unit.application.test_play_use_cases import HAVE_PLANKS, _obs

GOAL = Goal(spec=HAVE_PLANKS, reason="r")


def _check(value="repeating", confidence=0.9, error=None, every=3):
    judge = AsyncMock()
    if error:
        judge.ask.side_effect = error
    else:
        judge.ask.return_value = FastVerdict({"stuck": FastAnswer("stuck", value, confidence)}, 30)
    return StuckCheck(judge, every_steps=every, recorder=Mock()), judge


async def _steps(check, n):
    obs = _obs()
    obs.state["self"]["position"] = {"x": 1, "y": 70, "z": 2}
    why = None
    for _ in range(n):
        check.note("dig oak_log at 1,70,2 (0.90) → failed: no path", obs)
        why = await check.check_if_due(GOAL, obs)
    return why


@pytest.mark.asyncio
async def test_a_sure_stuck_answer_asks_gemini_to_rethink_with_the_record():
    check, judge = _check()
    assert await _steps(check, 2) is None  # まだ聞く時ではない
    judge.ask.assert_not_awaited()
    why = await _steps(check, 1)
    assert "is stuck" in why and "repeating" in why  # 「is stuck」: Gemini は行動の記録で分析する
    state = judge.ask.await_args.args[0]
    assert len(state["recent_actions"]) == 3 and state["recent_actions"][0]["at"] == {"x": 1, "y": 70, "z": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,confidence,error",
    [("fine", 0.9, None), ("repeating", 0.5, None), ("flying", 0.9, None), (None, 0, ActionSelectionError("down"))],
)
async def test_fine_unsure_or_no_answer_does_nothing(value, confidence, error):
    check, _ = _check(value, confidence, error)
    assert await _steps(check, 3) is None


@pytest.mark.asyncio
async def test_the_record_starts_over_for_a_new_goal():
    check, judge = _check()
    await _steps(check, 2)
    check.reset()
    assert await _steps(check, 2) is None
    judge.ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_five_minutes_without_progress_asks_jev_even_before_the_step_count():
    """小目標の進み具合が 5 分変わらなければ、数ステップを待たず行動の記録を Jev に渡す。"""
    clock = {"now": 0.0}
    judge = AsyncMock()
    judge.ask.return_value = FastVerdict({"stuck": FastAnswer("stuck", "no_progress", 0.9)}, 30)
    check = StuckCheck(judge, every_steps=6, quiet_seconds=300, clock=lambda: clock["now"])
    obs = _obs()

    check.note("goto → ok", obs)
    clock["now"] = 299
    assert await check.check_if_due(GOAL, obs) is None  # まだ 5 分たたない
    judge.ask.assert_not_awaited()

    clock["now"] = 301
    check.note("goto → ok", obs)
    why = await check.check_if_due(GOAL, obs)
    assert "no_progress" in why
    assert judge.ask.await_args.args[0]["minutes_without_progress"] == 5.0


@pytest.mark.asyncio
async def test_progress_that_moves_restarts_the_five_minutes():
    clock = {"now": 0.0}
    judge = AsyncMock()
    judge.ask.return_value = FastVerdict({"stuck": FastAnswer("stuck", "fine", 0.9)}, 30)
    check = StuckCheck(judge, every_steps=6, quiet_seconds=300, clock=lambda: clock["now"])
    obs = _obs()
    check.note("dig", obs)
    clock["now"] = 250
    from dataclasses import replace

    moved = replace(obs, goal=replace(obs.goal, remaining=obs.goal.remaining - 1))  # 進んだ
    check.note("dig", moved)
    clock["now"] = 400  # 最初から 400 秒だが、進んだのは 150 秒前
    assert await check.check_if_due(GOAL, moved) is None
    judge.ask.assert_not_awaited()
    clock["now"] = 560  # 進んでから 310 秒
    await check.check_if_due(GOAL, moved)
    judge.ask.assert_awaited_once()
    # 聞いたあとの 5 分は聞き直さない
    clock["now"] = 600
    await check.check_if_due(GOAL, moved)
    judge.ask.assert_awaited_once()
