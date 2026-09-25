"""ゲームエージェントのユースケースの DTO。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.value_objects import ActionDecision, ActionResult, Goal, GoalStatus


@dataclass(frozen=True)
class PlayStepReport:
    """プレイセッションの 1 ステップで起きたこと。"""

    goal: Optional[Goal]
    goal_changed: bool
    status: Optional[GoalStatus]
    decision: Optional[ActionDecision]
    result: Optional[ActionResult]
    house_complete: bool
    waiting: bool = False  # 何もしていない: ブリッジが反射で塞がっていた
