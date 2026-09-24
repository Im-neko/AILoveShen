"""Speech-related DTOs (Data Transfer Objects)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.value_objects import EmotionState, SpeechPriority


@dataclass
class SpeakTextRequest:
    """
    Input DTO for speak text use case.

    DTOs are simple data containers for transferring data across layer boundaries.
    They don't contain business logic.
    """

    text: str
    priority: SpeechPriority = SpeechPriority.NORMAL
    emotion: Optional[EmotionState] = None  # None = use current emotion
    source: str = "unknown"  # Identifier for the source (e.g., "commentary", "chat")
    language: str = "JP"
    speaker_id: int = 0


@dataclass
class SpeakTextResponse:
    """
    Output DTO for speak text use case.

    Contains the result of the speech synthesis and playback operation.
    """

    success: bool
    queued: bool = False  # True if added to queue instead of immediate playback
    message: str = ""
    duration_ms: Optional[int] = None  # Audio duration if available
    error: Optional[str] = None

    @classmethod
    def ok(
        cls,
        message: str = "Speech completed",
        duration_ms: Optional[int] = None,
    ) -> SpeakTextResponse:
        """Create a successful response."""
        return cls(
            success=True,
            message=message,
            duration_ms=duration_ms,
        )

    @classmethod
    def interrupted(cls) -> SpeakTextResponse:
        """Create an interrupted response."""
        return cls(
            success=True,
            message="Speech interrupted",
        )

    @classmethod
    def queued_response(cls) -> SpeakTextResponse:
        """Create a queued response."""
        return cls(
            success=True,
            queued=True,
            message="Speech queued",
        )

    @classmethod
    def error_response(cls, error: str) -> SpeakTextResponse:
        """Create an error response."""
        return cls(
            success=False,
            error=error,
            message=f"Speech failed: {error}",
        )
