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
    ActionResult,
    Activity,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalOutcome,
    HouseBlueprint,
    MessageRole,
    MessageType,
    ViewerRequest,
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
class PlaySession(Entity):
    """
    A play session: the house to build and the lifecycle of the current goal.

    Whether a goal is met is judged by the bridge from the world; the session
    decides when a new goal is due: a viewer's request, met, stuck (actions
    keep failing), stalled (the remaining work stopped going down), over
    budget, or the time of day changed.

    It is the one owner of what the streamer is doing (`activity()`): the goal
    decision, the commentary and the chat replies all read it. Only the step
    loop changes the goal; a chat reply leaves a request for the next step.

    Raises:
        ValueError: If a limit is not positive.
    """

    blueprint: HouseBlueprint = field(kw_only=True)
    max_steps_per_goal: int = 40
    max_consecutive_failures: int = 3
    max_stalled_steps: int = 8
    goal_history: int = 5
    goal: Optional[Goal] = field(default=None, init=False)
    goal_phase: str = field(default="", init=False)
    steps_in_goal: int = field(default=0, init=False)
    consecutive_failures: int = field(default=0, init=False)
    stalled_steps: int = field(default=0, init=False)
    least_remaining: Optional[int] = field(default=None, init=False)
    completion_announced: bool = field(default=False, init=False)
    last_observation: Optional[GameObservation] = field(default=None, init=False)
    request: Optional[ViewerRequest] = field(default=None, init=False)
    _recent_goals: deque[GoalOutcome] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate limits and create the bounded goal history."""
        for label in (
            "max_steps_per_goal",
            "max_consecutive_failures",
            "max_stalled_steps",
            "goal_history",
        ):
            if getattr(self, label) <= 0:
                raise ValueError(f"{label} must be positive, got {getattr(self, label)}")
        self._recent_goals = deque(maxlen=self.goal_history)

    def observe(self, obs: GameObservation) -> None:
        """Keep the latest observation (what the chat replies see of the game)."""
        self.last_observation = obs

    def activity(self) -> Activity:
        """What the streamer is doing and why (seen by goal decisions, commentary and replies)."""
        return Activity(
            goal=self.goal,
            observation=self.last_observation,
            recent_goals=self.recent_goals,
            request=self.request,
        )

    @property
    def recent_goals(self) -> tuple[GoalOutcome, ...]:
        """Recent goals and how each ended, oldest first."""
        return tuple(self._recent_goals)

    def request_goal(self, request: ViewerRequest) -> None:
        """Leave a viewer's request for the next step (a later one replaces it)."""
        self.request = request
        self.updated_at = _utc_now()

    def take_request(self) -> Optional[ViewerRequest]:
        """Hand the pending request to the step loop, which applies it."""
        request, self.request = self.request, None
        return request

    def end_goal(self, ended_because: str, met: bool) -> Optional[GoalOutcome]:
        """Record how the current goal ended (nothing when there is none)."""
        if self.goal is None:
            return None
        outcome = GoalOutcome(goal=self.goal, ended_because=ended_because, met=met)
        self._recent_goals.append(outcome)
        return outcome

    def track_progress(self, obs: GameObservation) -> None:
        """Note the remaining work of the current goal; not going down counts as stalling."""
        if self.goal is None or obs.goal is None:
            return
        remaining = obs.goal.remaining
        if self.least_remaining is None or remaining < self.least_remaining:
            self.least_remaining = remaining
            self.stalled_steps = 0
        elif self.steps_in_goal > 0:
            self.stalled_steps += 1

    def phase_changed(self, obs: GameObservation) -> bool:
        """The time of day moved on (e.g. dusk began) since the goal was set."""
        return self.goal is not None and obs.time_phase != self.goal_phase

    def needs_new_goal(self, obs: GameObservation) -> bool:
        """A new goal is due: a request, none yet, met, stuck, stalled, too long, or dusk/dawn."""
        return bool(self.goal_end_reason(obs))

    def goal_end_reason(self, obs: GameObservation) -> str:
        """Why a new goal is due, for the LLM prompt and logs ("" while the goal goes on)."""
        if self.request is not None:
            return f"viewer {self.request.user_name} asked: {self.request.message}"
        if self.goal is None or obs.goal is None:
            return "no goal yet"
        name = self.goal.spec.describe()
        if obs.goal.met:
            return f"goal {name} is met"
        if self.consecutive_failures >= self.max_consecutive_failures:
            return f"goal {name} is stuck (actions keep failing)"
        if self.stalled_steps >= self.max_stalled_steps:
            return f"goal {name} stalled (no progress in {self.stalled_steps} steps)"
        if self.steps_in_goal >= self.max_steps_per_goal:
            return f"goal {name} ran for {self.steps_in_goal} steps"
        if self.phase_changed(obs):
            return f"the time of day changed from {self.goal_phase} to {obs.time_phase}"
        return ""

    def set_goal(self, goal: Goal, time_phase: str) -> None:
        """Start pursuing a new goal at the given time of day."""
        self.goal = goal
        self.goal_phase = time_phase
        self.steps_in_goal = 0
        self.consecutive_failures = 0
        self.stalled_steps = 0
        self.least_remaining = None
        self.updated_at = _utc_now()

    def record(self, result: ActionResult) -> None:
        """Count a step toward the current goal."""
        self.steps_in_goal += 1
        self.consecutive_failures = 0 if result.ok else self.consecutive_failures + 1
        self.updated_at = _utc_now()
