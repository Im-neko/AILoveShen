"""Output ports (driven adapter interfaces)."""

from __future__ import annotations

from ailoveshen.application.ports.output.audio_player import IAudioPlayer
from ailoveshen.application.ports.output.event_publisher import IEventPublisher, IEventSubscriber
from ailoveshen.application.ports.output.speech_synthesizer import ISpeechSynthesizer

__all__ = ["IAudioPlayer", "IEventPublisher", "IEventSubscriber", "ISpeechSynthesizer"]
