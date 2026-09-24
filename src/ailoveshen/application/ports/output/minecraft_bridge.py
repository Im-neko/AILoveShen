"""Minecraft bridge output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ailoveshen.domain.value_objects import ActionResult, GameObservation, PlannedBlock


class IMinecraftBridge(ABC):
    """
    Output port for the process that plays Minecraft (Mineflayer sidecar).

    The bridge owns everything that needs the live world: observation,
    enumerating the actions executable right now, running them, and placing
    a build plan's blocks. This interface is defined in the Application layer.
    """

    @abstractmethod
    async def observe(self) -> GameObservation:
        """
        Take a snapshot of the game and the actions executable right now.

        Raises:
            GameBridgeError: If the bridge is unreachable or not in the world
        """
        ...

    @abstractmethod
    async def act(self, action_id: str) -> ActionResult:
        """
        Run one action to completion (or failure) and report the outcome.

        Raises:
            GameBridgeError: If the bridge is unreachable or busy
        """
        ...

    @abstractmethod
    async def set_build_plan(
        self,
        blocks: Sequence[PlannedBlock],
        width: int,
        depth: int,
        height: int,
    ) -> None:
        """
        Give the bridge the blocks to build, in placement order.

        Raises:
            GameBridgeError: If the bridge rejects the plan
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources held by the client."""
        ...
