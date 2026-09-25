"""Entity base classes for Domain layer."""

from __future__ import annotations

import uuid
from abc import ABC
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from ailoveshen.domain.value_objects import (
    ActionResult,
    Activity,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalOutcome,
    GoalSpec,
    HouseBlueprint,
    MessageRole,
    MessageType,
    MidGoal,
    MidGoalState,
    Mission,
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
class MidGoalPlan(Entity):
    """
    The mission and its mid goals in priority order (the first pending one is
    worked on now).

    The limits keep viewers from taking the stream over: a viewer's mid goal
    goes behind the one being worked on, at most one per viewer and
    `max_viewer_goals` in all, and is dropped once it has used
    `viewer_budget` steps. Mid goals are completed from the world (the bridge
    judges their conditions), never by a model saying so.

    Raises:
        ValueError: If a limit is not positive (on creation), or an operation
            breaks a limit (the message says which, for the model to retry).
    """

    mission: Mission = field(kw_only=True)
    max_goals: int = 6
    max_viewer_goals: int = 2
    viewer_budget: int = 80
    finished_shown: int = 3
    _goals: list[MidGoal] = field(default_factory=list, init=False, repr=False)
    _finished: deque[MidGoal] = field(init=False, repr=False)
    _next_id: int = field(default=1, init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate limits."""
        for label in ("max_goals", "max_viewer_goals", "viewer_budget", "finished_shown"):
            if getattr(self, label) <= 0:
                raise ValueError(f"{label} must be positive, got {getattr(self, label)}")
        self._finished = deque(maxlen=self.finished_shown)

    @property
    def pending(self) -> tuple[MidGoal, ...]:
        """Mid goals still to do, in priority order."""
        return tuple(self._goals)

    @property
    def finished(self) -> tuple[MidGoal, ...]:
        """Recently completed or dropped mid goals, oldest first."""
        return tuple(self._finished)

    @property
    def current(self) -> Optional[MidGoal]:
        """The mid goal worked on now (the first pending one)."""
        return self._goals[0] if self._goals else None

    def get(self, mid_goal_id: str) -> Optional[MidGoal]:
        """A pending mid goal by id."""
        return next((g for g in self._goals if g.id == mid_goal_id), None)

    def add(
        self,
        title: str,
        conditions: tuple[GoalSpec, ...],
        reason: str = "",
        requested_by: Optional[str] = None,
        position: Optional[int] = None,
    ) -> MidGoal:
        """
        Add a mid goal at `position` (0-based among the pending; None: last).

        A viewer's goes behind the one being worked on, whatever the position.
        """
        if len(self._goals) >= self.max_goals:
            raise ValueError(f"the mid goal list is full ({self.max_goals})")
        if requested_by is not None:
            viewers = [g for g in self._goals if g.requested_by is not None]
            if any(g.requested_by == requested_by for g in viewers):
                raise ValueError(f"{requested_by} already has a request in the list")
            if len(viewers) >= self.max_viewer_goals:
                raise ValueError(f"the list already has {self.max_viewer_goals} viewers' requests")
        goal = MidGoal(
            id=f"m{self._next_id}",
            title=title,
            conditions=conditions,
            reason=reason,
            requested_by=requested_by,
        )
        earliest = 1 if requested_by is not None and self._goals else 0
        at = (
            len(self._goals) if position is None else min(max(position, earliest), len(self._goals))
        )
        self._goals.insert(at, goal)
        self._next_id += 1
        self.updated_at = _utc_now()
        return goal

    def move(self, mid_goal_id: str, position: int) -> MidGoal:
        """Reprioritise a pending mid goal (0-based position); a viewer's never goes first."""
        goal = self._require(mid_goal_id)
        self._goals.remove(goal)
        earliest = 1 if goal.requested_by is not None and self._goals else 0
        self._goals.insert(min(max(position, earliest), len(self._goals)), goal)
        self.updated_at = _utc_now()
        return goal

    def drop(self, mid_goal_id: str, reason: str) -> MidGoal:
        """Give a mid goal up; the reason is required (it is said on stream)."""
        if not reason:
            raise ValueError(f"dropping {mid_goal_id} needs a reason")
        return self._finish(mid_goal_id, MidGoalState.DROPPED, reason)

    def complete(self, mid_goal_id: str) -> MidGoal:
        """Mark a mid goal done (its conditions hold in the world)."""
        return self._finish(mid_goal_id, MidGoalState.DONE, "its conditions hold")

    def judged(self, mid_goal_id: str, progress: tuple[str, ...]) -> None:
        """Keep how a mid goal's conditions stand."""
        goal = self._require(mid_goal_id)
        self._replace(goal, replace(goal, progress=progress))

    def charge(self, mid_goal_id: Optional[str]) -> Optional[MidGoal]:
        """Count a step for a mid goal; a viewer's over budget is dropped and returned."""
        goal = self.get(mid_goal_id) if mid_goal_id else None
        if goal is None:
            return None
        goal = self._replace(goal, replace(goal, steps=goal.steps + 1))
        if goal.requested_by is not None and goal.steps >= self.viewer_budget:
            return self.drop(goal.id, f"it took {goal.steps} steps, over the budget for a request")
        return None

    def restore(self, pending: list[MidGoal], finished: list[MidGoal], next_id: int) -> None:
        """Put back a saved plan (ids stay as they were)."""
        self._goals = list(pending)
        self._finished.clear()
        self._finished.extend(finished)
        self._next_id = next_id

    @property
    def next_id(self) -> int:
        """The number the next mid goal's id gets (kept when saved)."""
        return self._next_id

    def _require(self, mid_goal_id: str) -> MidGoal:
        goal = self.get(mid_goal_id)
        if goal is None:
            ids = ", ".join(g.id for g in self._goals) or "none"
            raise ValueError(f"no pending mid goal {mid_goal_id} (pending: {ids})")
        return goal

    def _replace(self, old: MidGoal, new: MidGoal) -> MidGoal:
        self._goals[self._goals.index(old)] = new
        self.updated_at = _utc_now()
        return new

    def _finish(self, mid_goal_id: str, state: MidGoalState, because: str) -> MidGoal:
        goal = self._require(mid_goal_id)
        self._goals.remove(goal)
        ended = replace(goal, state=state, ended_because=because)
        self._finished.append(ended)
        self.updated_at = _utc_now()
        return ended


@dataclass(eq=False)
class PlaySession(Entity):
    """
    A play session: the house to build, the mission with its mid goals, and
    the lifecycle of the current (small) goal.

    Whether a goal is met is judged by the bridge from the world; the session
    decides when a new goal is due: none yet, met, the mid goal it served is
    done or dropped, stuck (actions keep failing), stalled (the remaining
    work stopped going down), over budget, or the time of day changed.

    It is the one owner of what the streamer is doing (`activity()`): the goal
    decision, the commentary and the chat replies all read it. Only the step
    loop changes the small goal; a chat reply may add a mid goal (within the
    plan's limits), never interrupting the small goal.

    Raises:
        ValueError: If a limit is not positive.
    """

    blueprint: HouseBlueprint = field(kw_only=True)
    plan: MidGoalPlan = field(kw_only=True)
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
            mission=self.plan.mission,
            mid_goals=self.plan.pending + self.plan.finished,
            goal=self.goal,
            observation=self.last_observation,
            recent_goals=self.recent_goals,
        )

    @property
    def recent_goals(self) -> tuple[GoalOutcome, ...]:
        """Recent goals and how each ended, oldest first."""
        return tuple(self._recent_goals)

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
        """A new goal is due: none yet, met, its mid goal ended, stuck, stalled, too long, dusk."""
        return bool(self.goal_end_reason(obs))

    def goal_end_reason(self, obs: GameObservation) -> str:
        """Why a new goal is due, for the LLM prompt and logs ("" while the goal goes on)."""
        if self.goal is None or obs.goal is None:
            return "no goal yet"
        name = self.goal.spec.describe()
        if obs.goal.met:
            return f"goal {name} is met"
        mid = self.goal.mid_goal_id
        if mid is not None and self.plan.get(mid) is None:
            return f"the mid goal {mid} that goal {name} served has ended"
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

    def record(self, result: ActionResult) -> Optional[MidGoal]:
        """Count a step toward the current goal and its mid goal; returns a mid goal dropped
        for going over its budget (a viewer's request)."""
        self.steps_in_goal += 1
        self.consecutive_failures = 0 if result.ok else self.consecutive_failures + 1
        self.updated_at = _utc_now()
        return self.plan.charge(self.goal.mid_goal_id if self.goal else None)
