"""Application use cases."""

from __future__ import annotations

from ailoveshen.application.use_cases.generate_commentary import GenerateCommentaryUseCase
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.application.use_cases.speak_text import SpeakTextUseCase

__all__ = ["GenerateCommentaryUseCase", "GenerateResponseUseCase", "SpeakTextUseCase"]
