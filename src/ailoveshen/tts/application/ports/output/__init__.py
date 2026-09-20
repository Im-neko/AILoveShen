"""TTS Output ports (driven adapters interfaces)."""

from __future__ import annotations

from ailoveshen.tts.application.ports.output.speech_synthesizer import (
    ISpeechSynthesizer,
)
from ailoveshen.tts.application.ports.output.audio_player import IAudioPlayer

__all__ = ["ISpeechSynthesizer", "IAudioPlayer"]
