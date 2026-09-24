"""Application DTOs."""

from __future__ import annotations

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateCommentaryResponse,
    GenerateResponseRequest,
    GenerateResponseResponse,
)
from ailoveshen.application.dto.speech_dto import (
    SpeakTextRequest,
    SpeakTextResponse,
)

__all__ = [
    "GenerateCommentaryRequest",
    "GenerateCommentaryResponse",
    "GenerateResponseRequest",
    "GenerateResponseResponse",
    "SpeakTextRequest",
    "SpeakTextResponse",
]
