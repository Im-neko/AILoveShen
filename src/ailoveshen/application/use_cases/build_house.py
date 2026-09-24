"""Build house use cases: the LLM designs and directs, the action selector plays."""

from __future__ import annotations

from collections import deque
from typing import Any

from loguru import logger

from ailoveshen.application.dto.game_dto import HouseStepReport
from ailoveshen.application.ports.input.build_house import IAdvanceHouseProject, IStartHouseProject
from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities import HouseProject
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
)
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    CharacterProfile,
    GameObservation,
    Goal,
    GoalType,
    HouseBlueprint,
    Side,
    WallOpening,
)

_SIDES = [s.value for s in Side]

HOUSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "A short name for the house"},
        "concept": {
            "type": "string",
            "description": "One sentence about the idea behind the design",
        },
        "width": {
            "type": "integer",
            "minimum": HouseBlueprint.MIN_SIDE,
            "maximum": HouseBlueprint.MAX_SIDE,
        },
        "depth": {
            "type": "integer",
            "minimum": HouseBlueprint.MIN_SIDE,
            "maximum": HouseBlueprint.MAX_SIDE,
        },
        "wall_height": {
            "type": "integer",
            "minimum": HouseBlueprint.MIN_WALL_HEIGHT,
            "maximum": HouseBlueprint.MAX_WALL_HEIGHT,
        },
        "door_side": {"type": "string", "enum": _SIDES},
        "door_offset": {"type": "integer", "minimum": 1, "maximum": HouseBlueprint.MAX_SIDE - 2},
        "windows": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "side": {"type": "string", "enum": _SIDES},
                    "offset": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": HouseBlueprint.MAX_SIDE - 2,
                    },
                },
                "required": ["side", "offset"],
            },
        },
        "corner_pillars": {"type": "boolean"},
    },
    "required": [
        "name",
        "concept",
        "width",
        "depth",
        "wall_height",
        "door_side",
        "door_offset",
        "windows",
        "corner_pillars",
    ],
}


def _goal_schema(goals: list[GoalType]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "enum": [g.value for g in goals]},
            "reason": {"type": "string", "description": "Why this goal now, in one short sentence"},
        },
        "required": ["goal", "reason"],
    }


def _parse_blueprint(data: dict[str, Any]) -> HouseBlueprint:
    try:
        return HouseBlueprint(
            name=str(data["name"]),
            concept=str(data["concept"]),
            width=int(data["width"]),
            depth=int(data["depth"]),
            wall_height=int(data["wall_height"]),
            door_side=Side(data["door_side"]),
            door_offset=int(data["door_offset"]),
            windows=tuple(
                WallOpening(Side(w["side"]), int(w["offset"])) for w in data.get("windows", [])
            ),
            corner_pillars=bool(data.get("corner_pillars", False)),
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"malformed blueprint: {e}") from e


class StartHouseProjectUseCase(IStartHouseProject):
    """
    Use case: the LLM designs a house, and its block plan is sent to the bridge.

    The design is validated by HouseBlueprint; an invalid design is sent back
    to the LLM with the validation error, up to max_attempts times.
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        character: CharacterProfile,
        max_attempts: int = 3,
        max_steps_per_goal: int = 15,
        max_consecutive_failures: int = 3,
    ) -> None:
        """
        Initialize use case with dependencies (Dependency Injection).

        Args:
            text_generator: LLM adapter (structured output)
            prompt_builder: Game prompt builder adapter
            bridge: Minecraft bridge adapter
            event_publisher: Event publisher for domain events
            character: The streamer's character profile (the design reflects it)
            max_attempts: Design attempts before giving up
            max_steps_per_goal: Steps before the LLM is asked for a new goal
            max_consecutive_failures: Failed steps in a row before a new goal is asked for
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._character = character
        self._max_attempts = max_attempts
        self._max_steps_per_goal = max_steps_per_goal
        self._max_consecutive_failures = max_consecutive_failures

    async def execute(self) -> HouseProject:
        """Design the house, send its plan, and return the new project."""
        blueprint = await self._design()
        logger.info(
            f"House designed: {blueprint.name} "
            f"{blueprint.width}x{blueprint.depth}x{blueprint.wall_height} "
            f"door={blueprint.door_side.value}:{blueprint.door_offset} "
            f"windows={len(blueprint.windows)} "
            f"pillars={blueprint.corner_pillars} - {blueprint.concept}"
        )
        await self._event_publisher.publish(
            HouseDesignedEvent(name=blueprint.name, concept=blueprint.concept)
        )
        await self._bridge.set_build_plan(
            blueprint.blocks(), blueprint.width, blueprint.depth, blueprint.height
        )
        return HouseProject(
            blueprint=blueprint,
            max_steps_per_goal=self._max_steps_per_goal,
            max_consecutive_failures=self._max_consecutive_failures,
        )

    async def _design(self) -> HouseBlueprint:
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_house_design_prompt(
                self._character, previous_error=error
            )
            data = await self._text_generator.generate_json(prompt, HOUSE_SCHEMA)
            try:
                return _parse_blueprint(data)
            except ValueError as e:
                error = str(e)
                logger.warning(f"House design attempt {attempt} rejected: {error}")
        raise TextGenerationError(
            f"no valid house design after {self._max_attempts} attempts: {error}"
        )


