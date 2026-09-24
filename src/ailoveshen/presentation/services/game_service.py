"""Game service for presentation layer."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from ailoveshen.application.ports.input.build_house import IAdvanceHouseProject, IStartHouseProject
from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities import HouseProject


@dataclass(frozen=True)
class HouseBuildOutcome:
    """Result of a house building run."""

    project: HouseProject
    steps: int
    complete: bool


class GameService:
    """
    Presentation layer service running the game agent.

    The LLM designs the house and sets goals, the action selector picks each
    action, the bridge plays. Runs step by step until the house is complete
    or the step budget is spent.
    """

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

    async def build_house(self, max_steps: int = 200) -> HouseBuildOutcome:
        """
        Design and build a house.

        Args:
            max_steps: Maximum number of actions before giving up

        Returns:
            The project, steps taken and whether every block is in place
        """
        project = await self._start.execute()
        for step in range(1, max_steps + 1):
            report = await self._advance.execute(project)
            if report.complete:
                logger.info(f"House '{project.blueprint.name}' complete after {step - 1} steps")
                return HouseBuildOutcome(project=project, steps=step - 1, complete=True)
            if report.result is not None and report.decision is not None:
                goal = report.goal.goal_type.value if report.goal else "-"
                logger.info(
                    f"[{step}] {goal} -> {report.decision.action_id} "
                    f"(conf {report.decision.confidence:.2f}): "
                    f"{'ok' if report.result.ok else 'FAILED'} {report.result.result} "
                    f"({report.result.seconds}s)"
                )
        logger.warning(f"House '{project.blueprint.name}' not complete after {max_steps} steps")
        return HouseBuildOutcome(project=project, steps=max_steps, complete=False)

    async def close(self) -> None:
        """Close the bridge client and the model clients."""
        await self._bridge.close()
        await self._text_generator.close()
        await self._action_selector.close()
