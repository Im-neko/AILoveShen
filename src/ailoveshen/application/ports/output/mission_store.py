"""Mission store output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import MidGoal, Mission, TownDefinition


@dataclass(frozen=True)
class SavedPlan:
    """What is kept of a plan: the mission, the mid goals, the next id's number, the town."""

    mission: Mission
    pending: tuple[MidGoal, ...]
    finished: tuple[MidGoal, ...]
    next_id: int
    town: Optional[TownDefinition] = None
    town_stage: int = 0


class IMissionStore(ABC):
    """
    Output port keeping the mission and its mid goals across restarts.

    This interface is defined in the Application layer.
    Infrastructure adapters implement this interface.
    """

    @abstractmethod
    def load(self) -> Optional[SavedPlan]:
        """The saved plan, or None when nothing was saved."""
        ...

    @abstractmethod
    def save(self, plan: MidGoalPlan) -> None:
        """Save the plan (after every change)."""
        ...
