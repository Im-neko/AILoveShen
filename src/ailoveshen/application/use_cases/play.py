"""Play use cases: the LLM designs and sets goals, the bridge judges them, the selector plays."""

from __future__ import annotations

from typing import Any

from loguru import logger

from ailoveshen.application.dto.game_dto import PlayStepReport
from ailoveshen.application.ports.input.play import IAdvancePlay, IStartPlay
from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.goal_vocabulary import (
    GoalDecision,
    Serves,
    goal_schema,
    parse_decision,
    predicates_now,
)
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.domain.entities import Conversation, MidGoalPlan, PlaySession
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalEndedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
)
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    Activity,
    CharacterProfile,
    GameObservation,
    Goal,
    GoalStatus,
    HouseBlueprint,
    Side,
    is_survival,
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

    The mission and its mid goals carry on from the saved plan when it is for
    the same mission; otherwise the plan made from the configuration starts.
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        character: CharacterProfile,
        plan: MidGoalPlan,
        store: IMissionStore,
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
            plan: The mission with its first mid goals, from the configuration
            store: Keeps the plan across restarts
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
        self._plan = plan
        self._store = store
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
            plan=self._load_plan(),
            max_steps_per_goal=self._max_steps_per_goal,
            max_consecutive_failures=self._max_consecutive_failures,
            max_stalled_steps=self._max_stalled_steps,
        )

    def _load_plan(self) -> MidGoalPlan:
        plan = self._plan
        saved = self._store.load()
        if saved is not None and saved.mission == plan.mission:
            plan.restore(list(saved.pending), list(saved.finished), saved.next_id)
            logger.info(f"Mid goals carried on: {', '.join(g.describe() for g in plan.pending)}")
        else:
            if saved is not None:
                logger.info(f"Mission changed from '{saved.mission.text}': mid goals start anew")
            self._store.save(plan)
        return plan

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
    2. If a new small goal is due (none, met, its mid goal ended, stuck,
       stalled, too long, or the time of day changed), the mid goals are
       judged from the world first (those done are completed), then the
       current goal ends (GoalEndedEvent) and the LLM decides the next one,
       seeing the mission, the mid goals, what the streamer is doing and the
       recent conversation. With it the LLM may edit the mid-goal list (add,
       move, drop with a reason; the plan's limits hold). The small goal
       serves the mid goal at the top of the list after the edits, or
       survival. A goal or edit that cannot be used goes back to it with the
       reason
    3. The action selector (Jev) picks one of the candidates the bridge
       grounded for the goal and the body's needs; a single candidate is taken
       without a model call
    4. The bridge runs it, and the session counts the step toward the goal and
       its mid goal (a viewer's request over its budget is dropped and told)

    Only this loop changes the small goal: chat replies run concurrently and
    may add a viewer's mid goal behind the current one, never interrupting.
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        action_selector: IActionSelector,
        event_publisher: IEventPublisher,
        conversation: Conversation,
        mid_goals: MidGoalKeeper,
        history_limit: int = 10,
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
            conversation: What was said on stream (shared with commentary and replies)
            mid_goals: Judges, edits and saves the mid goals (shared with chat replies)
            history_limit: Number of recent conversation messages given to the goal decision
            max_goal_attempts: Goal decisions before giving up when they are rejected
        """
        self._bridge = bridge
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._action_selector = action_selector
        self._event_publisher = event_publisher
        self._conversation = conversation
        self._mid_goals = mid_goals
        self._history_limit = history_limit
        self._max_goal_attempts = max_goal_attempts

    async def execute(self, session: PlaySession) -> PlayStepReport:
        """Take one step of the session."""
        obs = await self._bridge.observe()
        session.observe(obs)
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
            await self._change_goal(session, obs)
            goal_changed = True
            obs = await self._bridge.observe()
            session.observe(obs)

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
        await self._mid_goals.step_counted(session.plan, session.record(result))
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

    async def _change_goal(self, session: PlaySession, obs: GameObservation) -> None:
        reason = session.goal_end_reason(obs)
        # Judged before deciding: a mid goal already done (the house built in an earlier run)
        # must not be the one the next goal serves
        await self._mid_goals.judge(session.plan)
        # The view before the goal ends: its status is why it ends
        activity = session.activity()
        await self._end_goal(session, obs, reason)
        await self._decide_goal(session, obs, activity, reason)

    async def _end_goal(self, session: PlaySession, obs: GameObservation, reason: str) -> None:
        met = obs.goal is not None and obs.goal.met
        outcome = session.end_goal(reason, met)
        if outcome is None:
            return
        goal = outcome.goal
        await self._event_publisher.publish(
            GoalEndedEvent(
                goal=goal.spec.describe(),
                reason=goal.reason,
                ended_because=reason,
                met=met,
            )
        )

    async def _decide_goal(
        self, session: PlaySession, obs: GameObservation, activity: Activity, reason: str
    ) -> None:
        predicates = predicates_now(obs)
        plan = session.plan
        error = ""
        for attempt in range(1, self._max_goal_attempts + 1):
            prompt = self._prompt_builder.build_goal_prompt(
                blueprint=session.blueprint,
                activity=activity,
                goal_ended_because=reason,
                recent_messages=self._conversation.recent_messages(self._history_limit),
                predicates=predicates,
                previous_error=error,
            )
            schema = goal_schema(predicates, [g.id for g in plan.pending])
            data = await self._text_generator.generate_json(prompt, schema)
            try:
                decision = parse_decision(data)
                if decision.spec.predicate not in predicates:
                    raise ValueError(
                        f"{decision.spec.predicate.value} is not one of the goals offered"
                    )
                await self._mid_goals.check_new(decision.changes)
                _serving(decision, self._mid_goals.rehearse(plan, decision.changes))
                status = await self._bridge.set_goal(decision.spec)
                # Applied only now, to the plan as it is (a reply may have added to it meanwhile)
                preview = self._mid_goals.rehearse(plan, decision.changes)
                _serving(decision, preview)
            except (ValueError, GoalRejectedError) as e:
                error = str(e)
                logger.warning(f"Goal decision {attempt} rejected: {data!r}: {error}")
                continue
            await self._mid_goals.commit(plan, decision.changes)
            await self._start_goal(session, obs, _goal(decision, plan), reason, status)
            return
        raise TextGenerationError(
            f"no acceptable goal after {self._max_goal_attempts} attempts: {error}"
        )

    async def _start_goal(
        self,
        session: PlaySession,
        obs: GameObservation,
        goal: Goal,
        reason: str,
        status: GoalStatus,
    ) -> None:
        session.set_goal(goal, obs.time_phase)
        mid = session.plan.get(goal.mid_goal_id) if goal.mid_goal_id else None
        serves = f" for {mid.describe()}" if mid else " for survival"
        logger.info(
            f"Goal: {goal.spec.describe()}{serves} ({reason}) - {goal.reason}; "
            f"remaining {status.remaining}"
        )
        await self._event_publisher.publish(
            GoalSetEvent(
                goal=goal.spec.describe(), reason=goal.reason, mid_goal=mid.title if mid else ""
            )
        )


def _serving(decision: GoalDecision, plan: MidGoalPlan) -> None:
    """Check the small goal serves what it says, in the plan after the edits."""
    if decision.serves == Serves.SURVIVAL:
        if not is_survival(decision.spec):
            raise ValueError(
                f"{decision.spec.describe()} is not a survival goal (through_night, at_home, "
                "cleared, have(food, n)); it must serve the mid goal at the top of the list"
            )
    elif plan.current is None:
        raise ValueError(
            "there is no mid goal left: add one toward the mission, or choose a survival goal"
        )


def _goal(decision: GoalDecision, plan: MidGoalPlan) -> Goal:
    current = plan.current
    mid_goal_id = current.id if decision.serves == Serves.CURRENT and current else None
    return Goal(spec=decision.spec, reason=decision.reason, mid_goal_id=mid_goal_id)
