"""
技が動いている間の経過（設計書 186）。

技は 1 回に数十秒〜数分かかり、その間は観測が更新されない（次の手を選ぶまで）。実況は「状況が変わった」と
見なせず、行動の最初に意図を 1 回読んだあとは技が終わるまで黙っていた（2026-10-18 user: 「スキルは何を
してるのかある程度喋るようにしたい。視聴者からすると急に黙っちゃうから」）。ブリッジの状態（位置と、
技が書いた最後の数行）から、実況に渡す 1 行の経過を作る。
"""

from __future__ import annotations

from typing import Any, Optional

LOG_LINES_SHOWN = 2
LOG_CHARS = 80


def _position(state: dict[str, Any]) -> Optional[tuple[float, float, float]]:
    p = (state.get("self") or {}).get("position") or {}
    try:
        return float(p["x"]), float(p["y"]), float(p["z"])
    except (KeyError, TypeError, ValueError):
        return None


def skill_note(
    name: str, state: dict[str, Any], start: Optional[tuple[float, float, float]]
) -> str:
    """
    動いている技の経過の 1 行（技の名前、経った秒数、今の位置と始めからの動き、技のログ）。
    技が動いていなければ空。`start` は技を始めたときの位置。
    """
    skill = state.get("skill") or {}
    if not skill:
        return ""
    parts = [f"技「{name}」を実行して{int(skill.get('seconds') or 0)}秒"]
    here = _position(state)
    if here is not None:
        parts.append(f"今 x={here[0]:.0f} y={here[1]:.0f} z={here[2]:.0f}")
        if start is not None:
            dy = here[1] - start[1]
            flat = ((here[0] - start[0]) ** 2 + (here[2] - start[2]) ** 2) ** 0.5
            moved = []
            if abs(dy) >= 1:
                moved.append(f"{abs(dy):.0f}ブロック{'下りた' if dy < 0 else '上がった'}")
            if flat >= 3:
                moved.append(f"横に{flat:.0f}m動いた")
            if moved:
                parts.append("始めから" + "、".join(moved))
    log = [str(x)[:LOG_CHARS] for x in (skill.get("log") or [])][-LOG_LINES_SHOWN:]
    if log:
        parts.append("技のログ: " + " / ".join(log))
    return "、".join(parts)


def start_position(state: dict[str, Any]) -> Optional[tuple[float, float, float]]:
    """技を始めたときの位置（経過の「始めから」の基準）。"""
    return _position(state)