class AdvanceHouseProjectUseCase(IAdvanceHouseProject):
    """
    Use case: one step of the house project.

    1. Observe the game. While the bridge is busy (its reflex is handling a
       nearby threat) the step does nothing. Completing the house is announced
       once; the project goes on afterwards (surviving the night, food, ...)
    2. If a goal decision is due (none, met, stuck, too long, or the time of
       day changed), the LLM picks the next goal from the goals that have an
       executable action right now
    3. The action selector (Jev) picks one of the executable actions the goal
       allows (survival actions are always allowed); a single candidate is taken
       without a model call
    4. The bridge runs it, and the project records the outcome
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        action_selector: IActionSelector,
        event_publisher: IEventPublisher,
        goal_history: int = 5,
    ) -> None:
        """
        Initialize use case with dependencies (Dependency Injection).

        Args:
            bridge: Minecraft bridge adapter
            text_generator: LLM adapter (structured output) for goal decisions
            prompt_builder: Game prompt builder adapter
            action_selector: Fast decision model adapter (Jev)
            event_publisher: Event publisher for domain events
            goal_history: Number of recent goals shown to the LLM
        """
        self._bridge = bridge
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._action_selector = action_selector
        self._event_publisher = event_publisher
        self._recent_goals: deque[Goal] = deque(maxlen=goal_history)

    async def execute(self, project: HouseProject) -> HouseStepReport:
        """Take one step of the project."""
        obs = await self._bridge.observe()
        complete = project.is_complete(obs)
        if complete and not project.completion_announced:
            project.completion_announced = True
            await self._event_publisher.publish(HouseCompletedEvent(name=project.blueprint.name))
        if obs.busy:
            return HouseStepReport(
                goal=project.goal,
                goal_changed=False,
                decision=None,
                result=None,
                complete=complete,
                waiting=True,
            )

        goal_changed = False
        if project.needs_new_goal(obs):
            await self._decide_goal(project, obs)
            goal_changed = True

        goal = project.goal
        assert goal is not None
        candidates = [a for a in obs.actions if goal.allows(a.action_id)]
        if not candidates:
            logger.info(
                f"No executable action for goal {goal.goal_type.value}; asking for a new goal"
            )
            project.block_goal()
            return HouseStepReport(
                goal=goal, goal_changed=goal_changed, decision=None, result=None, complete=complete
            )

        if len(candidates) == 1:
            decision = ActionDecision(action_id=candidates[0].action_id, confidence=1.0)
        else:
            instructions = self._prompt_builder.build_action_instructions(goal, project.blueprint)
            decision = await self._action_selector.select(obs.state, candidates, instructions)

        result = await self._bridge.act(decision.action_id)
        project.record(result)
        await self._event_publisher.publish(
            GameActionExecutedEvent(
                action_id=result.action_id,
                ok=result.ok,
                result=result.result,
                confidence=decision.confidence,
            )
        )
        return HouseStepReport(
            goal=goal,
            goal_changed=goal_changed,
            decision=decision,
            result=result,
            complete=complete,
        )

    async def _decide_goal(self, project: HouseProject, obs: GameObservation) -> None:
        reason = project.goal_end_reason(obs)
        available = project.pursuable_goals(obs)
        prompt = self._prompt_builder.build_goal_prompt(
            blueprint=project.blueprint,
            needs=project.material_needs(obs),
            observation=obs,
            current_goal=project.goal,
            goal_ended_because=reason,
            recent_goals=tuple(self._recent_goals),
            goals=available,
        )
        data = await self._text_generator.generate_json(prompt, _goal_schema(available))
        try:
            goal = Goal(goal_type=GoalType(data["goal"]), reason=str(data.get("reason", "")))
        except (KeyError, ValueError) as e:
            raise TextGenerationError(f"invalid goal decision {data!r}: {e}") from e
        if goal.goal_type not in available:
            raise TextGenerationError(
                f"LLM chose {goal.goal_type.value}, which has no executable action now"
            )

        project.set_goal(goal, obs.time_phase)
        self._recent_goals.append(goal)
        logger.info(f"Goal: {goal.goal_type.value} ({reason}) - {goal.reason}")
        await self._event_publisher.publish(
            GoalSetEvent(goal_type=goal.goal_type.value, reason=goal.reason)
        )
