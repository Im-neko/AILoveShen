"""小目標の終わりの記録（docs/design/82）のテスト。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.goal_ends import (
    CUT,
    DIED,
    FAILED,
    MET,
    end_category,
    goal_end_entry,
)
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.value_objects import Goal, GoalPredicate, GoalSpec
from tests.unit.application.test_play_use_cases import _obs, _session

FOOD = GoalSpec(GoalPredicate.HAVE, item="food", count=16)
NOW = datetime(2026, 10, 2, 3, 0, 0, tzinfo=UTC)


class TestEndCategory:
    """goal_end_reason の文から、終わり方を分ける。"""

    @pytest.mark.parametrize(
        ("reason", "category"),
        [
            ("goal have(food, 16) is met", MET),
            ("goal have(food, 16) is stuck (actions keep failing)", FAILED),
            ("goal have(food, 16) stalled (no progress in 8 steps)", FAILED),
            ("goal built(storehouse) ran for 40 steps", FAILED),
            (
                "goal have(food, 16) is cut because the streamer died and respawned "
                "(the position changed and the items carried may be lost)",
                DIED,
            ),
            ("the time of day changed from day to dusk", CUT),
            ("goal through_night() is over: the night has passed (day)", CUT),
            ("goal have(log, 4) is reconsidered: you told neko you would stop", CUT),
            ("the objective m2 that goal have(log, 4) served has ended", CUT),
        ],
    )
    def test_the_reason_decides_the_category(self, reason, category):
        assert end_category(reason) == category

    def test_a_stuck_objective_added_after_a_met_goal_does_not_make_it_a_failure(self):
        """大目標が進まない印は後ろに足されるだけ: 小目標自身が達成なら達成。"""
        reason = "goal have(log, 4) is met; objective m1 has made no progress in 60 steps"
        assert end_category(reason) == MET

    def test_a_reconsidered_reason_that_mentions_stuck_is_a_cut(self):
        reason = "goal have(log, 4) is reconsidered: the goal is stuck in a loop"
        assert end_category(reason) == CUT

    def test_a_stalled_objective_with_a_goal_still_running_is_a_failure(self):
        reason = "the objective m1 has made no progress in 60 steps"
        assert end_category(reason) == FAILED


class TestGoalEndEntry:
    """終わる小目標の 1 件。"""

    def test_it_describes_how_the_goal_went(self):
        session = _session(FOOD)
        session.steps_in_goal = 12
        session.consecutive_failures = 1
        session.stalled_steps = 3
        session.least_remaining = 5
        start = session.goal.set_at
        entry = goal_end_entry(
            session,
            _obs(remaining=5),
            "goal have(food, 16) stalled (no progress in 3 steps)",
            start + timedelta(seconds=90),
        )

        assert entry["event"] == "goal_end"
        assert entry["goal"] == "have(food, 16)"
        assert entry["predicate"] == "have"
        assert entry["kind"] == "have(food)"  # 数を無視した鍵
        assert entry["category"] == FAILED
        assert entry["steps"] == 12
        assert entry["seconds"] == 90.0
        assert entry["stalled_steps"] == 3
        assert entry["least_remaining"] == 5
        assert entry["remaining_end"] == 5
        assert entry["survival"] is False
        assert entry["objective"] == session.plan.get("m1").title

    def test_a_goal_resumed_after_a_restart_is_timed_from_the_restart(self):
        """引き継いだ小目標の `set_at` は昨日のまま: 止まっていた間（22 時間）を秒数に数えない。"""
        session = _session(FOOD)
        session.resume_goal(session.goal, "day", 6, 0, 5, 16, None)
        resumed = session.goal_resumed_at
        entry = goal_end_entry(
            session, _obs(), "goal have(food, 16) is met", resumed + timedelta(seconds=30)
        )

        assert entry["resumed"] is True
        assert entry["seconds"] == 30.0
        assert entry["steps"] == 6  # 歩数は引き継いだ数え

    def test_a_goal_set_after_a_resume_is_timed_from_when_it_was_set(self):
        session = _session(FOOD)
        session.resume_goal(session.goal, "day", 6, 0, 5, 16, None)
        session.set_goal(Goal(FOOD, objective_id="m1"), "day")
        entry = goal_end_entry(
            session,
            _obs(),
            "goal have(food, 16) is met",
            session.goal.set_at + timedelta(seconds=40),
        )

        assert entry["resumed"] is False
        assert entry["seconds"] == 40.0

    def test_a_survival_goal_has_no_objective(self):
        session = _session()
        session.set_goal(Goal(GoalSpec(GoalPredicate.THROUGH_NIGHT)), "night")
        entry = goal_end_entry(session, _obs(), "goal through_night() is met", NOW)

        assert entry["survival"] is True
        assert entry["objective"] is None
        assert entry["kind"] == "through_night"
        assert entry["phase_start"] == "night"

    def test_deaths_are_the_count_since_the_goal_was_set(self):
        session = _session()
        session.last_observation = replace(_obs(), state={"deaths": 2})
        session.set_goal(Goal(FOOD, objective_id="m1"), "day")
        obs = replace(_obs(), state={"deaths": 3})
        reason = "goal have(food, 16) is cut because the streamer died and respawned"
        entry = goal_end_entry(session, obs, reason, NOW)

        assert entry["category"] == DIED
        assert entry["deaths"] == 1

    def test_deaths_are_unknown_without_the_bridge_count(self):
        session = _session(FOOD)
        entry = goal_end_entry(session, _obs(), "goal have(food, 16) is met", NOW)

        assert entry["deaths"] is None


class TestRecordedWhenTheGoalEnds:
    """AdvancePlayUseCase は、小目標を終わらせるときに記録する。"""

    @staticmethod
    def _use_case(recorder):
        return AdvancePlayUseCase(
            bridge=AsyncMock(),
            text_generator=AsyncMock(),
            prompt_builder=Mock(),
            action_selector=AsyncMock(),
            event_publisher=AsyncMock(),
            conversation=Conversation(),
            objectives=Mock(),
            town=AsyncMock(),
            notes=NoteKeeper(Mock(load=Mock(return_value=None))),
            goal_end_recorder=recorder,
        )

    @pytest.mark.asyncio
    async def test_the_ended_goal_is_recorded_before_the_next_one_resets_the_count(self):
        recorder = Mock()
        session = _session(FOOD)
        session.steps_in_goal = 7

        await self._use_case(recorder)._end_goal(
            session, _obs(met=True, remaining=0), "goal have(food, 16) is met"
        )

        recorder.record.assert_called_once()
        entry = recorder.record.call_args.args[0]
        assert entry["category"] == MET
        assert entry["steps"] == 7

    @pytest.mark.asyncio
    async def test_a_goal_kept_after_a_failed_decision_is_recorded_once(self):
        recorder = Mock()
        session = _session(FOOD)
        use_case = self._use_case(recorder)
        reason = "goal have(food, 16) is cut because the streamer died and respawned"

        await use_case._end_goal(session, _obs(), reason)
        await use_case._end_goal(session, _obs(), reason)

        recorder.record.assert_called_once()

    @pytest.mark.asyncio
    async def test_the_next_goal_is_recorded_again(self):
        recorder = Mock()
        session = _session(FOOD)
        use_case = self._use_case(recorder)

        await use_case._end_goal(session, _obs(), "goal have(food, 16) is met")
        session.set_goal(Goal(GoalSpec(GoalPredicate.HAVE, item="oak_log", count=4)), "day")
        await use_case._end_goal(session, _obs(), "goal have(oak_log, 4) is met")

        assert recorder.record.call_count == 2

    @pytest.mark.asyncio
    async def test_nothing_is_recorded_without_a_goal(self):
        recorder = Mock()
        session = _session()

        await self._use_case(recorder)._end_goal(session, _obs(), "no goal yet")

        recorder.record.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_broken_record_does_not_stop_the_play(self):
        recorder = Mock()
        recorder.record.side_effect = ValueError("boom")
        session = _session(FOOD)

        await self._use_case(recorder)._end_goal(session, _obs(), "goal have(food, 16) is met")

    @pytest.mark.asyncio
    async def test_it_works_without_a_recorder(self):
        session = _session(FOOD)

        await self._use_case(None)._end_goal(session, _obs(), "goal have(food, 16) is met")
