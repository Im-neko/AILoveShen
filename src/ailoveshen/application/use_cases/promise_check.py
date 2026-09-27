"""
返事が行動を約束しているのに何もしない扱いになっていないかを、Jev が確かめる
（docs/design/34 §10）。

返事と扱い（request / rethink）は 1 回の生成から出るが、「明るくしておくね」と言いながら
request は none で rethink もない、という返事が出る（教訓だけ書いて約束する）。扱いが
none / decline で rethink もないときだけ、返事の文が具体的なゲームの中の行動を約束しているかを
Jev に聞く。約束していれば、返事は書き直させる。Jev が答えられない・確かでないときは、
そのまま話す。
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import FastQuestion, FastQuestionKind

QUESTION = FastQuestion(
    name="promises",
    kind=FastQuestionKind.YES_NO,
    instructions=(
        "The streamer plays Minecraft and answered a viewer's chat comment with `reply`. Does the "
        "reply promise to do a concrete thing in the game (now or later), such as lighting a room "
        "up, placing torches, making or getting an item, building, going somewhere or fighting?"
    ),
    criteria={
        "yes": "it says the streamer will do such a thing (e.g. I'll light it up, I'll make "
        "one, I'll place torches in the room)",
        "no": "it only thanks, agrees in general, answers a question, says it will remember the "
        "advice or keep it in mind, says it already does it, or says it cannot or will not",
    },
)


class PromiseCheck:
    """返事が行動を約束しているかを Jev に聞く。"""

    def __init__(
        self,
        judge: IFastJudge,
        min_confidence: float = 0.6,
        timeout_seconds: float = 2.0,
        recorder: IWatchRecorder | None = None,
    ) -> None:
        """
        Args:
            judge: Jev
            min_confidence: 約束していると見なす確信度の下限（これ未満の「はい」はそのまま話す）
            timeout_seconds: Jev の答えを待つ上限（過ぎたらそのまま話す）
            recorder: 確かめた記録（返事の文ごと）
        """
        self._judge = judge
        self._min = min_confidence
        self._timeout = timeout_seconds
        self._recorder = recorder

    async def promises(self, comment: str, reply: str, handling: str) -> bool:
        """返事が行動を約束しているとき真（扱いが none / decline で rethink がない返事に使う）。"""
        state: dict[str, Any] = {"comment": comment, "reply": reply, "handling": handling}
        try:
            verdict = await asyncio.wait_for(
                self._judge.ask(state, (QUESTION,)), timeout=self._timeout
            )
        except (AILoveShenError, TimeoutError) as e:
            logger.debug(f"返事の約束は確かめずに話す（{type(e).__name__}）")
            self._record(state, None, 0.0)
            return False
        answer = verdict.answers.get(QUESTION.name)
        value = answer.value if answer is not None else None
        confidence = answer.confidence if answer is not None else 0.0
        self._record(state, value, confidence)
        return value is True and confidence >= self._min

    async def close(self) -> None:
        await self._judge.close()

    def _record(self, state: dict[str, Any], value: object, confidence: float) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record({**state, "promises": value, "confidence": round(confidence, 3)})
        except Exception as e:  # noqa: BLE001 - 記録の失敗で返事を止めない
            logger.warning(f"返事の約束の確かめを記録できなかった: {e}")
