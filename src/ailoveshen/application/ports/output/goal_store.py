"""今の小目標の保存の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.value_objects import Goal


@dataclass(frozen=True)
class SavedGoal:
    """今の小目標と、その歩みの数え（再起動のあとに続きから追うためのもの）。"""

    goal: Goal
    phase: str  # 小目標を決めたときの時間帯
    steps_in_goal: int = 0
    consecutive_failures: int = 0
    stalled_steps: int = 0
    least_remaining: Optional[int] = None
    deaths_at_goal: Optional[int] = None


class IGoalStore(ABC):
    """
    今の小目標を、再起動をまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> Optional[SavedGoal]:
        """保存した小目標。何も保存していないか、読めない形なら None。"""
        ...

    @abstractmethod
    def save(self, saved: SavedGoal) -> None:
        """小目標を保存する（ステップごとに）。"""
        ...
