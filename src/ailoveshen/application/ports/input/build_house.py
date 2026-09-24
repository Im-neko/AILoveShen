"""Build house input ports."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.game_dto import HouseStepReport
from ailoveshen.domain.entities import HouseProject


class IStartHouseProject(ABC):
    """Input port: design a house with the LLM and hand its plan to the bridge."""

    @abstractmethod
    async def execute(self) -> HouseProject:
        """
        Design the house and send the build plan.

        Raises:
            TextGenerationError: If the LLM cannot produce a valid blueprint
            GameBridgeError: If the bridge rejects the plan
        """
        ...


class IAdvanceHouseProject(ABC):
    """Input port: take one step (goal decision if due, then one action)."""

    @abstractmethod
    async def execute(self, project: HouseProject) -> HouseStepReport:
        """Observe, decide the goal if due, pick and run one action."""
        ...
