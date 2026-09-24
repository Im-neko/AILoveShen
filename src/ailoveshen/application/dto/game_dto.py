"""DTOs for the game agent use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.value_objects import ActionDecision, ActionResult, Goal


@dataclass(frozen=True)
class HouseStepReport:
    """What happened in one step of the house project."""

    goal: Optional[Goal]
    goal_changed: bool
    decision: Optional[ActionDecision]
    result: Optional[ActionResult]
    complete: bool
