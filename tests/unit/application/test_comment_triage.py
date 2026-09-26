"""CommentTriage（返事の前にコメントを Jev が仕分ける、docs/design/34 §5）のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.comment_triage import CommentTriage
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import ChatComment, FastAnswer, FastVerdict


def _verdict(reply, kind, confidence=0.9):
    return FastVerdict(
        answers={
            "reply": FastAnswer("reply", reply, confidence),
            "kind": FastAnswer("kind", kind, confidence),
        },
        elapsed_ms=40,
    )


def _triage(verdict=None, error=None):
    judge = AsyncMock()
    if error:
        judge.ask.side_effect = error
    else:
        judge.ask.return_value = verdict
    recorder = Mock()
    return CommentTriage(judge, recorder=recorder), recorder


C = ChatComment("まめ", "こんにちは")


@pytest.mark.asyncio
async def test_small_talk_that_needs_no_reply_is_skipped_and_recorded():
    triage, recorder = _triage(_verdict(False, "noise"))
    result = await triage.triage(C)
    assert not result.answer and result.kind == "noise"
    assert recorder.record.call_args.args[0]["comment"] == "こんにちは"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["request", "advice", "withdraw", "question"])
async def test_requests_advice_withdrawals_and_questions_always_get_a_reply(kind):
    triage, _ = _triage(_verdict(False, kind))
    assert (await triage.triage(C)).answer


@pytest.mark.asyncio
async def test_an_unsure_or_failing_jev_means_a_reply():
    triage, _ = _triage(_verdict(False, "chat", confidence=0.5))
    assert (await triage.triage(C)).answer
    triage, _ = _triage(error=ActionSelectionError("down"))
    result = await triage.triage(C)
    assert result.answer and result.source == "default"


@pytest.mark.asyncio
async def test_withdrawals_and_requests_are_urgent():
    triage, _ = _triage(_verdict(True, "withdraw"))
    assert (await triage.triage(C)).urgent
    triage, _ = _triage(_verdict(True, "chat"))
    assert not (await triage.triage(C)).urgent
