"""最終目標の保存の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.value_objects import (
    GoalSpec,
    Objective,
    Mission,
    TownDefinition,
    TownMap,
    TownSite,
)


@dataclass(frozen=True)
class SavedPlan:
    """プランのうち保存するもの: 最終目標、大目標、次の id の番号、街とその場所。"""

    mission: Mission
    pending: tuple[Objective, ...]
    finished: tuple[Objective, ...]
    next_id: int
    next_job_id: int = 1  # 次の中目標の id の番号（docs/design/50）
    town: Optional[TownDefinition] = None
    town_stage: int = 0
    stage_met: tuple[GoalSpec, ...] = ()  # 今の段階の条件のうち、これまでに満たしたもの
    site: Optional[TownSite] = None  # 選んだ街の場所
    town_map: Optional[TownMap] = None  # 街の理想図（docs/design/47 §6）


class IMissionStore(ABC):
    """
    最終目標とその大目標を、再起動をまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> Optional[SavedPlan]:
        """保存したプラン。何も保存していないか、読めない形なら None（最初のリストで始める）。"""
        ...

    @abstractmethod
    def save(self, plan: ObjectivePlan) -> None:
        """プランを保存する（変更のたびに）。"""
        ...
