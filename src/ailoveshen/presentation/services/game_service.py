"""Game service for presentation layer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger

from ailoveshen.application.ports.input.play import IAdvancePlay, IStartPlay
from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.domain.entities import PlaySession


@dataclass(frozen=True)
class PlayOutcome:
    """Result of a play session."""

    session: PlaySession
    steps: int
    house_complete: bool


class GameService:
    """
    Presentation layer service running the game agent.

    The LLM designs the house and sets goals, the bridge judges them and
    grounds the candidates, the action selector picks each one. The session
    goes on after the house is complete (the night, food, ...) until the step
    budget is spent.
    """

    WAIT_SECONDS = 1.0  # pause while the bridge's reflex is busy

    def __init__(
        self,
        start_play: IStartPlay,
        advance_play: IAdvancePlay,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        action_selector: IActionSelector,
        mid_goals: MidGoalKeeper,
    ) -> None:
        """
        Initialize game service.

        Args:
            start_play: Use case designing the house and starting the session
            advance_play: Use case taking one step
            bridge: Minecraft bridge, closed together with the service
            text_generator: LLM, closed together with the service
            action_selector: Action selector, closed together with the service
            mid_goals: Keeps the mid goals (chat replies add viewers' requests through it)
        """
        self._start = start_play
        self._advance = advance_play
        self._bridge = bridge
        self._text_generator = text_generator
        self._action_selector = action_selector
        self._mid_goals = mid_goals
        self._session: PlaySession | None = None

    @property
    def session(self) -> PlaySession | None:
        """The session being played (what commentary and chat replies see), None before play()."""
        return self._session

    @property
    def mid_goals(self) -> MidGoalKeeper:
        """Keeps the mid goals; chat replies accept viewers' requests through it."""
        return self._mid_goals

    async def play(self, max_steps: int = 200) -> PlayOutcome:
        """
        Design a house, then play until max_steps actions have been taken.

        Args:
            max_steps: Number of actions to take (steps spent waiting for the
                bridge's reflex do not count)

        Returns:
            The session, actions taken and whether the house is complete
        """
        session = await self._start.execute()
        self._session = session
        steps = 0
        house_complete = False
        while steps < max_steps:
            report = await self._advance.execute(session)
            house_complete = report.house_complete
            if report.waiting:
                await asyncio.sleep(self.WAIT_SECONDS)
                continue
            steps += 1
            if report.result is not None and report.decision is not None:
                goal = report.goal.spec.describe() if report.goal else "-"
                remaining = report.status.remaining if report.status else "-"
                logger.info(
                    f"[{steps}] {goal} (remaining {remaining}) -> {report.decision.action_id} "
                    f"(conf {report.decision.confidence:.2f}): "
                    f"{'ok' if report.result.ok else 'FAILED'} {report.result.result} "
                    f"({report.result.seconds}s)"
                )
        logger.info(
            f"Played {steps} steps; house '{session.blueprint.name}' "
            f"{'complete' if house_complete else 'not complete'}"
        )
        return PlayOutcome(session=session, steps=steps, house_complete=house_complete)

    async def close(self) -> None:
        """Close the bridge client and the model clients."""
        await self._bridge.close()
        await self._text_generator.close()
        await self._action_selector.close()
