"""Play use cases: the LLM designs and sets goals, the bridge judges them, the selector plays."""

from __future__ import annotations

from collections import deque
from typing import Any

from loguru import logger

from ailoveshen.application.dto.game_dto import GoalOutcome, PlayStepReport
from ailoveshen.application.ports.input.play import IAdvancePlay, IStartPlay
from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
)
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    CharacterProfile,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    HouseBlueprint,
    Side,
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
        "corner_pillars",
    ],
}


MAX_GOAL_COUNT = 64
MIN_EXPLORE_DISTANCE = 8
MAX_EXPLORE_DISTANCE = 128


def _goal_schema(predicates: list[GoalPredicate]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "predicate": {"type": "string", "enum": [p.value for p in predicates]},
            "item": {
                "type": "string",
                "description": "have / placed: an item or group, e.g. planks, log, bed, food, "
                "crafting_table, stick, wooden_sword, wooden_pickaxe",
            },
            "count": {"type": "integer", "minimum": 1, "maximum": MAX_GOAL_COUNT},
            "distance": {
                "type": "integer",
                "minimum": MIN_EXPLORE_DISTANCE,
                "maximum": MAX_EXPLORE_DISTANCE,
            },
            "reason": {"type": "string", "description": "Why this goal now, in one short sentence"},
        },
        "required": ["predicate", "reason"],
    }


def _parse_goal(data: dict[str, Any]) -> Goal:
    try:
        predicate = GoalPredicate(data["predicate"])
        spec = GoalSpec(
            predicate=predicate,
            item=str(data["item"])
            if predicate in (GoalPredicate.HAVE, GoalPredicate.PLACED)
            else None,
            count=int(data["count"]) if predicate == GoalPredicate.HAVE else None,
            where="home" if predicate == GoalPredicate.PLACED else None,
            distance=int(data["distance"]) if predicate == GoalPredicate.EXPLORED else None,
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"missing or malformed argument: {e}") from e
    return Goal(spec=spec, reason=str(data.get("reason", "")))


def _predicates(obs: GameObservation) -> list[GoalPredicate]:
    """The predicates that make sense now (e.g. none about the home before it exists)."""
    out = []
    if obs.has_plan and not obs.house_complete:
        out.append(GoalPredicate.BUILT)
    out.append(GoalPredicate.HAVE)
    if obs.has_home:
        out += [GoalPredicate.AT_HOME, GoalPredicate.THROUGH_NIGHT]
        if obs.time_phase == "day":
            out.append(GoalPredicate.CLEARED)
        if not obs.bed_in_home:
            out.append(GoalPredicate.PLACED)
    out.append(GoalPredicate.EXPLORED)
    return out


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
            corner_pillars=bool(data.get("corner_pillars", False)),
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"malformed blueprint: {e}") from e


