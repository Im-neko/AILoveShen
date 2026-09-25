"""DTOs for the game agent use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.value_objects import ActionDecision, ActionResult, Goal, GoalStatus


@dataclass(frozen=True)
class PlayStepReport:
    """What happened in one step of the play session."""

    goal: Optional[Goal]
    goal_changed: bool
    status: Optional[GoalStatus]
    decision: Optional[ActionDecision]
    result: Optional[ActionResult]
    house_complete: bool
    waiting: bool = False  # nothing done: the bridge was busy (reflex)
