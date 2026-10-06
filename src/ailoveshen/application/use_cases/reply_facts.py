"""
返事が、持ち物とチェストの事実と食い違っていないかを、Jev が確かめる（docs/design/34 §10 と同じ形）。

視聴者の「鉄は集まった？」に「鉄はまだ見つからなくて」と答えたが、鉄 2 個は預けたばかりでチェストに
あった（2026-10-01）。持ち物とチェストの中身は返事のプロンプトにあるのに、モデルが読み違えた。
返事の文が「何かを持っていない・見つからない」と言っているかだけを Jev に答えさせ、持っているかは
ブリッジが知っている事実（持ち物とチェスト）で決める。Jev が答えられないときはそのまま話す。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import Activity, FastQuestion, FastQuestionKind

HOLDINGS_SHOWN = 24  # Jev に見せる品の数の上限（多い順）

DENIES_HELD = FastQuestion(
    name="denies_held",
    kind=FastQuestionKind.YES_NO,
    instructions=(
        "A Minecraft streamer answered a viewer with `reply`. `holdings` lists what she carries and what "
        "is in her chests (item ids with counts; raw_iron is iron, oak_log is wood, and so on). Does the "
        "reply say that she has not found, does not have, or has none of an item that `holdings` shows "
        "she has? Saying she has too few of it, or needs more, is not denying."
    ),
    criteria={
        "yes": "The reply flatly says she has not found / does not have / has no X, and X is in holdings.",
        "no": "The reply says nothing about holding such items, says she has some or has too few, or "
        "talks about an item that is not in holdings.",
    },
)


def holdings(activity: Optional[Activity]) -> dict[str, dict[str, int]]:
    """持ち物とチェストの中身（品名 → 数。空なら {}）。ブリッジの `state` から。"""
    obs = activity.observation if activity is not None else None
    if obs is None:
        return {}
    state = obs.state or {}
    carried = {k: int(v) for k, v in (state.get("inventory") or {}).items() if v}
    chests: dict[str, int] = {}
    for chest in (state.get("memory") or {}).get("chests", []):
        for name, n in (chest.get("contents") or {}).items():
            chests[name] = chests.get(name, 0) + int(n)

    def top(items: dict[str, int]) -> dict[str, int]:
        return dict(sorted(items.items(), key=lambda kv: -kv[1])[:HOLDINGS_SHOWN])

    out = {"carried": top(carried), "in_chests": top(chests)}
    return {k: v for k, v in out.items() if v}


class ReplyFactCheck:
    """返事が「ない・見つからない」と言う物を、実際は持っていないかを Jev に聞く。"""

    def __init__(
        self,
        judge: IFastJudge,
        min_confidence: float = 0.75,
        timeout_seconds: float = 2.0,
        recorder: IWatchRecorder | None = None,
    ) -> None:
        """
        Args:
            judge: Jev
            min_confidence: 食い違うと言うのは、はいの確信度がこれ以上のときだけ
            timeout_seconds: Jev の答えを待つ上限（過ぎたらそのまま話す）
            recorder: 確かめた記録（返事の文ごと）
        """
        self._judge = judge
        self._min = min_confidence
        self._timeout = timeout_seconds
        self._recorder = recorder

    async def problem(self, reply: str, activity: Optional[Activity]) -> str:
        """食い違いがあれば、書き直させる理由（なければ空）。"""
        held = holdings(activity)
        if not held or not reply:
            return ""
        state: dict[str, Any] = {"reply": reply, "holdings": held}
        try:
            verdict = await asyncio.wait_for(
                self._judge.ask(state, (DENIES_HELD,)), timeout=self._timeout
            )
        except (AILoveShenError, TimeoutError) as e:
            logger.debug(f"返事の事実は確かめずに話す（{type(e).__name__}）")
            return ""
        answer = verdict.answers.get(DENIES_HELD.name)
        value = answer.value if answer is not None else None
        confidence = float(answer.confidence) if answer is not None else 0.0
        self._record(state, value, confidence)
        if value is not True or confidence < self._min:
            return ""
        facts = "、".join(
            f"{where}: " + "、".join(f"{n} {c}" for n, c in items.items())
            for where, items in (
                ("持ち物", held.get("carried", {})),
                ("チェスト", held.get("in_chests", {})),
            )
            if items
        )
        return (
            f"返事「{reply}」は、持っている物を「ない・見つからない」と言っている。事実は {facts}。"
            "事実に合わせて言い直す（持っているものは持っていると言い、足りないなら足りないと言う）。"
        )

    async def close(self) -> None:
        await self._judge.close()

    def _record(self, state: dict[str, Any], value: object, confidence: float) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record({**state, "denies_held": value, "confidence": round(confidence, 3)})
        except Exception as e:  # noqa: BLE001 - 記録の失敗で返事を止めない
            logger.warning(f"返事の事実の確かめを記録できなかった: {e}")
