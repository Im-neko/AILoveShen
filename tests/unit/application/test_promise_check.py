"""PromiseCheck（返事が行動を約束しているかを Jev が確かめる、docs/design/34 §10）のテスト。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.promise_check import PromiseCheck
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import FastAnswer, FastVerdict


def _check(value=None, confidence=0.9, error=None):
    judge = AsyncMock()
    if error:
        judge.ask.side_effect = error
    else:
        judge.ask.return_value = FastVerdict(
            answers={"promises": FastAnswer("promises", value, confidence)}, elapsed_ms=40
        )
    recorder = Mock()
    return PromiseCheck(judge, recorder=recorder, timeout_seconds=0.1), judge, recorder


@pytest.mark.asyncio
async def test_a_sure_yes_is_a_promise_and_is_recorded_with_the_reply():
    check, judge, recorder = _check(True)
    assert await check.promises("明るくしとこうね", "明るくしておくね！", "none")
    assert judge.ask.call_args.args[0] == {
        "comment": "明るくしとこうね",
        "reply": "明るくしておくね！",
        "handling": "none",
    }
    assert recorder.record.call_args.args[0]["promises"] is True


@pytest.mark.asyncio
async def test_no_or_an_unsure_yes_is_spoken_as_is():
    check, _, _ = _check(False)
    assert not await check.promises("c", "覚えておくね", "none")
    check, _, _ = _check(True, confidence=0.4)
    assert not await check.promises("c", "やるね", "none")


@pytest.mark.asyncio
async def test_a_failing_or_slow_jev_is_spoken_as_is():
    check, _, recorder = _check(error=ActionSelectionError("down"))
    assert not await check.promises("c", "やるね", "none")
    assert recorder.record.call_args.args[0]["promises"] is None

    async def slow(*_):
        await asyncio.sleep(1)

    check, judge, _ = _check(True)
    judge.ask.side_effect = slow
    assert not await check.promises("c", "やるね", "none")
