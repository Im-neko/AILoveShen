"""Domain events."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ailoveshen.domain.value_objects import EmotionState

# =============================================================================
# Base Event
# =============================================================================


def _generate_event_id() -> str:
    """Generate a unique event ID."""
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DomainEvent:
    """
    Base class for domain events.

    Domain events represent something meaningful that happened in the domain.
    They are immutable (frozen=True) and carry all necessary information.
    """

    event_id: str = field(default_factory=_generate_event_id)
    occurred_at: datetime = field(default_factory=_utc_now)

    @property
    def event_type(self) -> str:
        """Return the event type name."""
        return self.__class__.__name__


# =============================================================================
# Speech Events
# =============================================================================


@dataclass(frozen=True)
class SpeechStartedEvent(DomainEvent):
    """
    Domain event raised when speech synthesis/playback begins.

    Used to notify other components that the AI is speaking.
    """

    text: str = field(default="")
    source: str = field(default="unknown")  # e.g., "commentary", "chat_response"
    emotion: EmotionState = field(default_factory=EmotionState)


@dataclass(frozen=True)
class SpeechCompletedEvent(DomainEvent):
    """
    Domain event raised when speech playback ends.

    Indicates whether the speech completed normally or was interrupted.
    """

    text: str = field(default="")
    source: str = field(default="unknown")
    completed: bool = field(default=True)  # False if interrupted
    duration_ms: int = field(default=0)


@dataclass(frozen=True)
class SpeechQueuedEvent(DomainEvent):
    """
    Domain event raised when a speech request is added to the queue.

    Useful for tracking pending speech requests.
    """

    text: str = field(default="")
    source: str = field(default="unknown")
    queue_position: int = field(default=0)


# =============================================================================
# Conversation Events
# =============================================================================


@dataclass(frozen=True)
class CommentaryGeneratedEvent(DomainEvent):
    """Domain event raised when the streamer's commentary has been generated."""

    text: str = field(default="")


@dataclass(frozen=True)
class ChatResponseGeneratedEvent(DomainEvent):
    """Domain event raised when a reply to a viewer's chat has been generated."""

    text: str = field(default="")
    original_message: str = field(default="")
    user_name: str = field(default="")


# =============================================================================
# Game Events
# =============================================================================


@dataclass(frozen=True)
class HouseDesignedEvent(DomainEvent):
    """Event raised when the LLM has designed a house to build."""

    name: str = ""
    concept: str = ""


@dataclass(frozen=True)
class GoalSetEvent(DomainEvent):
    """Event raised when the LLM sets a new goal."""

    goal_type: str = ""
    reason: str = ""


@dataclass(frozen=True)
class GameActionExecutedEvent(DomainEvent):
    """Event raised after the agent executed one action in the game."""

    action_id: str = ""
    ok: bool = True
    result: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class HouseCompletedEvent(DomainEvent):
    """Event raised when every block of the house is in place."""

    name: str = ""