class StartPlayUseCase(IStartPlay):
    """
    Use case: the LLM designs a house, its block plan is sent to the bridge, a session starts.

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
        max_steps_per_goal: int = 40,
        max_consecutive_failures: int = 3,
        max_stalled_steps: int = 8,
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
            max_stalled_steps: Steps without progress before a new goal is asked for
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._character = character
        self._max_attempts = max_attempts
        self._max_steps_per_goal = max_steps_per_goal
        self._max_consecutive_failures = max_consecutive_failures
        self._max_stalled_steps = max_stalled_steps

    async def execute(self) -> PlaySession:
        """Design the house, send its plan, and return the new session."""
        blueprint = await self._design()
        logger.info(
            f"House designed: {blueprint.name} "
            f"{blueprint.width}x{blueprint.depth}x{blueprint.wall_height} "
            f"door={blueprint.door_side.value}:{blueprint.door_offset} "
            f"pillars={blueprint.corner_pillars} - {blueprint.concept}"
        )
        await self._event_publisher.publish(
            HouseDesignedEvent(name=blueprint.name, concept=blueprint.concept)
        )
        await self._bridge.set_build_plan(
            blueprint.blocks(), blueprint.width, blueprint.depth, blueprint.height
        )
        return PlaySession(
            blueprint=blueprint,
            max_steps_per_goal=self._max_steps_per_goal,
            max_consecutive_failures=self._max_consecutive_failures,
            max_stalled_steps=self._max_stalled_steps,
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


class AdvancePlayUseCase(IAdvancePlay):
    """
    Use case: one step of the play session.

    1. Observe. While the bridge is busy (its reflex is handling a nearby
       threat) the step does nothing. Completing the house is announced once
    2. If a new goal is due (none, met, stuck, stalled, too long, or the time
       of day changed), the LLM sets one in the predicate vocabulary; a goal
       the bridge rejects goes back to the LLM with the reason
    3. The action selector (Jev) picks one of the candidates the bridge
       grounded for the goal and the body's needs; a single candidate is taken
       without a model call
    4. The bridge runs it, and the session records the outcome
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        action_selector: IActionSelector,
        event_publisher: IEventPublisher,
        goal_history: int = 5,
        max_goal_attempts: int = 3,
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
            max_goal_attempts: Goal decisions before giving up when they are rejected
        """
        self._bridge = bridge
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._action_selector = action_selector
        self._event_publisher = event_publisher
        self._recent_goals: deque[GoalOutcome] = deque(maxlen=goal_history)
        self._max_goal_attempts = max_goal_attempts

    async def execute(self, session: PlaySession) -> PlayStepReport:
        """Take one step of the session."""
        obs = await self._bridge.observe()
        if obs.house_complete and not session.completion_announced:
            session.completion_announced = True
            await self._event_publisher.publish(HouseCompletedEvent(name=session.blueprint.name))
        if obs.busy:
            return PlayStepReport(
                goal=session.goal,
                goal_changed=False,
                status=obs.goal,
                decision=None,
                result=None,
                house_complete=obs.house_complete,
                waiting=True,
            )

        session.track_progress(obs)
        goal_changed = False
        if session.needs_new_goal(obs):
            await self._decide_goal(session, obs)
            goal_changed = True
            obs = await self._bridge.observe()

        goal = session.goal
        assert goal is not None
        candidates = obs.candidates
        if len(candidates) == 1:
            decision = ActionDecision(action_id=candidates[0].action_id, confidence=1.0)
        else:
            state, instructions = self._prompt_builder.build_action_context(goal, obs)
            logger.info(
                f"Candidates ({len(candidates)}): " + " | ".join(c.action_id for c in candidates)
            )
            decision = await self._action_selector.select(state, candidates, instructions)

        result = await self._bridge.act(decision.action_id)
        session.record(result)
        await self._event_publisher.publish(
            GameActionExecutedEvent(
                action_id=result.action_id,
                ok=result.ok,
                result=result.result,
                confidence=decision.confidence,
            )
        )
        return PlayStepReport(
            goal=goal,
            goal_changed=goal_changed,
            status=obs.goal,
            decision=decision,
            result=result,
            house_complete=obs.house_complete,
        )

    async def _decide_goal(self, session: PlaySession, obs: GameObservation) -> None:
        reason = session.goal_end_reason(obs)
        if session.goal is not None:
            self._recent_goals.append(GoalOutcome(goal=session.goal, ended_because=reason))
        predicates = _predicates(obs)
        error = ""
        for attempt in range(1, self._max_goal_attempts + 1):
            prompt = self._prompt_builder.build_goal_prompt(
                blueprint=session.blueprint,
                observation=obs,
                current_goal=session.goal,
                goal_ended_because=reason,
                recent_goals=tuple(self._recent_goals),
                predicates=predicates,
                previous_error=error,
            )
            data = await self._text_generator.generate_json(prompt, _goal_schema(predicates))
            try:
                goal = _parse_goal(data)
                if goal.spec.predicate not in predicates:
                    raise ValueError(f"{goal.spec.predicate.value} is not one of the goals offered")
                status = await self._bridge.set_goal(goal.spec)
            except (ValueError, GoalRejectedError) as e:
                error = str(e)
                logger.warning(f"Goal decision {attempt} rejected: {data!r}: {error}")
                continue
            session.set_goal(goal, obs.time_phase)
            logger.info(
                f"Goal: {goal.spec.describe()} ({reason}) - {goal.reason}; "
                f"remaining {status.remaining}"
            )
            await self._event_publisher.publish(
                GoalSetEvent(goal=goal.spec.describe(), reason=goal.reason)
            )
            return
        raise TextGenerationError(
            f"no acceptable goal after {self._max_goal_attempts} attempts: {error}"
        )
