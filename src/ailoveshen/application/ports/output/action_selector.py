"""Action selector output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ailoveshen.domain.value_objects import ActionDecision, AvailableAction


class IActionSelector(ABC):
    """
    Output port for the fast typed decision model (Jev) picking the next action.

    This interface is defined in the Application layer.
    """

    @abstractmethod
    async def select(
        self,
        state: dict[str, Any],
        actions: Sequence[AvailableAction],
        instructions: str,
    ) -> ActionDecision:
        """
        Pick exactly one of the given actions for the given state.

        Args:
            state: JSON-serializable game state
            actions: Candidate actions (at least two)
            instructions: What the choice should optimize for

        Raises:
            ActionSelectionError: If the model call fails
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources held by the selector."""
        ...
