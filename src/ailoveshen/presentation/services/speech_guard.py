"""
読み上げる前の実況・一言を今の状態と照らし、食い違えば 1 回だけ作り直し、それでも食い違えば言わない
（docs/design/65 §4）。Narrator と LiveNarration が使う。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

from loguru import logger

from ailoveshen.application.use_cases.speech_check import SpeechCheck, SpeechFinding
from ailoveshen.domain.value_objects import Activity
from ailoveshen.presentation.services.spoken_log import SpokenLog

Regenerate = Callable[[list[str]], Awaitable[str]]


class SpeechGuard:
    """文を読む直前に照らして、食い違うことを言わせない。"""

    def __init__(
        self,
        check: SpeechCheck,
        activity: Callable[[], Optional[Activity]],
        log: Optional[SpokenLog] = None,
    ) -> None:
        """
        Args:
            check: 文を今の状態と照らす
            activity: 配信者が今していること。作った時点ではなく、読む直前のものと照らす
            log: 照合の記録
        """
        self._check = check
        self._activity = activity
        self._log = log

    async def verify(self, text: str, regenerate: Regenerate) -> Optional[str]:
        """
        読んでよい文を返す（言わないなら None）。

        食い違えば、理由を出来事として渡して作り直し（1 回だけ。`regenerate(extra)` が新しい文を返す）、
        作り直した文も食い違えば言わない。照らせなかったとき（Jev が答えない）は、そのまま読む。
        """
        for attempt in (1, 2):
            finding = await self._check.check(text, self._activity())
            if finding.ok:
                self._record(text, finding, "ok" if finding.checked else "unchecked", attempt)
                return text
            if attempt == 2:
                logger.info(f"[say] 今の状態と食い違うので言わない: {text}（{'；'.join(finding.problems)}）")
                self._record(text, finding, "dropped", attempt)
                return None
            self._record(text, finding, "rewrite", attempt)
            logger.info(f"[say] 今の状態と食い違うので作り直す: {text}（{'；'.join(finding.problems)}）")
            text = await regenerate(
                [
                    f"前の案「{text}」は今の状態と食い違っている: {'；'.join(finding.problems)}。"
                    "今の状態に合わせて言い直す"
                ]
            )
            if not text:
                return None
        return None  # pragma: no cover - ループは必ず return する

    def _record(self, text: str, finding: SpeechFinding, verdict: str, attempt: int) -> None:
        if self._log is not None:
            self._log.check(text, finding, verdict, attempt)
