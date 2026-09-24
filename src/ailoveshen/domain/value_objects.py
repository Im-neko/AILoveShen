"""Value objects for Domain layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Optional


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


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
    emotion: EmotionState = field(default_factory=EmotionState)
    source: str = "unknown"  # "commentary" | "response" | "game_event"

    def should_interrupt(self) -> bool:
        """Check if this request should interrupt current speech."""
        return self.priority >= SpeechPriority.INTERRUPT


class SpeechStatus(str, Enum):
    """Speech playback status."""

    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    PLAYING = "playing"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass(frozen=True)
class SpeechResult:
    """
    Value object representing speech synthesis/playback result.

    Immutable record of what happened with a speech request.
    """

    request_text: str
    status: SpeechStatus
    audio_duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    completed_at: datetime = field(default_factory=_utc_now)

    @classmethod
    def queued(cls, text: str) -> SpeechResult:
        """Create a queued result."""
        return cls(
            request_text=text,
            status=SpeechStatus.QUEUED,
        )

    @classmethod
    def completed(cls, text: str, duration_ms: int) -> SpeechResult:
        """Create a completed result."""
        return cls(
            request_text=text,
            status=SpeechStatus.COMPLETED,
            audio_duration_ms=duration_ms,
        )

    @classmethod
    def interrupted(cls, text: str) -> SpeechResult:
        """Create an interrupted result."""
        return cls(
            request_text=text,
            status=SpeechStatus.INTERRUPTED,
        )

    @classmethod
    def failed(cls, text: str, error: str) -> SpeechResult:
        """Create a failed result."""
        return cls(
            request_text=text,
            status=SpeechStatus.FAILED,
            error_message=error,
        )

    @property
    def is_success(self) -> bool:
        """Check if the speech completed successfully."""
        return self.status == SpeechStatus.COMPLETED


# =============================================================================
# Conversation Value Objects
# =============================================================================


class MessageRole(str, Enum):
    """Who sent a conversation message."""

    VIEWER = "viewer"
    STREAMER = "streamer"


class MessageType(str, Enum):
    """What kind of utterance a conversation message is."""

    CHAT = "chat"  # Viewer chat comment
    COMMENTARY = "commentary"  # Streamer's game commentary / thoughts
    RESPONSE = "response"  # Streamer's reply to a viewer


@dataclass(frozen=True)
class ConversationMessage:
    """
    A single message in the stream conversation.

    Raises:
        ValueError: If content is empty.
    """

    role: MessageRole
    message_type: MessageType
    content: str
    speaker_name: str = ""
    speaker_id: Optional[str] = None
    timestamp: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        """Validate content."""
        if not self.content.strip():
            raise ValueError("content must not be empty")

    @classmethod
    def from_viewer(
        cls,
        content: str,
        user_name: str,
        user_id: Optional[str] = None,
    ) -> ConversationMessage:
        """Create a chat message sent by a viewer."""
        return cls(
            role=MessageRole.VIEWER,
            message_type=MessageType.CHAT,
            content=content,
            speaker_name=user_name,
            speaker_id=user_id,
        )

    @classmethod
    def from_streamer(cls, content: str, message_type: MessageType) -> ConversationMessage:
        """Create a message spoken by the AI streamer."""
        return cls(
            role=MessageRole.STREAMER,
            message_type=message_type,
            content=content,
        )


@dataclass(frozen=True)
class CharacterProfile:
    """
    The AI streamer's character.

    Raises:
        ValueError: If name is empty.
    """

    name: str = "AILoveShen"
    description: str = "明るく元気なAI配信者"
    speech_style: str = "フレンドリーで親しみやすい"
    first_person: str = "私"
    sentence_endings: tuple[str, ...] = ("だよ", "だね", "かな", "！")
    personality_traits: tuple[str, ...] = (
        "好奇心旺盛",
        "ポジティブ",
        "ちょっとおっちょこちょい",
        "視聴者思い",
    )

    def __post_init__(self) -> None:
        """Validate name."""
        if not self.name.strip():
            raise ValueError("name must not be empty")


@dataclass(frozen=True)
class GenerationContext:
    """
    Everything the streamer knows when generating an utterance.

    game_state_summary is optional until the game integration (Phase 6)
    provides a structured game state.
    """

    emotion_state: EmotionState = field(default_factory=EmotionState)
    game_state_summary: Optional[str] = None
    recent_events: tuple[str, ...] = ()
    recent_messages: tuple[ConversationMessage, ...] = ()


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
