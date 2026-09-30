"""SpeechGuard（読む前の照合、作り直し、言わない。設計書 65 §4）のテスト。"""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.application.use_cases.speech_check import SpeechFinding
from ailoveshen.domain.value_objects import Activity
from ailoveshen.presentation.services.speech_guard import SpeechGuard
from ailoveshen.presentation.services.spoken_log import SpokenLog

BAD = SpeechFinding(
    problems=("家の中にいる・入ったと言っているが、実際は家の外（家まで 30m）",),
    checked=True,
    answers={"claims_inside_home": [True, 0.9]},
)
GOOD = SpeechFinding(checked=True, answers={"claims_inside_home": [False, 0.9]})


class _Recorder(IWatchRecorder):
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, entry: dict) -> None:
        self.entries.append(entry)


def _guard(*findings: SpeechFinding, recorder: _Recorder | None = None) -> SpeechGuard:
    check = AsyncMock()
    check.check.side_effect = list(findings)
    log = SpokenLog(recorder, lambda: None) if recorder is not None else None
    return SpeechGuard(check, lambda: Activity(), log)


@pytest.mark.asyncio
async def test_a_line_that_fits_the_state_is_read_as_is():
    regenerate = AsyncMock()
    guard = _guard(GOOD)

    assert await guard.verify("松明を作るね", regenerate) == "松明を作るね"
    regenerate.assert_not_called()


@pytest.mark.asyncio
async def test_a_mismatch_is_written_again_once_with_the_reason():
    regenerate = AsyncMock(return_value="まだ家の外だよ")
    recorder = _Recorder()
    guard = _guard(BAD, GOOD, recorder=recorder)

    assert await guard.verify("家に入れたよ", regenerate) == "まだ家の外だよ"

    (extra,) = regenerate.await_args.args
    assert len(extra) == 1
    assert "前の案「家に入れたよ」は今の状態と食い違っている" in extra[0]
    assert "実際は家の外（家まで 30m）" in extra[0]
    assert [(e["verdict"], e["attempt"]) for e in recorder.entries] == [("rewrite", 1), ("ok", 2)]


@pytest.mark.asyncio
async def test_a_line_that_still_mismatches_is_not_said():
    regenerate = AsyncMock(return_value="やっと家に入れたよ")
    recorder = _Recorder()
    guard = _guard(BAD, BAD, recorder=recorder)

    assert await guard.verify("家に入れたよ", regenerate) is None

    regenerate.assert_awaited_once()
    assert [e["verdict"] for e in recorder.entries] == ["rewrite", "dropped"]


@pytest.mark.asyncio
async def test_an_empty_rewrite_means_nothing_is_said():
    guard = _guard(BAD)

    assert await guard.verify("家に入れたよ", AsyncMock(return_value="")) is None


@pytest.mark.asyncio
async def test_a_line_that_could_not_be_checked_is_read_and_recorded_as_unchecked():
    recorder = _Recorder()
    guard = _guard(SpeechFinding(), recorder=recorder)

    assert await guard.verify("松明を作るね", AsyncMock()) == "松明を作るね"
    assert recorder.entries[0]["verdict"] == "unchecked"
