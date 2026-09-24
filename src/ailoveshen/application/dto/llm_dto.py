"""LLM-related DTOs (Data Transfer Objects)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ailoveshen.domain.value_objects import EmotionState


@dataclass
class GenerateCommentaryRequest:
    """Input DTO for the generate commentary use case."""

    emotion_state: EmotionState = field(default_factory=EmotionState)
    recent_events: list[str] = field(default_factory=list)
    game_state_summary: Optional[str] = None  # None until game integration exists


@dataclass
class GenerateCommentaryResponse:
    """Output DTO for the generate commentary use case."""

    success: bool
    text: str = ""
    error: Optional[str] = None

    @classmethod
    def ok(cls, text: str) -> GenerateCommentaryResponse:
        """Create a response; empty text means the model produced nothing usable."""
        return cls(success=bool(text), text=text)

    @classmethod
    def error_response(cls, error: str) -> GenerateCommentaryResponse:
        """Create an error response."""
        return cls(success=False, error=error)


@dataclass
class GenerateResponseRequest:
    """Input DTO for the generate chat response use case."""

    user_name: str
    message: str
    user_id: Optional[str] = None
    emotion_state: EmotionState = field(default_factory=EmotionState)


@dataclass
class GenerateResponseResponse:
    """Output DTO for the generate chat response use case."""

    success: bool
    original_message: str
    user_name: str
    text: str = ""
    error: Optional[str] = None

    @classmethod
    def ok(cls, text: str, original_message: str, user_name: str) -> GenerateResponseResponse:
        """Create a response; empty text means the model produced nothing usable."""
        return cls(
            success=bool(text),
            text=text,
            original_message=original_message,
            user_name=user_name,
        )

    @classmethod
    def error_response(
        cls,
        error: str,
        original_message: str,
        user_name: str,
    ) -> GenerateResponseResponse:
        """Create an error response."""
        return cls(
            success=False,
            error=error,
            original_message=original_message,
            user_name=user_name,
        )
