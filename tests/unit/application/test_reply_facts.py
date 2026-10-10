"""ReplyFactCheck（返事が持っている物を「ない」と言っていないかを Jev が確かめる）のテスト。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.reply_facts import ReplyFactCheck, holdings
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import (
    Activity,
    Candidate,
    FastAnswer,
    FastVerdict,
    GameObservation,
)

STATE = {
    "inventory": {"stone_pickaxe": 1, "coal": 9},
    "memory": {"chests": [{"contents": {"raw_iron": 2, "cobblestone": 3}}, {"contents": {}}]},
}


def _activity(state=STATE):
    obs = GameObservation(
        state=state, candidates=(Candidate("wait", {"verb": "wait"}),), health=20.0, food=20
    )
    activity = Mock(spec=Activity)
    activity.observation = obs
    return activity


def _check(value=None, confidence=0.9, error=None):
    judge = AsyncMock()
    if error:
        judge.ask.side_effect = error
    else:
        judge.ask.return_value = FastVerdict(
            answers={"denies_held": FastAnswer("denies_held", value, confidence)}, elapsed_ms=40
        )
    recorder = Mock()
    return ReplyFactCheck(judge, recorder=recorder, timeout_seconds=0.1), judge, recorder


def test_holdings_are_what_is_carried_and_what_the_chests_hold():
    assert holdings(_activity()) == {
        "carried": {"coal": 9, "stone_pickaxe": 1},
        "in_chests": {"raw_iron": 2, "cobblestone": 3},
    }
    assert holdings(None) == {}
    assert holdings(_activity({})) == {}


@pytest.mark.asyncio
async def test_a_sure_denial_of_something_held_comes_back_with_the_facts():
    """実例（2026-10-01）: 「鉄は見つからなくて」と言ったが、鉄 2 個はチェストにあった。"""
    check, judge, recorder = _check(True)
    problem = await check.problem("鉄はまだ見つからなくて、食料が先なんだよね", _activity())
    assert "raw_iron" in problem and "見つからない" in problem
    assert judge.ask.call_args.args[0]["reply"].startswith("鉄は")
    assert recorder.record.call_args.args[0]["denies_held"] is True


@pytest.mark.asyncio
async def test_no_or_an_unsure_yes_or_nothing_held_is_spoken_as_is():
    check, _, _ = _check(False)
    assert await check.problem("鉄はあと 1 個！", _activity()) == ""
    check, _, _ = _check(True, confidence=0.4)
    assert await check.problem("鉄はない", _activity()) == ""
    check, judge, _ = _check(True)
    assert await check.problem("鉄はない", _activity({})) == ""
    judge.ask.assert_not_called()  # 持ち物の事実がなければ聞かない


@pytest.mark.asyncio
async def test_a_failing_or_slow_jev_is_spoken_as_is():
    check, _, recorder = _check(error=ActionSelectionError("down"))
    assert await check.problem("鉄はない", _activity()) == ""
    recorder.record.assert_not_called()

    async def slow(*_):
        await asyncio.sleep(1)

    check, judge, _ = _check(True)
    judge.ask.side_effect = slow
    assert await check.problem("鉄はない", _activity()) == ""


@pytest.mark.asyncio
async def test_a_reply_that_puts_her_underground_when_she_is_home_comes_back_with_the_place():
    """実測（2026-10-10 19:32）: 家の中にいるのに「地上へ出る道をつなげていくね」と答えた。"""
    from ailoveshen.application.use_cases.speech_check import SpeechCheck

    judge = AsyncMock()
    judge.ask.return_value = FastVerdict(
        answers={"claims_underground": FastAnswer("claims_underground", True, 0.9)}, elapsed_ms=30
    )
    obs = GameObservation(
        state={}, candidates=(), health=20.0, food=20, has_home=True, inside_home=True
    )
    check = ReplyFactCheck(AsyncMock(), place=SpeechCheck(judge))

    problem = await check.problem(
        "地上へ出る道をつなげていくね", Activity(observation=obs)
    )

    assert "地下にいる・まだ地上に出ていないと言っているが、実際はもう地上にいる" in problem
    assert "事実に合わせて言い直す" in problem
    names = [q.name for q in judge.ask.call_args.args[1]]
    assert "contradicts_plan" not in names  # 返事は次の予定を答えてよい


@pytest.mark.asyncio
async def test_a_reply_that_fits_the_place_is_spoken_as_is():
    from ailoveshen.application.use_cases.speech_check import SpeechCheck

    judge = AsyncMock()
    judge.ask.return_value = FastVerdict(
        answers={"claims_underground": FastAnswer("claims_underground", False, 0.9)}, elapsed_ms=30
    )
    obs = GameObservation(state={}, candidates=(), health=20.0, food=20, has_home=True)
    check = ReplyFactCheck(AsyncMock(), place=SpeechCheck(judge))

    assert await check.problem("朝になったら石を掘るよ", Activity(observation=obs)) == ""
