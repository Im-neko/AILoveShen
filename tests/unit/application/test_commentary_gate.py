"""CommentaryGate（実況の間合い、docs/design/34 §6）のテスト。"""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.application.use_cases.commentary_gate import CommentaryGate
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import FastAnswer, FastVerdict


def _gate(value=None, confidence=0.9, error=None):
    judge = AsyncMock()
    if error:
        judge.ask.side_effect = error
    else:
        judge.ask.return_value = FastVerdict(
            answers={"worth_speaking": FastAnswer("worth_speaking", value, confidence)}, elapsed_ms=30
        )
    return CommentaryGate(judge)


@pytest.mark.asyncio
async def test_a_sure_no_keeps_quiet_otherwise_speak():
    assert not await _gate(False).worth_speaking(["新しい小目標"], 12)
    assert await _gate(True).worth_speaking(["新しい小目標"], 12)
    assert await _gate(False, confidence=0.4).worth_speaking(["新しい小目標"], 12)  # 迷えば話す
    assert await _gate(error=ActionSelectionError("down")).worth_speaking(["x"], 12)
