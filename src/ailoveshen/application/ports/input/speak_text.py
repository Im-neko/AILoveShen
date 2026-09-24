"""Speak text input port (use case interface)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.speech_dto import (
    SpeakTextRequest,
    SpeakTextResponse,
)


class ISpeakText(ABC):
    """
    Input port for text-to-speech use case.

    Presentation layer uses this interface to request speech synthesis.
    This follows the Dependency Inversion Principle - high-level modules
    depend on abstractions, not concrete implementations.
    """

    @abstractmethod
    async def execute(self, request: SpeakTextRequest) -> SpeakTextResponse:
        """
        Execute speak text use case.

        This method:
        1. Determines the emotion to speak with (current emotion or override)
        2. Synthesizes speech using the configured TTS service
        3. Plays the audio through the configured audio player
        4. Publishes domain events for speech started/completed

        Args:
            request: Speech request parameters

        Returns:
            Speech result containing success status and any error information.
        """
        ...
