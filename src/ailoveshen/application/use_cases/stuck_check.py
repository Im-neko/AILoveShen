"""
行動の記録から、行動が行き詰まっていないかを Jev に定期的に聞く（docs/design/34 §9）。

数の上の「進まない」（残りが減らない: stalled）とは別に、同じ行動の繰り返し、失敗の繰り返し、
行ったり来たり、忙しいのに目標が進まない、を行動の記録と位置から見る。Jev が行き詰まっていると
はっきり答えたら、今の小目標を終わらせて Gemini に考え直させる（理由は「is stuck」を含むので、
Gemini は行動の記録を見て原因を分析する: 27）。Jev が答えられない・迷うときは何もしない。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import FastQuestion, FastQuestionKind, GameObservation, Goal

PATTERNS = {
    "fine": "making progress, or doing varied work that fits the goal",
    "repeating": "doing the same action again and again without a new result",
    "failing": "the actions keep failing",
    "back_and_forth": "going back and forth between the same places",
    "no_progress": "busy, but the goal's progress does not move",
}
QUESTION = FastQuestion(
    name="stuck",
    kind=FastQuestionKind.CHOICE,
    instructions=(
        "Look at the streamer's recent actions for the current goal (oldest first, with results) "
        "and where they stood. Is the play stuck, and how? When minutes_without_progress is given, "
        "the goal's progress has not changed for that long: look for what went wrong."
    ),
    criteria=PATTERNS,
)
HISTORY = 12


class StuckCheck:
    """行動の記録から、行き詰まっていないかを数ステップごとに Jev に聞く。"""

    def __init__(
        self,
        judge: IFastJudge,
        every_steps: int = 6,
        min_confidence: float = 0.75,
        timeout_seconds: float = 3.0,
        recorder: Optional[IWatchRecorder] = None,
        quiet_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            judge: Jev
            every_steps: 今の小目標でこの数の行動ごとに聞く（最初の聞き取りもこの数の後）
            min_confidence: 行き詰まっているという答えがこれ以上のときだけ考え直させる
            timeout_seconds: Jev の答えを待つ上限（過ぎたら何もしない）
            recorder: 判断の記録
            quiet_seconds: 小目標の進み具合（残りと進捗の行）がこれだけ変わらなければ、数ステップを
                待たずに聞く（0: 時間では聞かない）。聞いたあとも変わらなければ、また同じだけたって聞く
            clock: 時計（テストで差し替える）
        """
        self._judge = judge
        self._every = max(1, every_steps)
        self._min_confidence = min_confidence
        self._timeout = timeout_seconds
        self._recorder = recorder
        self._steps: deque[dict[str, Any]] = deque(maxlen=HISTORY)
        self._since = 0
        self._quiet = quiet_seconds
        self._clock = clock
        self._progress: Any = None
        self._changed_at = clock()
        self._asked_at: Optional[float] = None

    def reset(self) -> None:
        """小目標が変わった（記録を消す）。"""
        self._steps.clear()
        self._since = 0
        self._progress = None
        self._changed_at = self._clock()
        self._asked_at = None

    def note(self, line: str, obs: GameObservation) -> None:
        """1 ステップの行動と結果、そのときいた位置を覚える。"""
        pos = (obs.state.get("self") or {}).get("position")
        self._steps.append({"action": line, **({"at": pos} if pos else {})})
        self._since += 1
        status = obs.goal
        progress = (status.remaining, tuple(status.lines)) if status else None
        if progress != self._progress:  # 進み具合が変わった: 変わらない時間の数え直し
            self._progress = progress
            self._changed_at = self._clock()
            self._asked_at = None

    async def check_if_due(self, goal: Goal, obs: GameObservation) -> Optional[str]:
        """聞く時なら Jev に聞き、行き詰まっていれば理由（考え直させる）。そうでなければ None。"""
        now = self._clock()
        still = now - self._changed_at
        long_quiet = (
            self._quiet > 0
            and still >= self._quiet
            and (self._asked_at is None or now - self._asked_at >= self._quiet)
        )
        if self._since < self._every and not long_quiet:
            return None
        self._since = 0
        if long_quiet:
            self._asked_at = now
        status = obs.goal
        state = {
            "goal": goal.describe(),
            "progress": list(status.lines) if status else [],
            "remaining": status.remaining if status else None,
            "recent_actions": list(self._steps),
            **({"minutes_without_progress": round(still / 60, 1)} if long_quiet else {}),
        }
        try:
            verdict = await asyncio.wait_for(self._judge.ask(state, (QUESTION,)), timeout=self._timeout)
        except (AILoveShenError, TimeoutError) as e:
            logger.debug(f"行き詰まりの確認は飛ばした（{type(e).__name__}）")
            return None
        answer = verdict.answers.get(QUESTION.name)
        pattern = str(answer.value) if answer is not None else "fine"
        stuck = (
            answer is not None
            and pattern in PATTERNS
            and pattern != "fine"
            and answer.confidence >= self._min_confidence
        )
        self._record(state, pattern, answer.confidence if answer else None, stuck)
        if not stuck:
            return None
        self.reset()
        return f"Jev judged from the action record that it is stuck ({pattern}: {PATTERNS[pattern]})"

    def _record(self, state: dict[str, Any], pattern: str, confidence: Optional[float], stuck: bool) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record(
                {
                    "goal": state["goal"],
                    "actions": len(state["recent_actions"]),
                    "pattern": pattern,
                    "confidence": round(confidence, 3) if confidence is not None else None,
                    "rethink": stuck,
                }
            )
        except Exception as e:  # noqa: BLE001 - 記録の失敗でプレイを止めない
            logger.warning(f"行き詰まりの確認を記録できなかった: {e}")
