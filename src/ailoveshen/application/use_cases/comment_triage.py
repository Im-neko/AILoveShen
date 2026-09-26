"""
チャットのコメントを、返事を作る前に Jev が仕分ける（docs/design/34 §5）。

1 回の呼び出しで 2 つ聞く: 返事をするか（yes/no）と種類（頼み・助言・取り下げ・質問・雑談・返事の
要らないもの）。頼み・助言・取り下げは必ず返事（Gemini）に回す: 受ける・断る・考え直す・教訓・
取り下げは返事と同じ 1 回の生成で決める（設計書 12・13）。返事をしないのは、Jev が「要らない」かつ
雑談か返事の要らないものと、はっきり答えたときだけ。Jev が答えられなければ返事をする（前と同じ）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import Activity, ChatComment, FastQuestion, FastQuestionKind

KINDS = {
    "request": "asks the streamer to do or make something in the game",
    "advice": "suggests a better way, points something out, or corrects the streamer",
    "withdraw": "calls off something they asked for earlier",
    "question": "asks the streamer a question",
    "chat": "a greeting, a reaction, or small talk",
    "noise": "spam, a lone emote, or nothing a streamer would answer",
}
# 必ず返事（Gemini）に回す種類: 返事と同じ生成で頼みを受ける・考え直す・教訓にする・取り下げる
MUST_ANSWER = ("request", "advice", "withdraw")
# 急ぐ種類: 待っているコメントの先頭に
URGENT = ("withdraw", "request")
QUESTIONS = (
    FastQuestion(
        name="reply",
        kind=FastQuestionKind.YES_NO,
        instructions=(
            "Should the streamer answer this chat comment out loud? Answer no only for comments "
            "that need no answer at all."
        ),
    ),
    FastQuestion(
        name="kind",
        kind=FastQuestionKind.CHOICE,
        instructions="What kind of comment is this?",
        criteria=KINDS,
    ),
)


@dataclass(frozen=True)
class Triage:
    """仕分けの結果。`answer` が偽なら返事をしない。`source` は "jev" か "default"。"""

    answer: bool
    kind: str = "chat"
    confidence: float = 0.0
    source: str = "default"

    @property
    def urgent(self) -> bool:
        return self.kind in URGENT


class CommentTriage:
    """コメントを返事の前に Jev で仕分ける。"""

    def __init__(
        self,
        judge: IFastJudge,
        activity: Callable[[], Optional[Activity]] = lambda: None,
        skip_min_confidence: float = 0.7,
        timeout_seconds: float = 2.0,
        recorder: Optional[IWatchRecorder] = None,
    ) -> None:
        """
        Args:
            judge: Jev
            activity: 配信者が今していること（返事が要るかの手がかり）
            skip_min_confidence: 返事をしないのは、両方の答えの確信度がこれ以上のときだけ
            timeout_seconds: Jev の答えを待つ上限（過ぎたら返事をする）
            recorder: 仕分けの記録（返事をしなかったコメントは文ごと残す）
        """
        self._judge = judge
        self._activity = activity
        self._skip_min = skip_min_confidence
        self._timeout = timeout_seconds
        self._recorder = recorder

    async def triage(self, comment: ChatComment) -> Triage:
        state = self._state(comment)
        try:
            verdict = await asyncio.wait_for(self._judge.ask(state, QUESTIONS), timeout=self._timeout)
        except (AILoveShenError, TimeoutError) as e:
            logger.debug(f"コメントの仕分けは Jev なしで（{type(e).__name__}）: 返事をする")
            return self._record(comment, Triage(answer=True))
        reply = verdict.answers.get("reply")
        kind = verdict.answers.get("kind")
        kind_value = str(kind.value) if kind is not None and kind.value in KINDS else "chat"
        confidence = min(
            reply.confidence if reply is not None else 0.0,
            kind.confidence if kind is not None else 0.0,
        )
        skip = (
            reply is not None
            and reply.value is False
            and kind_value not in MUST_ANSWER
            and kind_value in ("chat", "noise")
            and confidence >= self._skip_min
        )
        return self._record(
            comment, Triage(answer=not skip, kind=kind_value, confidence=confidence, source="jev")
        )

    async def close(self) -> None:
        await self._judge.close()

    def _state(self, comment: ChatComment) -> dict[str, Any]:
        state: dict[str, Any] = {"viewer": comment.user_name, "comment": comment.message}
        activity = self._activity()
        if activity is not None and activity.goal is not None:
            state["streamer_is_doing"] = activity.goal.spec.describe()
            if activity.intent:
                state["trying_to"] = activity.intent
        return state

    def _record(self, comment: ChatComment, result: Triage) -> Triage:
        if self._recorder is not None:
            try:
                self._recorder.record(
                    {
                        "viewer": comment.user_name,
                        "comment": comment.message,
                        "answer": result.answer,
                        "kind": result.kind,
                        "confidence": round(result.confidence, 3),
                        "source": result.source,
                    }
                )
            except Exception as e:  # noqa: BLE001 - 記録の失敗で返事を止めない
                logger.warning(f"コメントの仕分けを記録できなかった: {e}")
        return result
