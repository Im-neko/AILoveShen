"""Game service for presentation layer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger

from ailoveshen.application.ports.input.build_house import IAdvanceHouseProject, IStartHouseProject
from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities import HouseProject


@dataclass(frozen=True)
class PlayOutcome:
    """Result of a play session."""

    project: HouseProject
    steps: int
    complete: bool


class GameService:
    """
    Presentation layer service running the game agent.

    The LLM designs the house and sets goals, the action selector picks each
    action, the bridge plays. The session goes on after the house is complete
    (the night, food, ...) until the step budget is spent.
    """

    WAIT_SECONDS = 1.0  # pause while the bridge's reflex is busy

    def __init__(
        self,
        start_house_project: IStartHouseProject,
        advance_house_project: IAdvanceHouseProject,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        action_selector: IActionSelector,
    ) -> None:
        """
        Initialize game service.

        Args:
            start_house_project: Use case designing the house and sending its plan
            advance_house_project: Use case taking one step
            bridge: Minecraft bridge, closed together with the service
            text_generator: LLM, closed together with the service
            action_selector: Action selector, closed together with the service
        """
        self._start = start_house_project
        self._advance = advance_house_project
        self._bridge = bridge
        self._text_generator = text_generator
        self._action_selector = action_selector

    async def play(self, max_steps: int = 200) -> PlayOutcome:
        """
        Design a house, then play until max_steps actions have been taken.

        Args:
            max_steps: Number of actions to take (steps spent waiting for the
                bridge's reflex do not count)

        Returns:
            The project, actions taken and whether the house is complete
        """
        project = await self._start.execute()
        steps = 0
        complete = False
        while steps < max_steps:
            report = await self._advance.execute(project)
            complete = report.complete
            if report.waiting:
                await asyncio.sleep(self.WAIT_SECONDS)
                continue
            steps += 1
            if report.result is not None and report.decision is not None:
                goal = report.goal.goal_type.value if report.goal else "-"
                logger.info(
                    f"[{steps}] {goal} -> {report.decision.action_id} "
                    f"(conf {report.decision.confidence:.2f}): "
                    f"{'ok' if report.result.ok else 'FAILED'} {report.result.result} "
                    f"({report.result.seconds}s)"
                )
        logger.info(
            f"Played {steps} steps; house '{project.blueprint.name}' "
            f"{'complete' if complete else 'not complete'}"
        )
        return PlayOutcome(project=project, steps=steps, complete=complete)

    async def close(self) -> None:
        """Close the bridge client and the model clients."""
        await self._bridge.close()
        await self._text_generator.close()
        await self._action_selector.close()
