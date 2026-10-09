"""もう持っている施設で大目標を済みにする（docs/design/142）: 記憶（ブリッジの施設の一覧）との突き合わせ。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from ailoveshen.domain.exceptions import GoalRejectedError
from ailoveshen.domain.value_objects import ConditionStatus, GoalPredicate

# 家の中心からこの距離（m）以内にある施設だけが、家の施設の代わりになる。畑や採掘場は通って使う物で、
# 引っ越し前の遠い畑を「ある」と数えると、新しい家に食料の基盤ができない
SATISFY_NEAR_HOME_M = 64


def _center(facility: dict[str, Any]) -> tuple[float, float] | None:
    c = facility.get("center")
    if not isinstance(c, dict) or "x" not in c or "z" not in c:
        return None
    return float(c["x"]), float(c["z"])


def home_distance(facility: dict[str, Any], facilities: Sequence[dict[str, Any]]) -> float | None:
    """家の中心からの距離（m）。家か施設の場所が分からなければ None。"""
    home = next((f for f in facilities if f.get("kind") == "home"), None)
    here = _center(facility)
    there = _center(home) if home else None
    if here is None or there is None:
        return None
    return math.hypot(here[0] - there[0], here[1] - there[1])


def home_signature(facilities: Sequence[dict[str, Any]]) -> tuple[float, float] | None:
    """家の場所（引っ越しで変わる。目標リストが変わった印の 1 つ）。"""
    home = next((f for f in facilities if f.get("kind") == "home"), None)
    return _center(home) if home else None


def _progress(f: dict[str, Any]) -> str:
    c = f.get("contents") or {}
    if "complete" in c:
        return f"{c.get('placed', 0)}/{c.get('total', 0)}"
    if "cells" in c and "done" in c:
        return f"{c['done']}/{c['cells']}"
    return ""


def describe_memory(facilities: Sequence[dict[str, Any]]) -> str:
    """目標の決定に見せる「作った施設」の行（家は除く）。家の近くかも添える。"""
    lines = []
    for f in facilities:
        if f.get("kind") == "home":
            continue
        far = home_distance(f, facilities)
        crop = (f.get("contents") or {}).get("crop")
        kind = f"{f.get('kind')}（{crop}）" if crop else str(f.get("kind"))
        status = "完成" if f.get("status") == "done" else f"{f.get('status')} {_progress(f)}".strip()
        if far is None:
            where = "家からの距離は不明"
        elif far <= SATISFY_NEAR_HOME_M:
            where = f"家から {far:.0f}m（家の近く）"
        else:
            where = f"家から {far:.0f}m（家の近くではない）"
        lines.append(f"- {f.get('name')}: {kind} {status}、{where}")
    return "\n".join(lines)


def check_satisfy(
    wanted: Sequence[ConditionStatus],
    by: str,
    facilities: Sequence[dict[str, Any]],
) -> None:
    """
    大目標の条件（`wanted`、ブリッジの判定つき）の施設を、`by` の施設が代わりに満たすかを確かめる。

    満たしていない施設の条件は 1 つだけで、`by` は同じ種類（畑なら同じ作物）の完成した施設で家の
    近くにあること。施設以外の条件は世界が判定する（満たしていなければ断る）。

    Raises:
        GoalRejectedError: 合わないとき（理由はモデルに返る）
    """
    by_name = {str(f.get("name")): f for f in facilities}
    others = [s for s in wanted if s.spec.predicate != GoalPredicate.FACILITY and not s.met]
    if others:
        raise GoalRejectedError(
            "satisfy needs every other condition met in the world first; not met: "
            + ", ".join(s.spec.describe() for s in others)
        )
    open_ones = [s for s in wanted if s.spec.predicate == GoalPredicate.FACILITY and not s.met]
    if not open_ones:
        return  # 世界がもう満たしている: 判定が済みにする
    if len(open_ones) > 1:
        raise GoalRejectedError(
            "satisfy can stand in for one facility at a time; this objective still needs: "
            + ", ".join(s.spec.describe() for s in open_ones)
        )
    wanted_name = str(open_ones[0].spec.name)
    target = by_name.get(wanted_name)
    if target is None:
        raise GoalRejectedError(f"the facility {wanted_name} is not registered, its kind is unknown")
    own = by_name.get(by)
    if own is None or own.get("kind") == "home":
        known = ", ".join(n for n, f in by_name.items() if f.get("kind") != "home") or "none"
        raise GoalRejectedError(f"there is no facility named {by} (facilities: {known})")
    if own.get("name") == wanted_name:
        raise GoalRejectedError(f"{by} is the facility the objective asks for; it is not done yet")
    if own.get("kind") != target.get("kind"):
        raise GoalRejectedError(
            f"{by} is a {own.get('kind')} but {wanted_name} is a {target.get('kind')}"
        )
    crops = {(f.get("contents") or {}).get("crop") for f in (own, target)}
    if None not in crops and len(crops) > 1:
        raise GoalRejectedError(
            f"{by} grows {(own.get('contents') or {}).get('crop')} but {wanted_name} grows "
            f"{(target.get('contents') or {}).get('crop')}"
        )
    if own.get("status") != "done":
        raise GoalRejectedError(f"{by} is not finished yet ({own.get('status')} {_progress(own)})".strip())
    far = home_distance(own, facilities)
    if far is None:
        raise GoalRejectedError("there is no home yet, so no facility is near it")
    if far > SATISFY_NEAR_HOME_M:
        raise GoalRejectedError(
            f"{by} is {far:.0f} m from the home (more than {SATISFY_NEAR_HOME_M} m): too far to serve it"
        )
