"""
読み上げた文と、そのときの状態の記録（docs/design/65 §2）。

視聴者が聞いたことの記録がなかった: `SpokenLines` は読み上げの前に覚えるだけで、`TTSService` は
待って古くなった実況を捨てる。ここに 1 行 1 件で残す（`event` で分ける）:
- `line`: 話そうとした文と、そのときの状態（`outcome`: said / repeat: 直前と同じで言わない）
- `check`: 照合の結果（`verdict`: ok / rewrite: 食い違うので作り直す / dropped: 言わない / unchecked）
- `tts`: 読み上げの結果（played / failed / dropped_old / dropped_full）
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from ailoveshen.application.dto.speech_dto import SpeakTextRequest
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.application.use_cases.speech_check import SpeechFinding, speech_state
from ailoveshen.domain.value_objects import Activity


class SpokenLog:
    """読み上げの記録。"""

    def __init__(
        self, recorder: IWatchRecorder, activity: Callable[[], Optional[Activity]]
    ) -> None:
        self._recorder = recorder
        self._activity = activity

    def line(self, text: str, source: str, outcome: str = "said") -> None:
        """話そうとした文（言ったか、直前と同じで言わなかったか）と、今の状態。"""
        self._recorder.record(
            {
                "event": "line",
                "text": text,
                "source": source,
                "outcome": outcome,
                "state": speech_state(self._activity()),
            }
        )

    def check(self, text: str, finding: SpeechFinding, verdict: str, attempt: int) -> None:
        """照合の結果（Jev の答えと、食い違い）。"""
        self._recorder.record(
            {
                "event": "check",
                "text": text,
                "verdict": verdict,
                "attempt": attempt,
                "checked": finding.checked,
                "problems": list(finding.problems),
                "answers": finding.answers,
                "state": speech_state(self._activity()),
            }
        )

    def tts(self, request: SpeakTextRequest, outcome: str, waited: float) -> None:
        """読み上げの結果（TTSService の on_outcome）。"""
        self._recorder.record(
            {
                "event": "tts",
                "text": request.text,
                "source": request.source,
                "outcome": outcome,
                "waited": round(waited, 1),
            }
        )
