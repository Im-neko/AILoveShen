"""Entity base classes for Domain layer."""

from __future__ import annotations

import uuid
from abc import ABC
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from ailoveshen.domain.value_objects import (
    GOAL_ACTIONS,
    ActionResult,
    BlockKind,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalType,
    HouseBlueprint,
    MaterialNeeds,
    MessageRole,
    MessageType,
)

if TYPE_CHECKING:
    from ailoveshen.domain.events import DomainEvent


def generate_id() -> str:
    """Generate a unique identifier."""
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


@dataclass
class Entity(ABC):
    """
    Base class for all domain entities.

    Entities are objects that have a distinct identity that runs through time
    and different states. Two entities are equal if they have the same id.
    """

    id: str = field(default_factory=generate_id)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __eq__(self, other: Any) -> bool:
        """Entities are equal if they have the same id."""
        if not isinstance(other, Entity):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash based on id for use in sets and dicts."""
        return hash(self.id)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.id!r})"


@dataclass
class AggregateRoot(Entity):
    """
    Base class for aggregate roots.

    An aggregate root is an entity that acts as a gateway to a cluster
    of related objects. All external references should be to the aggregate root.
    """

    _domain_events: list["DomainEvent"] = field(default_factory=list, repr=False)

    def add_domain_event(self, event: "DomainEvent") -> None:
        """Add a domain event to be dispatched."""
        self._domain_events.append(event)

    def clear_domain_events(self) -> list["DomainEvent"]:
        """Clear and return all domain events."""
        events = self._domain_events.copy()
        self._domain_events.clear()
        return events


@dataclass(eq=False)
class Conversation(Entity):
    """
    The stream's short-term conversation history.

    Holds viewer chats and the streamer's utterances in order, keeping only
    the latest max_history messages.

    Raises:
        ValueError: If max_history is not positive.
    """

    max_history: int = 20
    _messages: deque[ConversationMessage] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate max_history and create the bounded history."""
        if self.max_history <= 0:
            raise ValueError(f"max_history must be positive, got {self.max_history}")
        self._messages = deque(maxlen=self.max_history)

    def add_viewer_message(
        self,
        content: str,
        user_name: str,
        user_id: Optional[str] = None,
    ) -> ConversationMessage:
        """Record a chat message from a viewer."""
        return self._append(ConversationMessage.from_viewer(content, user_name, user_id))

    def add_streamer_message(
        self,
        content: str,
        message_type: MessageType,
    ) -> ConversationMessage:
        """Record something the streamer said."""
        return self._append(ConversationMessage.from_streamer(content, message_type))

    def recent_messages(self, limit: int = 10) -> tuple[ConversationMessage, ...]:
        """Return the latest messages, oldest first."""
        if limit <= 0:
            return ()
        return tuple(self._messages)[-limit:]

    def recent_viewer_messages(self, limit: int = 5) -> tuple[ConversationMessage, ...]:
        """Return the latest viewer chat messages, oldest first."""
        if limit <= 0:
            return ()
        viewer = [m for m in self._messages if m.role == MessageRole.VIEWER]
        return tuple(viewer[-limit:])

    def clear(self) -> None:
        """Forget the whole history."""
        self._messages.clear()
        self.updated_at = _utc_now()

    def _append(self, message: ConversationMessage) -> ConversationMessage:
        self._messages.append(message)
        self.updated_at = _utc_now()
        return message

    def __iter__(self) -> Iterator[ConversationMessage]:
        return iter(self._messages)

    def __len__(self) -> int:
        return len(self._messages)


