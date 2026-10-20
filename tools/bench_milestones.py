"""
判断なしのプレイ（設計書 191）の進み具合を、LLM に見せない外側から測る。

ブリッジの目標の判定は使わない（raw では目標がない）。ボットの持ち物を 1 秒ごとに見て、一度でも持ったものと、置いたブロック
（作業台とかまど: 作ってすぐ置くと持ち物には残らない）から、節目に着いた最初の秒を記録する（ラッチ）。
家は設計も判定もないので、節目に入れない（寝床や屋根の走査は別に要る）。
"""

from __future__ import annotations

from typing import Iterable

# (名前, どれかの持ち物の名前の終わり/一致, 置いたブロックの名前)。順番が進みの順
MILESTONES: tuple[tuple[str, tuple[str, ...], str | None], ...] = (
    ("log", ("_log",), None),
    ("planks", ("_planks",), None),
    ("crafting_table", ("crafting_table",), "crafting_table"),
    ("wooden_pickaxe", ("wooden_pickaxe",), None),
    ("cobblestone", ("cobblestone",), None),
    ("stone_pickaxe", ("stone_pickaxe",), None),
    ("furnace", ("furnace",), "furnace"),
    ("coal", ("coal", "charcoal"), None),
    ("raw_iron", ("raw_iron",), None),
    ("iron_ingot", ("iron_ingot",), None),
    ("iron_pickaxe", ("iron_pickaxe",), None),
    ("diamond", ("diamond",), None),
)
NAMES = tuple(m[0] for m in MILESTONES)
PLACED_BLOCKS = tuple(m[2] for m in MILESTONES if m[2])
FINISH = "diamond"  # これに着いたら成功


def _holds(inventory: dict[str, int], suffixes: tuple[str, ...]) -> bool:
    return any(count > 0 and any(item.endswith(s) for s in suffixes) for item, count in inventory.items())


def reached(inventory: dict[str, int], placed: Iterable[str] = ()) -> set[str]:
    """今の持ち物と、近くにある置いたブロックの名前から、今この瞬間に成り立っている節目。"""
    placed = set(placed)
    out = set()
    for name, suffixes, block in MILESTONES:
        if _holds(inventory, suffixes) or (block is not None and block in placed):
            out.add(name)
    return out


class MilestoneTracker:
    """節目に最初に着いた秒を持つ（一度着いたら戻らない）。"""

    def __init__(self) -> None:
        self.at: dict[str, float] = {}

    def update(self, seconds: float, inventory: dict[str, int], placed: Iterable[str] = ()) -> list[str]:
        """新しく着いた節目の名前（順番どおり）。"""
        now = reached(inventory, placed)
        new = [n for n in NAMES if n in now and n not in self.at]
        for name in new:
            self.at[name] = round(seconds, 1)
        return new

    @property
    def finished(self) -> bool:
        return FINISH in self.at
