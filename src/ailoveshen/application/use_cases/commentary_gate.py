"""
目標の切れ目の実況を、今話す価値があるかを Jev に聞く（docs/design/34 §6）。

見どころ（中目標の完了・やめた、家や街、技を覚えた）と、しばらく黙っているときは聞かずに話す
（呼び出し側が決める）。Jev が答えられない・迷うときは話す（前と同じ）。Jev は実況の文を見ない。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import Activity, FastQuestion, FastQuestionKind

QUESTION = FastQuestion(
    name="worth_speaking",
    kind=FastQuestionKind.YES_NO,
    instructions=(
        "A game streamer narrates what they are doing. Given what just happened (events) and "
        "how long they have been quiet, is this worth saying out loud now? Say no for routine "
        "continuation of the same work, yes for a new direction, a problem, something found or "
        "something viewers would want explained."
    ),
)


class CommentaryGate:
    """実況の前に、今話す価値があるかを Jev に聞く。"""

    def __init__(
        self,
        judge: IFastJudge,
        activity: Callable[[], Optional[Activity]] = lambda: None,
        min_confidence: float = 0.6,
        timeout_seconds: float = 1.5,
        recorder: Optional[IWatchRecorder] = None,
    ) -> None:
        """
        Args:
            judge: Jev
            activity: 配信者が今していること
            min_confidence: 「話さない」はこの確信度以上のときだけ（迷えば話す）
            timeout_seconds: Jev の答えを待つ上限（過ぎたら話す）
            recorder: 判断の記録
        """
        self._judge = judge
        self._activity = activity
        self._min_confidence = min_confidence
        self._timeout = timeout_seconds
        self._recorder = recorder

    async def worth_speaking(self, events: list[str], quiet_seconds: float) -> bool:
        state: dict[str, Any] = {"events": events, "quiet_for_seconds": round(quiet_seconds)}
        activity = self._activity()
        if activity is not None and activity.goal is not None:
            state["current_goal"] = activity.goal.spec.describe()
        try:
            verdict = await asyncio.wait_for(self._judge.ask(state, (QUESTION,)), timeout=self._timeout)
        except (AILoveShenError, TimeoutError) as e:
            logger.debug(f"実況の間合いは Jev なしで（{type(e).__name__}）: 話す")
            return self._record(state, True, None)
        answer = verdict.answers.get(QUESTION.name)
        if answer is None:
            return self._record(state, True, None)
        speak = not (answer.value is False and answer.confidence >= self._min_confidence)
        return self._record(state, speak, answer.confidence)

    async def close(self) -> None:
        await self._judge.close()

    def _record(self, state: dict[str, Any], speak: bool, confidence: Optional[float]) -> bool:
        if self._recorder is not None:
            try:
                self._recorder.record(
                    {
                        **state,
                        "speak": speak,
                        "confidence": round(confidence, 3) if confidence is not None else None,
                    }
                )
            except Exception as e:  # noqa: BLE001 - 記録の失敗で実況を止めない
                logger.warning(f"実況の間合いを記録できなかった: {e}")
        return speak
