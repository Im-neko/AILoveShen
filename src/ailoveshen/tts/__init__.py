"""TTS (Text-to-Speech) module for AILoveShen."""

from __future__ import annotations

from typing import TYPE_CHECKING

# Domain layer imports (lightweight, no external dependencies)
from ailoveshen.tts.domain.value_objects import SpeechResult, SpeechStatus, VoiceConfig
from ailoveshen.tts.domain.events import (
    SpeechStartedEvent,
    SpeechCompletedEvent,
    SpeechQueuedEvent,
)
from ailoveshen.tts.domain.services.emotion_style_service import EmotionStyleService

# Lazy imports for heavy dependencies (sounddevice, httpx)
if TYPE_CHECKING:
    from ailoveshen.tts.presentation.services.tts_service import TTSService
    from ailoveshen.tts.factory import create_tts_service, create_and_connect_tts_service


def __getattr__(name: str):
    """Lazy load modules with heavy dependencies."""
    if name == "TTSService":
        from ailoveshen.tts.presentation.services.tts_service import TTSService
        return TTSService
    if name == "create_tts_service":
        from ailoveshen.tts.factory import create_tts_service
        return create_tts_service
    if name == "create_and_connect_tts_service":
        from ailoveshen.tts.factory import create_and_connect_tts_service
        return create_and_connect_tts_service
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Domain
    "SpeechResult",
    "SpeechStatus",
    "VoiceConfig",
    "SpeechStartedEvent",
    "SpeechCompletedEvent",
    "SpeechQueuedEvent",
    "EmotionStyleService",
    # Presentation (lazy loaded)
    "TTSService",
    # Factory (lazy loaded)
    "create_tts_service",
    "create_and_connect_tts_service",
]
