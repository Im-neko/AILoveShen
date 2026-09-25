"""大目標の保存の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import GoalSpec, MidGoal, Mission, TownDefinition


@dataclass(frozen=True)
class SavedPlan:
    """プランのうち保存するもの: 大目標、中目標、次の id の番号、街。"""

    mission: Mission
    pending: tuple[MidGoal, ...]
    finished: tuple[MidGoal, ...]
    next_id: int
    town: Optional[TownDefinition] = None
    town_stage: int = 0
    stage_met: tuple[GoalSpec, ...] = ()  # 今の段階の条件のうち、これまでに満たしたもの


class IMissionStore(ABC):
    """
    大目標とその中目標を、再起動をまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> Optional[SavedPlan]:
        """保存したプラン。何も保存していなければ None。"""
        ...

    @abstractmethod
    def save(self, plan: MidGoalPlan) -> None:
        """プランを保存する（変更のたびに）。"""
        ...
