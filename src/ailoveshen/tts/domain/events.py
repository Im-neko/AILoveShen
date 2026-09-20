"""TTS domain events."""

from __future__ import annotations

from dataclasses import dataclass, field

from ailoveshen.core.domain.value_objects import DomainEvent


@dataclass(frozen=True)
class SpeechStartedEvent(DomainEvent):
    """
    Domain event raised when speech synthesis/playback begins.

    Used to notify other components that the AI is speaking.
    """

    text: str = field(default="")
    source: str = field(default="unknown")  # e.g., "commentary", "chat_response"
    style: str = field(default="Neutral")


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
