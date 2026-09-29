"""
状況が変わらない時間を数える（docs/design/51）。

失敗が続いていなくても、行動が成功して何も変わらないことがある（すでにいる場所への goto を 3 回、
持っていない物の板材へのクラフトなど）。位置・持ち物・体力・満腹度・時間帯・小目標の進み具合が
`seconds` 変わらず、その間に 2 手以上していれば「動かない」とみなし、Jev に任せず LLM に、その間の
手と結果を見せて別の方法を探させる。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

from ailoveshen.domain.value_objects import GameObservation

# 待つ・寝る手は、何も変わらないのがふつう（動かないと数えない）
WAITING = ("wait", "sleep")
MIN_ACTIONS = 2  # これ以上の手をしても変わらなかったときだけ（1 手を長く待つのは動かないではない）
KEPT_ACTIONS = 6


class Stagnation:
    """位置・持ち物・体力・時間帯・小目標の進み具合が変わらない時間と、その間の手。"""

    def __init__(
        self, seconds: float = 10.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._seconds = seconds
        self._clock = clock
        self._signature: Any = None
        self._changed_at = clock()
        self._actions: deque[str] = deque(maxlen=KEPT_ACTIONS)
        self._count = 0

    def reset(self) -> None:
        """小目標が変わった。"""
        self._signature = None
        self._changed_at = self._clock()
        self._actions.clear()
        self._count = 0

    def observe(self, state: dict[str, Any], obs: GameObservation) -> float:
        """今の状況を見る。動かない時間（秒）を返す（動いていれば、または手が足りなければ 0）。"""
        me = state.get("self") or {}
        pos = me.get("position") or {}
        status = obs.goal
        signature = (
            tuple(round(float(pos.get(k, 0))) for k in "xyz"),
            tuple(sorted((state.get("inventory") or {}).items())),
            round(float(me.get("health", 0))),
            me.get("food"),
            obs.time_phase,
            (status.remaining, tuple(status.lines)) if status else None,
        )
        now = self._clock()
        if signature != self._signature:
            self._signature = signature
            self._changed_at = now
            self._actions.clear()
            self._count = 0
            return 0.0
        still = now - self._changed_at
        return still if self._seconds > 0 and still >= self._seconds and self._count >= MIN_ACTIONS else 0.0

    def record(self, line: str) -> None:
        """変わらない間にした手と結果（待つ・寝る手は数えない）。"""
        if line.lower().startswith(WAITING):
            return
        self._actions.append(line)
        self._count += 1

    def actions(self) -> list[str]:
        return list(self._actions)
