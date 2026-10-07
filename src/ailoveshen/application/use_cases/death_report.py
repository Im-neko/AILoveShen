"""死んだときの様子（docs/design/105）: ブリッジが死んだ瞬間にまとめた記録を、教訓の状況と、目標の決定の文にする。

復活すると位置も時間帯も体力も変わる（夜の外で死んでも、復活後は「家の中、体力 20」）ので、
教訓の「どんなときか」は、死ぬ前の様子から取る。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ailoveshen.domain.value_objects import SITUATION_VALUES

# ダメージの種類の言い方（ブリッジの damage-log の原因の種類）
_KINDS = {
    "unknown": "原因不明（遠くからの矢・爆発など）",
    "fall": "落下",
    "drowning": "溺れ",
    "lava": "溶岩",
    "burning": "火",
    "starving": "空腹",
}


def death_situation(report: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """死ぬ前の様子から、教訓の状況のキー（time / place / body。死んだので body は hurt）。"""
    if not report:
        return {}
    keys: dict[str, str] = {}
    phase = report.get("phase")
    if phase in SITUATION_VALUES["time"]:
        keys["time"] = phase
    place = report.get("place")
    if place in SITUATION_VALUES["place"]:
        keys["place"] = place
    keys["body"] = "hurt"
    return keys


def describe_death(report: Optional[Mapping[str, Any]]) -> str:
    """目標の決定の文に添える、死んだときの様子（なければ空）。"""
    if not report:
        return ""
    where = {"home": "家の中", "underground": "地下", "outside": "家の外"}.get(
        str(report.get("place")), "場所不明"
    )
    phase = {"day": "昼", "dusk": "夕方", "night": "夜", "dawn": "明け方"}.get(
        str(report.get("phase")), "時間帯不明"
    )
    lines = [f"- {phase}、{where}で死んだ"]
    far = report.get("from_home_m")
    if isinstance(far, (int, float)):
        lines[0] += f"（家のドアから約 {int(far)} m）"
    damage = report.get("damage") or {}
    if damage:
        parts = [
            f"{_KINDS.get(kind, kind)} {total:g}"
            for kind, total in sorted(damage.items(), key=lambda kv: -kv[1])
        ]
        seconds = report.get("seconds")
        before = report.get("health_before")
        span = f"{int(seconds)} 秒で" if isinstance(seconds, (int, float)) and seconds else ""
        start = f"体力 {before:g} から" if isinstance(before, (int, float)) else ""
        lines.append(f"- {start}{span}{report.get('hits', 0)} 回受けた: {'、'.join(parts)}")
    doing = report.get("doing") or []
    if doing:
        lines.append(f"- そのときの行動: {' → '.join(str(d) for d in doing)}")
    return "\n".join(lines)
