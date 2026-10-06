"""
同じ理由で失敗し続けているのを見つける（2026-10-03）。

`tool_stuck`（2 回続けて失敗したら、同じ道具・同じ引数を繰り返さないよう言う）は、引数の文字列が違えば
「違う」とみなす。`dig(z=285)` が「575.7m away」で断られ続けたとき、モデルは座標を 54, 55, 56 と変えただけで、
原因（マイナスを落としている）は変わらず、8 回以上続いた。原因の文（数字を除いて同じ）が続くことを数え、
モデルに「数字を変えても同じ」と伝え、続くようなら、その道具をその手番では出さない。
"""
from __future__ import annotations

import re
from typing import Callable, Sequence

from ailoveshen.domain.value_objects import SameCause, ToolOutcome

SAME_CAUSE_MIN = 3  # この回数続けたら、理由を示して別の方法を選ばせる
SAME_CAUSE_WITHHOLD = 4  # この回数続けたら、その道具をこの手番では出さない
# 出さなくならない道具（別の方法そのもの）
NEVER_WITHHELD = frozenset({"do_suggestion", "run_skill"})
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_REASON_CHARS = 200


def reason_key(result: str) -> str:
    """理由の文から数字（座標、距離、id）を除いたもの。同じ原因なら、数字が違っても同じになる。"""
    return _NUMBER.sub("#", result).strip()[:_REASON_CHARS]


def same_cause_run(
    outcomes: Sequence[ToolOutcome], lookup: Callable[[str], bool] = lambda name: False
) -> SameCause | None:
    """
    直近の道具の結果（古い順）の末尾で、失敗が同じ理由で `SAME_CAUSE_MIN` 回以上続いていれば、その状態。
    見張りが止めた失敗は数えない（原因が道具の使い方ではないので）。調べもの（`lookup(道具名)` が真）は
    間にあっても数えも切りもしない（調べては同じ手を繰り返す、が一番多い形）。
    """
    acts = [o for o in outcomes if not lookup(o.call.name)]
    last = acts[-1] if acts else None
    if last is None or last.ok or last.stopped_by:
        return None
    key = reason_key(last.result)
    times = 0
    for outcome in reversed(acts):
        if outcome.ok or outcome.stopped_by or reason_key(outcome.result) != key:
            break
        times += 1
    if times < SAME_CAUSE_MIN:
        return None
    tool = last.call.name
    return SameCause(
        tool=tool,
        reason=last.result[:_REASON_CHARS],
        times=times,
        withheld=times >= SAME_CAUSE_WITHHOLD and tool not in NEVER_WITHHELD,
    )
