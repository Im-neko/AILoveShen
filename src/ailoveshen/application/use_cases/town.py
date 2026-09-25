"""The town of the mission: defined once by the LLM, checked by the bridge, filled in later."""

from __future__ import annotations

from dataclasses import replace

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.goal_vocabulary import (
    parse_stage,
    parse_town,
    stage_schema,
    town_schema,
)
from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.events import TownDefinedEvent
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    GoalSpec,
    TownDefinition,
    TownStage,
)


class TownPlanner:
    """
    Makes the town of the mission once and keeps it doable (docs/design/15_town.md §3).

    The LLM writes what the town is and its stages. Each stage's conditions
    are checked by the bridge before the definition is kept: a condition it
    cannot judge, or an item nothing the streamer can do gets (the iron sword
    before smelting), is sent back with the reason. What is still wrong after
    the last attempt goes to the stage's unresolved parts with the reason, so
    nothing impossible becomes a mid goal.

    The definition is fixed: comments and runs do not change it. Only the
    unresolved parts are written again, at the start of a run, since abilities
    added since may now express them (the stage keeps its title and meaning).
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        character: CharacterProfile,
        store: IMissionStore,
        max_attempts: int = 3,
    ) -> None:
        """
        Initialize with dependencies (Dependency Injection).

        Args:
            text_generator: LLM adapter (structured output)
            prompt_builder: Game prompt builder adapter
            bridge: Minecraft bridge adapter (checks the conditions)
            event_publisher: Event publisher for domain events
            character: The streamer's character profile
            store: Keeps the plan across restarts
            max_attempts: Generations before what is still wrong is left unresolved
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._character = character
        self._store = store
        self._max_attempts = max_attempts

    async def prepare(self, plan: MidGoalPlan) -> None:
        """Define the town if it is not yet, else write its unresolved stages again; save."""
        if plan.town is None:
            town = await self._define(plan)
            plan.define_town(town)
            self._store.save(plan)
            logger.info(f"Town defined: {town.text} / {' → '.join(s.title for s in town.stages)}")
            for i, s in enumerate(town.stages):
                logger.info(f"  stage {i + 1} {_describe(s)}")
            await self._event_publisher.publish(
                TownDefinedEvent(text=town.text, stages=tuple(s.title for s in town.stages))
            )
            return
        town = plan.town
        stages = list(town.stages)
        for i in range(plan.town_stage, len(stages)):
            if stages[i].ready:
                continue
            stages[i] = await self._resolve(town, stages[i])
            logger.info(f"Town stage {i + 1} written again: {_describe(stages[i])}")
        if tuple(stages) != town.stages:
            plan.define_town(replace(town, stages=tuple(stages)))
            self._store.save(plan)

    async def _define(self, plan: MidGoalPlan) -> TownDefinition:
        error = ""
        town: TownDefinition | None = None
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_town_prompt(
                self._character, plan.mission, previous_error=error
            )
            data = await self._text_generator.generate_json(prompt, town_schema())
            try:
                town = parse_town(data)
            except ValueError as e:
                error = str(e)
                logger.warning(f"Town definition attempt {attempt} rejected: {error}")
                continue
            errors = [
                f"段階「{s.title}」: {e}" for s in town.stages for e in await self._problems(s)
            ]
            if not errors:
                return town
            error = "\n".join(errors)
            logger.warning(f"Town definition attempt {attempt} rejected: {error}")
        if town is None:
            raise TextGenerationError(
                f"no valid town definition after {self._max_attempts} attempts: {error}"
            )
        return replace(town, stages=tuple([await self._settle(s) for s in town.stages]))

    async def _resolve(self, town: TownDefinition, stage: TownStage) -> TownStage:
        error = ""
        written = stage
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_stage_prompt(town, stage, previous_error=error)
            data = await self._text_generator.generate_json(prompt, stage_schema())
            try:
                written = parse_stage(data, stage.title, stage.why)
            except ValueError as e:
                error = str(e)
                logger.warning(f"Town stage attempt {attempt} rejected: {error}")
                continue
            problems = await self._problems(written)
            if not problems:
                return written
            error = "\n".join(problems)
            logger.warning(f"Town stage attempt {attempt} rejected: {error}")
        return await self._settle(written)

    async def _problems(self, stage: TownStage) -> list[str]:
        """Why the stage's conditions cannot be kept, one line per condition (none: all good)."""
        return [p for c in stage.conditions if (p := await self._problem(c))]

    async def _problem(self, condition: GoalSpec) -> str:
        try:
            [status] = await self._bridge.check([condition])
        except GoalRejectedError as e:
            return f"{condition.describe()} は判定できない（{e}）"
        if status.impossible:
            why = ", ".join(status.impossible)
            return f"{condition.describe()} は今できることでは手に入らない（{why}）"
        return ""

    async def _settle(self, stage: TownStage) -> TownStage:
        """The stage with the conditions that cannot be kept moved to its unresolved parts."""
        kept: list[GoalSpec] = []
        unresolved = list(stage.unresolved)
        for c in stage.conditions:
            problem = await self._problem(c)
            if problem:
                unresolved.append(problem)
            else:
                kept.append(c)
        return replace(stage, conditions=tuple(kept), unresolved=tuple(unresolved))


def _describe(stage: TownStage) -> str:
    conditions = ", ".join(c.describe() for c in stage.conditions) or "-"
    unresolved = f" / unresolved: {'; '.join(stage.unresolved)}" if stage.unresolved else ""
    return f"{stage.title}: {conditions}{unresolved}"
