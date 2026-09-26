"""襲われたときの反射を Jev に選ばせるテスト（docs/design/28_jev_reflex.md）。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.danger import (
    QUESTION,
    DangerWatcher,
    ReflexPolicy,
    reflex_question,
)
from ailoveshen.domain.entities import MidGoalPlan, PlaySession
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import (
    ActionResult,
    FastAnswer,
    FastQuestionKind,
    FastVerdict,
    Goal,
    GoalPredicate,
    GoalSpec,
    Mission,
)
from tests.unit.application.test_play_use_cases import _obs

DANGER = {
    "id": 7,
    "trigger": "hurt",
    "target": {"id": 12, "name": "skeleton", "distance_m": 11.0},
    "options": ["fight", "flee", "go_home", "keep_distance", "ignore"],
    "rule": "flee",
    "choice": "flee",
}
STATE = {"self": {"health": 12}, "danger": DANGER, "home": {"distance_m": 20, "inside": False}}


def _judge(choice, confidence=0.9, delay=0.0):
    judge = AsyncMock()

    async def ask(state, questions):
        await asyncio.sleep(delay)
        return FastVerdict({QUESTION: FastAnswer(QUESTION, choice, confidence)}, 300, 900)

    judge.ask.side_effect = ask
    return judge


def _bridge(state=STATE):
    bridge = AsyncMock()
    bridge.danger.return_value = state
    bridge.steer_reflex.return_value = True
    return bridge


FAST = ReflexPolicy(poll_seconds=0.01, timeout_seconds=0.2, min_confidence=0.5)


def test_the_question_offers_only_what_the_bridge_can_do_now():
    q = reflex_question({**DANGER, "options": ["fight", "ignore"]})
    assert q.kind == FastQuestionKind.CHOICE
    assert set(q.criteria) == {"fight", "ignore"}
    assert "took damage" in q.instructions and "skeleton" in q.instructions


@pytest.mark.asyncio
async def test_jev_steers_the_reflex_once_per_danger_and_it_is_recorded():
    bridge, recorder = _bridge(), Mock()
    watcher = DangerWatcher(bridge, _judge("go_home", 0.8), recorder, FAST)

    assert await watcher.check_once() == "go_home"
    assert await watcher.check_once() is None  # 同じ危険には 1 回だけ
    bridge.steer_reflex.assert_awaited_once_with(7, "go_home", 0.8)
    entry = recorder.record.call_args.args[0]
    assert (entry["rule"], entry["jev"], entry["steered"]) == ("flee", "go_home", True)
    assert entry["state"]["home"] == {"distance_m": 20, "inside": False}  # まわりも毎回渡す


@pytest.mark.asyncio
async def test_unsure_late_or_failing_jev_keeps_the_rule():
    for judge in (
        _judge("fight", 0.3),  # 自信がない
        _judge("fight", 0.9, delay=0.5),  # 遅い
        _judge("dance", 0.9),  # 選択肢にない
    ):
        bridge = _bridge()
        assert await DangerWatcher(bridge, judge, None, FAST).check_once() is None
        bridge.steer_reflex.assert_not_awaited()
    failing = AsyncMock()
    failing.ask.side_effect = ActionSelectionError("Jev down")
    bridge = _bridge()
    assert await DangerWatcher(bridge, failing, None, FAST).check_once() is None


@pytest.mark.asyncio
async def test_no_danger_no_call():
    bridge, judge = _bridge(None), _judge("fight")
    assert await DangerWatcher(bridge, judge, None, FAST).check_once() is None
    judge.ask.assert_not_awaited()


def _session():
    plan = MidGoalPlan(mission=Mission("家を建てる"))
    s = PlaySession(blueprint=None, plan=plan, max_stalled_steps=2, max_consecutive_failures=2)
    s.set_goal(Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=4)), "day")
    return s


def test_steps_cut_by_an_attack_are_not_failures_of_the_small_goal():
    session = _session()
    obs = _obs(remaining=3)
    session.track_progress(obs)
    for text in (
        "failed: interrupted: took damage (zombie nearby)",
        "failed: reflex: zombie 3m away",
    ):
        session.record(ActionResult("dig log", False, text, 1.0))
        session.track_progress(obs)
    session.record(ActionResult("dig log", False, "x", 1.0, interrupted=True))
    session.track_progress(obs)
    assert session.consecutive_failures == 0
    assert session.stalled_steps == 0
    assert session.goal_end_reason(obs) == ""
    # ふつうの失敗は今までどおり数える
    session.record(ActionResult("dig log", False, "failed: no path", 1.0))
    session.record(ActionResult("dig log", False, "failed: no path", 1.0))
    assert "stuck" in session.goal_end_reason(obs)
