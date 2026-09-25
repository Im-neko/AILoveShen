"""Minecraft bridge output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ailoveshen.domain.value_objects import (
    ActionResult,
    ConditionStatus,
    GameObservation,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
)


class IMinecraftBridge(ABC):
    """
    Output port for the process that plays Minecraft (Mineflayer sidecar).

    The bridge owns everything that needs the live world: judging the goal
    from the world, decomposing it into what can be done now (the candidates),
    running one candidate, and the build plan. This interface is defined in
    the Application layer.
    """

    @abstractmethod
    async def observe(self) -> GameObservation:
        """
        Take a snapshot of the game: the goal's status and the candidates.

        Raises:
            GameBridgeError: If the bridge is unreachable or not in the world
        """
        ...

    @abstractmethod
    async def set_goal(self, spec: GoalSpec) -> GoalStatus:
        """
        Set the goal the candidates are grounded for.

        Raises:
            GoalRejectedError: If the bridge rejects the goal (with the reason)
            GameBridgeError: If the bridge is unreachable
        """
        ...

    @abstractmethod
    async def check(self, specs: Sequence[GoalSpec]) -> list[ConditionStatus]:
        """
        Judge mid goals' conditions from the world without setting a goal.

        Raises:
            GoalRejectedError: If a spec cannot be a condition (the message says why)
            GameBridgeError: If the bridge is unreachable
        """
        ...

    @abstractmethod
    async def act(self, action_id: str) -> ActionResult:
        """
        Run one candidate to completion (or failure) and report the outcome.

        Raises:
            GameBridgeError: If the bridge is unreachable or busy
        """
        ...

    @abstractmethod
    async def set_build_plan(self, blueprint: HouseBlueprint) -> None:
        """
        Give the bridge the house to build: its blocks in placement order, and the design
        itself, which the bridge keeps with the home it becomes (the observation's home design).

        Raises:
            GameBridgeError: If the bridge rejects the plan
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources held by the client."""
        ...
