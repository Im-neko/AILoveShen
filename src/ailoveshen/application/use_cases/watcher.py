"""
道具の実行を見張る（docs/design/21_tool_control.md §5）。

道具を実行している間、1 秒ごとにブリッジの共通の状態を読み、有効な質問をまとめて 1 回、高頻度の
判断モデル（Jev）に聞く。質問は 2 種類:

- コードの 1 問「この行動は進んでいるか」。規則に勝つまでは記録だけ（19 §13 の 4）
- 配信者（Gemini）が道具に添えた質問。「はい」が続けば、決まった 3 つのこと（止める・起こす・
  終わったかも）のどれかを起こす。Jev の答えが直接ワールドの行動を起こすことはなく、完了も判定しない
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import (
    FastQuestion,
    FastQuestionKind,
    ToolCall,
    ToolOutcome,
    WatchAction,
)

PROGRESS = "progressing"
PROGRESS_QUESTION = FastQuestion(
    name=PROGRESS,
    kind=FastQuestionKind.YES_NO,
    instructions=(
        "Is the current action (state.action) making progress? Look at how far it moved, "
        "the distance to its target, digging, items gained, path failures and position resets."
    ),
    criteria={
        "yes": "it moves closer, digs, gains items, or waits for a stated purpose",
        "no": "it stays in place, keeps being reset to the same position, finds no path, "
        "or nothing changes",
    },
)

# on_yes ごとの、結果に入れる言葉
REASONS = {
    WatchAction.STOP: "stopped by watch",
    WatchAction.WAKE: "woke up",
    WatchAction.MAYBE_DONE: "maybe done",
}


@dataclass(frozen=True)
class WatchPolicy:
    """見張りの既定値（19 §13 の 5。§6 の評価で調整する）。"""

    interval_seconds: float = 1.0
    grace_seconds: float = 2.0
    threshold: float = 0.7
    consecutive: int = 2
    act_on_progress: bool = False  # コードの 1 問で止める（規則に勝つまでは記録だけ）
    act_on_questions: bool = True  # 配信者の質問で止める・起こす


class ToolWatcher:
    """道具を 1 つ実行し、その間、質問で見張る。"""

    def __init__(
        self,
        bridge: IMinecraftBridge,
        judge: Optional[IFastJudge],
        recorder: Optional[IWatchRecorder] = None,
        policy: WatchPolicy = WatchPolicy(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            bridge: Minecraft ブリッジ（道具の実行、共通の状態、中断）
            judge: 高頻度の判断モデル。None なら見張らずに実行だけする
            recorder: ティックごとの記録を残す先
            policy: 間隔、しきい値など
            clock: 経過時間を測る時計（テストで差し替える）
        """
        self._bridge = bridge
        self._judge = judge
        self._recorder = recorder
        self._policy = policy
        self._clock = clock

    async def run(self, call: ToolCall) -> ToolOutcome:
        """道具を実行し、終わるまで見張る。見張りの失敗でプレイは止めない。"""
        task = asyncio.ensure_future(self._bridge.run_tool(call.name, call.args))
        stopped_by: Optional[str] = None
        if self._judge is not None:
            try:
                stopped_by = await self._watch(call, task)
            except AILoveShenError as e:
                logger.warning(f"見張りを続けられなかった（道具はそのまま続ける）: {e}")
        ok, result, seconds, refused = await task
        if stopped_by and not ok:
            logger.info(f"見張りが止めた: {call.describe()} -> {stopped_by}")
        return ToolOutcome(
            call=call,
            ok=ok,
            result=result,
            seconds=seconds,
            refused=refused,
            stopped_by=stopped_by if not ok else None,
        )

    async def _watch(self, call: ToolCall, task: asyncio.Future) -> Optional[str]:
        policy = self._policy
        started = self._clock()
        questions = [PROGRESS_QUESTION] + [
            FastQuestion(name=f"watch_{i}", kind=FastQuestionKind.YES_NO, instructions=w.question)
            for i, w in enumerate(call.watch)
        ]
        streaks = {q.name: 0 for q in questions}
        tick = 0
        while not task.done():
            await asyncio.wait({task}, timeout=policy.interval_seconds)
            if task.done():
                break
            if self._clock() - started < policy.grace_seconds:
                continue
            state = await self._bridge.state()
            if state.get("action") is None:  # まだ始まっていないか、もう終わった
                continue
            # 前の呼び出しが終わるまで次は聞かない（await するので重ならない）
            verdict = await self._judge.ask(state, questions)
            tick += 1
            fired: Optional[str] = None
            for q in questions:
                answer = verdict.answers.get(q.name)
                if answer is None:
                    streaks[q.name] = 0
                    continue
                # 進んでいるかは「いいえ」、配信者の質問は「はい」が合図
                signal = (answer.value is False) if q.name == PROGRESS else (answer.value is True)
                strong = signal and answer.confidence >= policy.threshold
                streaks[q.name] = streaks[q.name] + 1 if strong else 0
                if fired or streaks[q.name] < policy.consecutive:
                    continue
                if q.name == PROGRESS and policy.act_on_progress:
                    fired = f"stopped by watch: not making progress ({answer.confidence:.2f})"
                elif q.name != PROGRESS and policy.act_on_questions:
                    w = call.watch[int(q.name.split("_")[1])]
                    fired = f"{REASONS[w.on_yes]}: {w.question} ({answer.confidence:.2f})"
            self._record(call, tick, state, verdict, streaks, fired)
            if fired:
                aborted = await self._bridge.abort(fired)
                if aborted:
                    return fired
        return None

    def _record(self, call, tick, state, verdict, streaks, fired) -> None:
        if self._recorder is None:
            return
        self._recorder.record(
            {
                "tool": call.describe(),
                "intent": call.intent,
                "tick": tick,
                "state": state,
                "questions": {
                    PROGRESS: PROGRESS_QUESTION.instructions,
                    **{f"watch_{i}": w.question for i, w in enumerate(call.watch)},
                },
                "answers": {
                    name: {"value": a.value, "confidence": round(a.confidence, 3)}
                    for name, a in verdict.answers.items()
                },
                "streaks": dict(streaks),
                "fired": fired,
                "elapsed_ms": verdict.elapsed_ms,
                "input_tokens": verdict.input_tokens,
            }
        )
