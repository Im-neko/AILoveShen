"""
同じ手の並びの繰り返しを見つける（技にする機会。docs/design/22 §13）。

畑を耕して種をまく（till → sow）を何十回も、1 手ごとに選んで実行するのは遅い。同じ並びを続けて
何度も成功させたときは、その並びを配信者（LLM）に見せて、技にするか（write_skill）、すでにある技を
呼ぶか（run_skill）を選ばせる。ここは見つけるだけで、技を書くかどうかは LLM が決める。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

# 手の並びに数えない手: 道具は till / sow 自身が持つ。待つのは繰り返しの一部ではない
IGNORED = ("equip", "wait", "sleep")
MAX_PERIOD = 3  # 並びの長さ（till → sow は 2）
CYCLES = 3  # 2 手以上の並びを、この回数続けて成功したら繰り返し
SINGLE_REPEATS = 6  # 1 手だけの並びは、より多く続けたときだけ（木を 3 本切るのはふつう）
KEPT = 24
NUDGES = 2  # 同じ並びを見せる回数（無視されても、何度も見せない）
COOLDOWN_SECONDS = 1800.0


@dataclass(frozen=True)
class Repeat:
    """続けて成功した同じ手の並び。"""

    steps: tuple[str, ...]
    times: int

    def describe(self) -> str:
        return " → ".join(self.steps)


class Repetition:
    """今の小目標で成功した手（動詞）を覚え、同じ並びが続いたら知らせる。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._verbs: deque[str] = deque(maxlen=KEPT)
        self._shown: dict[tuple[str, ...], tuple[int, float]] = {}

    def reset(self) -> None:
        """小目標が変わった。"""
        self._verbs.clear()

    def record(self, verb: str | None, ok: bool) -> None:
        """1 手の結果。失敗は繰り返しを切る。動詞のない手（技、調べもの）と IGNORED は数えない。"""
        if verb is None or verb in IGNORED:
            return
        if not ok:
            self._verbs.clear()
            return
        self._verbs.append(verb)

    def find(self) -> Optional[Repeat]:
        """続いている繰り返し（なければ None）。同じ並びは NUDGES 回まで、COOLDOWN_SECONDS の間。"""
        found = self._tail()
        if found is None:
            return None
        now = self._clock()
        count, since = self._shown.get(found.steps, (0, now))
        if now - since > COOLDOWN_SECONDS:
            count, since = 0, now
        if count >= NUDGES:
            return None
        self._shown[found.steps] = (count + 1, since)
        return found

    def _tail(self) -> Optional[Repeat]:
        verbs = list(self._verbs)
        for period in range(1, MAX_PERIOD + 1):
            need = SINGLE_REPEATS if period == 1 else CYCLES
            if len(verbs) < period * need:
                continue
            unit = verbs[-period:]
            if period > 1 and len(set(unit)) == 1:
                continue  # 同じ 1 手の繰り返し（period 1 で見る）
            times = 0
            while len(verbs) >= (times + 1) * period and verbs[
                len(verbs) - (times + 1) * period : len(verbs) - times * period
            ] == unit:
                times += 1
            if times >= need:
                return Repeat(tuple(unit), times)
        return None
