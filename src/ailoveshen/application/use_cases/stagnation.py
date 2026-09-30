"""
状況が変わらない時間を数える（docs/design/51）。

失敗が続いていなくても、行動が成功して何も変わらないことがある（すでにいる場所への goto を 3 回、
持っていない物の板材へのクラフトなど）。位置・持ち物・体力・満腹度・時間帯・小目標の進み具合が
変わらないまま、手の実行に `seconds` かかり、その間に 2 手以上していれば「動かない」とみなし、Jev
に任せず LLM に、その間の手と結果を見せて別の方法を探させる。

数えるのは手の実行にかかった時間だけで、LLM や Jev が手を選ぶ時間は数えない。選ぶのに数秒かかる
ので、時計で数えると、耕す→種をまくのような正しい手の途中でも「動かない」になり、思考つきの選び直し
（数十秒）を呼んで、さらに遅くなる。
"""

from __future__ import annotations

from collections import deque
from typing import Any

from ailoveshen.domain.value_objects import GameObservation

# 待つ・寝る手は、何も変わらないのがふつう（動かないと数えない）。手の動詞（候補の verb / 道具名）で
# 見分ける: 記録する行はソルバーの提案だと「do_suggestion(id=wait) …」で始まり、行の頭では分からない
# （2026-09-30: Jev が選んだ待ちが数えられ、夜に 3 回待つたびに tool_stuck が数分考えた）
WAITING_VERBS = frozenset({"wait", "sleep", "sleep_at", "bed_here"})
WAITING = ("wait", "sleep")
MIN_ACTIONS = 2  # これ以上の手をしても変わらなかったときだけ（1 手を長く待つのは動かないではない）
KEPT_ACTIONS = 6


class Stagnation:
    """位置・持ち物・体力・時間帯・小目標の進み具合が変わらない時間と、その間の手。"""

    def __init__(self, seconds: float = 10.0) -> None:
        self._seconds = seconds
        self._signature: Any = None
        self._acted = 0.0  # 変わらない間にした手の実行時間（秒）
        self._actions: deque[str] = deque(maxlen=KEPT_ACTIONS)
        self._count = 0

    def reset(self) -> None:
        """小目標が変わった。"""
        self._signature = None
        self._acted = 0.0
        self._actions.clear()
        self._count = 0

    def observe(self, state: dict[str, Any], obs: GameObservation) -> float:
        """今の状況を見る。動かない間の手の実行時間（秒）を返す（動いていれば、または足りなければ 0）。"""
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
        if signature != self._signature:
            self._signature = signature
            self._acted = 0.0
            self._actions.clear()
            self._count = 0
            return 0.0
        still = self._acted
        return still if self._seconds > 0 and still >= self._seconds and self._count >= MIN_ACTIONS else 0.0

    def record(self, line: str, seconds: float, waiting: bool = False) -> None:
        """変わらない間にした手と結果、実行にかかった秒（待つ・寝る手は数えない）。"""
        if waiting or line.lower().startswith(WAITING):
            return
        self._actions.append(line)
        self._count += 1
        self._acted += seconds

    def actions(self) -> list[str]:
        return list(self._actions)
