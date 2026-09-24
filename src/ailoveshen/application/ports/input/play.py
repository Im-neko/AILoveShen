"""Play input ports."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.game_dto import PlayStepReport
from ailoveshen.domain.entities import PlaySession


class IStartPlay(ABC):
    """Input port: design a house with the LLM, hand its plan to the bridge, start a session."""

    @abstractmethod
    async def execute(self) -> PlaySession:
        """
        Design the house and send the build plan.

        Raises:
            TextGenerationError: If the LLM cannot produce a valid blueprint
            GameBridgeError: If the bridge rejects the plan
        """
        ...


class IAdvancePlay(ABC):
    """Input port: take one step (a new goal if one is due, then one action)."""

    @abstractmethod
    async def execute(self, session: PlaySession) -> PlayStepReport:
        """Observe, set a new goal if due, pick and run one candidate."""
        ...
