"""最終目標の保存の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.value_objects import (
    Deed,
    GoalSpec,
    Mission,
    Objective,
    PlaybookStep,
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
    playbook_done: tuple[str, ...] = ()  # 済んだ定石の段（docs/design/74）
    origin: Optional[Mission] = None  # 設定の最終目標（保存したあとに変わっていないかの目印。なければ mission）
    missions_done: tuple[str, ...] = ()  # 達成した最終目標の文（docs/design/180）
    extra_playbook: tuple[PlaybookStep, ...] = ()  # 次の最終目標のために足した定石の段
    design_failed: dict[str, tuple[int, float]] = field(default_factory=dict)  # 設計に失敗した数と時刻（設計書 195）
    history: tuple[Deed, ...] = ()  # 自分がやったこと: 終わった大目標の記録（設計書 199）


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
