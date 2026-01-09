"""Value objects and domain events for Domain layer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum


# =============================================================================
# Domain Events
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
# Emotion Value Objects
# =============================================================================


class EmotionType(str, Enum):
    """Types of emotions the AI character can express."""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    SCARED = "scared"
    EXCITED = "excited"


@dataclass(frozen=True)
class EmotionState:
    """
    Emotion state value object.

    Value objects are immutable and compared by their attributes.

    Raises:
        ValueError: If intensity is not between 0.0 and 1.0.
    """

    primary: EmotionType = EmotionType.NEUTRAL
    intensity: float = 0.5  # 0.0 - 1.0

    def __post_init__(self) -> None:
        """Validate intensity range."""
        if not 0.0 <= self.intensity <= 1.0:
            raise ValueError(
                f"intensity must be between 0.0 and 1.0, got {self.intensity}"
            )

    def with_intensity(self, new_intensity: float) -> EmotionState:
        """Create a new EmotionState with different intensity (clamped)."""
        clamped = max(0.0, min(1.0, new_intensity))
        return EmotionState(primary=self.primary, intensity=clamped)

    def decay(self, rate: float = 0.1) -> EmotionState:
        """
        Decay emotion intensity over time.

        Returns neutral if intensity drops below threshold.
        """
        new_intensity = max(0.0, self.intensity - rate)
        if new_intensity < 0.3:
            return EmotionState(EmotionType.NEUTRAL, 0.5)
        return EmotionState(primary=self.primary, intensity=new_intensity)


# =============================================================================
# Speech Value Objects
# =============================================================================


class SpeechPriority(IntEnum):
    """Priority levels for speech requests."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    INTERRUPT = 3


@dataclass(frozen=True)
class SpeechRequest:
    """
    Speech request value object.

    Represents a request to synthesize and play speech.
    """

    text: str
    priority: SpeechPriority = SpeechPriority.NORMAL
    style: str = "Neutral"
    source: str = "unknown"  # "commentary" | "response" | "game_event"

    def should_interrupt(self) -> bool:
        """Check if this request should interrupt current speech."""
        return self.priority >= SpeechPriority.INTERRUPT


# =============================================================================
# Position Value Objects
# =============================================================================


@dataclass(frozen=True)
class Position:
    """3D position in the game world."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def distance_to(self, other: Position) -> float:
        """Calculate Euclidean distance to another position."""
        return (
            (self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2
        ) ** 0.5

    def __str__(self) -> str:
        return f"({self.x:.1f}, {self.y:.1f}, {self.z:.1f})"


@dataclass(frozen=True)
class Rotation:
    """
    Player rotation (yaw and pitch).

    Raises:
        ValueError: If yaw is not between -180 and 180, or pitch is not between -90 and 90.
    """

    yaw: float = 0.0  # -180 to 180
    pitch: float = 0.0  # -90 to 90

    def __post_init__(self) -> None:
        """Validate rotation ranges."""
        if not -180.0 <= self.yaw <= 180.0:
            raise ValueError(f"yaw must be between -180 and 180, got {self.yaw}")
        if not -90.0 <= self.pitch <= 90.0:
            raise ValueError(f"pitch must be between -90 and 90, got {self.pitch}")

    def __str__(self) -> str:
        return f"(yaw={self.yaw:.1f}, pitch={self.pitch:.1f})"


# =============================================================================
# Filter Result Value Objects
# =============================================================================


@dataclass(frozen=True)
class FilterResult:
    """
    Result of comment filtering.

    Raises:
        ValueError: If score is not between 0.0 and 1.0.
    """

    should_respond: bool
    score: float  # 0.0 - 1.0, how important/relevant the comment is
    reason: str

    def __post_init__(self) -> None:
        """Validate score range."""
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score must be between 0.0 and 1.0, got {self.score}")

    @classmethod
    def accept(cls, score: float, reason: str = "Accepted") -> FilterResult:
        """Create an accepting filter result."""
        return cls(should_respond=True, score=score, reason=reason)

    @classmethod
    def reject(cls, score: float, reason: str = "Rejected") -> FilterResult:
        """Create a rejecting filter result."""
        return cls(should_respond=False, score=score, reason=reason)
