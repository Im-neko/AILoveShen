"""SpeechCheck（発言を今の状態と照らす、設計書 65 §4）のテスト。"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from ailoveshen.application.use_cases.speech_check import SpeechCheck, speech_state
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import (
    Activity,
    FastAnswer,
    FastVerdict,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
)


def _activity(inside_home: bool = False, has_home: bool = True, goal: bool = True) -> Activity:
    obs = GameObservation(
        state={"home": {"door": {"x": 30, "z": 0}}, "self": {"position": {"x": 0, "z": 0}}},
        candidates=(),
        health=20.0,
        food=20,
        has_home=has_home,
        inside_home=inside_home,
    )
    return Activity(
        goal=Goal(GoalSpec(GoalPredicate.HAVE, item="torch", count=4), "暗い") if goal else None,
        observation=obs,
        intent="松明を作る",
        acting=True,
    )


def _judge(**answers: tuple[bool, float]) -> AsyncMock:
    judge = AsyncMock()
    judge.ask.return_value = FastVerdict(
        answers={name: FastAnswer(name, value, conf) for name, (value, conf) in answers.items()},
        elapsed_ms=20,
    )
    return judge


class TestSpeechState:
    def test_the_state_says_where_she_is_and_what_she_is_doing(self):
        state = speech_state(_activity())

        assert state["current_goal"] == "have(torch, 4)"
        assert state["current_action"] == "松明を作る"
        assert state["inside_home"] is False
        assert state["home_distance_m"] == 30

    def test_without_a_home_no_place_is_stated(self):
        assert "inside_home" not in speech_state(_activity(has_home=False))
        assert speech_state(None) == {}


class TestBundledGoal:
    def _activity(self, met_first: bool, all_met: bool = False) -> Activity:
        from ailoveshen.domain.value_objects import BundleMember, GoalStatus

        goal = Goal(
            GoalSpec(GoalPredicate.HAVE, item="furnace", count=1),
            "石炭を燃やす",
            together=(GoalSpec(GoalPredicate.HAVE, item="coal", count=1),),
        )
        status = GoalStatus(
            met=all_met,
            remaining=1,
            members=(
                BundleMember("have(furnace, 1)", met_first, 0 if met_first else 1),
                BundleMember("have(coal, 1)", all_met, 0 if all_met else 1),
            ),
        )
        obs = GameObservation(state={}, candidates=(), health=20.0, food=20, goal=status)
        return Activity(goal=goal, observation=obs)

    def test_only_the_conditions_still_open_are_the_current_goal(self):
        state = speech_state(self._activity(met_first=True))

        assert state["current_goal"] == "have(coal, 1)"  # かまどができた、は正しい
        assert state["goal_met"] is False

    def test_a_goal_that_is_met_but_not_ended_yet_may_be_said_done(self):
        state = speech_state(self._activity(met_first=True, all_met=True))

        assert state["goal_met"] is True
        assert [q.name for q in SpeechCheck.questions(has_home=False, has_goal=True, goal_met=True)] == [
            "contradicts_plan"
        ]
        from ailoveshen.application.use_cases.speech_check import problems_of

        assert problems_of({"claims_goal_done": [True, 0.9]}, state) == ()


class TestCheck:
    @pytest.mark.asyncio
    async def test_saying_she_is_in_the_home_while_outside_is_a_mismatch(self):
        judge = _judge(claims_inside_home=(True, 0.9), claims_outside_home=(False, 0.9), claims_goal_done=(False, 0.9), contradicts_plan=(False, 0.9))

        finding = await SpeechCheck(judge).check("家に入れたよ", _activity(inside_home=False))

        assert finding.checked and not finding.ok
        assert "家の外（家まで 30m）" in finding.problems[0]
        assert finding.answers["claims_inside_home"] == [True, 0.9]

    @pytest.mark.asyncio
    async def test_the_true_place_is_not_a_problem(self):
        judge = _judge(claims_inside_home=(True, 0.9), claims_outside_home=(False, 0.9), claims_goal_done=(False, 0.9), contradicts_plan=(False, 0.9))

        finding = await SpeechCheck(judge).check("家に入れたよ", _activity(inside_home=True))

        assert finding.ok and finding.checked

    @pytest.mark.asyncio
    async def test_outside_claim_while_inside_and_done_claim_and_plan_claim(self):
        judge = _judge(
            claims_inside_home=(False, 0.9),
            claims_outside_home=(True, 0.8),
            claims_goal_done=(True, 0.8),
            contradicts_plan=(True, 0.8),
        )

        finding = await SpeechCheck(judge).check("外に出て松明ができたから鉄を掘るよ", _activity(inside_home=True))

        assert len(finding.problems) == 3
        assert "家の中" in finding.problems[0]
        assert "まだ済んでいない" in finding.problems[1]
        assert "今の行動: 松明を作る" in finding.problems[2]

    @pytest.mark.asyncio
    async def test_an_unsure_yes_is_not_a_mismatch(self):
        judge = _judge(claims_inside_home=(True, 0.6), claims_outside_home=(False, 0.9), claims_goal_done=(False, 0.9), contradicts_plan=(False, 0.9))

        finding = await SpeechCheck(judge).check("家に入れたよ", _activity(inside_home=False))

        assert finding.ok

    @pytest.mark.asyncio
    async def test_only_questions_that_can_be_compared_are_asked(self):
        judge = _judge()

        await SpeechCheck(judge).check("こんにちは", None)
        judge.ask.assert_not_called()

        await SpeechCheck(judge).check("こんにちは", _activity(has_home=False, goal=False))
        names = [q.name for q in judge.ask.call_args.args[1]]
        assert names == ["claims_underground", "claims_on_surface"]

        await SpeechCheck(judge).check("こんにちは", _activity(has_home=False))
        names = [q.name for q in judge.ask.call_args.args[1]]
        assert names == ["claims_underground", "claims_on_surface", "claims_goal_done", "contradicts_plan"]

    @pytest.mark.asyncio
    async def test_a_reply_is_checked_only_for_where_she_is(self):
        """返事は次の予定を答えてよい: 小目標とは照らさず、今いる場所だけ照らす。"""
        judge = _judge()

        await SpeechCheck(judge).check("次は石を掘るよ", _activity(), place_only=True)

        names = [q.name for q in judge.ask.call_args.args[1]]
        assert names == [
            "claims_inside_home",
            "claims_outside_home",
            "claims_underground",
            "claims_on_surface",
        ]

    @pytest.mark.asyncio
    async def test_saying_she_is_underground_on_the_surface_is_a_problem(self):
        """実測（2026-10-10 19:32）: 家の中にいるのに「地上へ出る道をつなげていくね」と答えた。"""
        judge = _judge(claims_underground=(True, 0.9))
        finding = await SpeechCheck(judge).check(
            "地上へ出る道をつなげていくね", _activity(inside_home=True), place_only=True
        )
        assert finding.problems == ("地下にいる・まだ地上に出ていないと言っているが、実際はもう地上にいる",)

    @pytest.mark.asyncio
    async def test_saying_she_is_out_of_the_ground_underground_is_a_problem(self):
        judge = _judge(claims_on_surface=(True, 0.9))
        activity = replace(_activity(), observation=replace(_activity().observation, underground=True))
        finding = await SpeechCheck(judge).check("地上に出たよ", activity, place_only=True)
        assert finding.problems == ("地上に出たと言っているが、実際はまだ地下にいる",)

    @pytest.mark.asyncio
    async def test_a_judge_that_fails_or_is_late_means_not_checked(self):
        failing = AsyncMock()
        failing.ask.side_effect = ActionSelectionError("down")
        assert not (await SpeechCheck(failing).check("x", _activity())).checked

        async def slow(*args):
            await asyncio.sleep(1)

        late = AsyncMock()
        late.ask.side_effect = slow
        finding = await SpeechCheck(late, timeout_seconds=0.01).check("x", _activity())
        assert not finding.checked and finding.ok