@dataclass(eq=False)
class HouseProject(Entity):
    """
    Building one house: the blueprint, the current goal and the progress rules.

    Goal completion is decided here from the observed world, not by the LLM:
    the LLM chooses what to pursue next, the project says when a goal is met.

    Raises:
        ValueError: If a limit is not positive.
    """

    blueprint: HouseBlueprint = field(kw_only=True)
    max_steps_per_goal: int = 15
    max_consecutive_failures: int = 3
    explore_steps: int = 3
    food_stock: int = 4
    goal: Optional[Goal] = field(default=None, init=False)
    goal_phase: str = field(default="", init=False)
    steps_in_goal: int = field(default=0, init=False)
    consecutive_failures: int = field(default=0, init=False)
    completion_announced: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Validate limits."""
        for label in (
            "max_steps_per_goal",
            "max_consecutive_failures",
            "explore_steps",
            "food_stock",
        ):
            if getattr(self, label) <= 0:
                raise ValueError(f"{label} must be positive, got {getattr(self, label)}")

    def material_needs(self, obs: GameObservation) -> MaterialNeeds:
        """Compute what is missing from the remaining blocks and the inventory."""
        remaining = obs.build.remaining if obs.build else self.blueprint.material_counts()
        planks_blocks = remaining.get(BlockKind.PLANKS, 0)
        log_blocks = remaining.get(BlockKind.LOG, 0)
        door_needed = remaining.get(BlockKind.DOOR, 0) > 0 and obs.count("_door") == 0
        sword_needed = obs.count("_sword") == 0
        table_needed = (
            (door_needed or sword_needed)
            and not obs.crafting_table_nearby
            and obs.inventory.get("crafting_table", 0) == 0
        )

        planks_total = planks_blocks
        if door_needed:
            planks_total += MaterialNeeds.PLANKS_PER_DOOR_CRAFT
        if sword_needed:
            planks_total += MaterialNeeds.PLANKS_PER_SWORD
        if table_needed:
            planks_total += MaterialNeeds.PLANKS_PER_TABLE
        planks_short = max(0, planks_total - obs.count("_planks"))
        logs_total = log_blocks + -(-planks_short // MaterialNeeds.PLANKS_PER_LOG)
        return MaterialNeeds(
            logs_short=max(0, logs_total - obs.count("_log")),
            planks_short=planks_short,
            door_needed=door_needed,
            table_needed=table_needed,
            sword_needed=sword_needed,
        )

    def is_complete(self, obs: GameObservation) -> bool:
        """Every planned block is in place in the world."""
        return obs.build is not None and obs.build.complete

    def goal_met(self, obs: GameObservation) -> bool:
        """Whether the current goal's purpose is fulfilled."""
        if self.goal is None:
            return False
        if self.goal.goal_type == GoalType.EXPLORE:
            return self.steps_in_goal >= self.explore_steps
        return self.is_met(self.goal.goal_type, obs)

    def is_met(self, goal_type: GoalType, obs: GameObservation) -> bool:
        """Whether a goal's purpose is already fulfilled (exploring never is)."""
        needs = self.material_needs(obs)
        if goal_type == GoalType.GATHER_WOOD:
            return needs.wood_ready
        if goal_type == GoalType.CRAFT:
            return needs.crafted
        if goal_type == GoalType.BUILD_SHELTER:
            return self.is_complete(obs)
        if goal_type == GoalType.SURVIVE_NIGHT:
            # Morning, and nothing waits at the door (the bridge offers stay_inside then)
            return obs.time_phase == "day" and not obs.can("stay_inside")
        if goal_type == GoalType.GET_FOOD:
            return obs.food_items >= self.food_stock
        return False

    def pursuable_goals(self, obs: GameObservation) -> list[GoalType]:
        """
        Goals worth offering: not fulfilled yet, with an executable goal action.

        A fulfilled goal would end right after being chosen, and the LLM would
        be asked again at every step.
        """
        ids = {a.action_id for a in obs.actions}
        return [g for g in GoalType if GOAL_ACTIONS[g] & ids and not self.is_met(g, obs)]

    def phase_changed(self, obs: GameObservation) -> bool:
        """The time of day moved on (e.g. dusk began) since the goal was set."""
        return self.goal is not None and obs.time_phase != self.goal_phase

    def needs_new_goal(self, obs: GameObservation) -> bool:
        """A goal decision is due: none yet, met, stuck, or pursued too long."""
        return (
            self.goal is None
            or self.goal_met(obs)
            or self.consecutive_failures >= self.max_consecutive_failures
            or self.steps_in_goal >= self.max_steps_per_goal
            or self.phase_changed(obs)
        )

    def set_goal(self, goal: Goal, time_phase: str) -> None:
        """Start pursuing a new goal at the given time of day."""
        self.goal = goal
        self.goal_phase = time_phase
        self.steps_in_goal = 0
        self.consecutive_failures = 0
        self.updated_at = _utc_now()

    def block_goal(self) -> None:
        """Nothing can be done for the current goal right now: force a new decision."""
        self.consecutive_failures = self.max_consecutive_failures
        self.updated_at = _utc_now()

    def goal_end_reason(self, obs: GameObservation) -> str:
        """Why a new goal is due (for the LLM prompt and logs)."""
        if self.goal is None:
            return "no goal yet"
        if self.goal_met(obs):
            return f"goal {self.goal.goal_type.value} is met"
        if self.consecutive_failures >= self.max_consecutive_failures:
            return (
                f"goal {self.goal.goal_type.value} is stuck "
                "(actions keep failing or none is available)"
            )
        if self.steps_in_goal >= self.max_steps_per_goal:
            return f"goal {self.goal.goal_type.value} ran for {self.steps_in_goal} steps"
        if self.phase_changed(obs):
            return f"the time of day changed from {self.goal_phase} to {obs.time_phase}"
        return ""

    def record(self, result: ActionResult) -> None:
        """Count a step toward the current goal."""
        self.steps_in_goal += 1
        self.consecutive_failures = 0 if result.ok else self.consecutive_failures + 1
        self.updated_at = _utc_now()
