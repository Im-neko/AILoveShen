"""TTS-specific value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


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


@dataclass(frozen=True)
class VoiceConfig:
    """
    Voice configuration value object.

    Contains all settings needed for TTS synthesis.

    Raises:
        ValueError: If speaker_id is negative.
    """

    model_name: str = "default"
    speaker_id: int = 0
    language: str = "JP"
    sdp_ratio: float = 0.2
    noise: float = 0.6
    noisew: float = 0.8
    length: float = 1.0

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.speaker_id < 0:
            raise ValueError(f"speaker_id must be non-negative, got {self.speaker_id}")
