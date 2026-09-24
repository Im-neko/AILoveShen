"""Audio player output port."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Optional


class IAudioPlayer(ABC):
    """
    Output port for audio playback.

    This interface is defined in the Application layer.
    Infrastructure adapters implement this interface.
    """

    @abstractmethod
    async def play(
        self,
        audio_data: bytes,
        interrupt_event: Optional[asyncio.Event] = None,
    ) -> bool:
        """
        Play audio data.

        Args:
            audio_data: Audio data to play (WAV format)
            interrupt_event: Optional event to monitor for interruption.
                           When set, playback should stop immediately.

        Returns:
            True if playback completed normally, False if interrupted.

        Raises:
            AudioPlaybackError: If playback fails due to an error.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """
        Stop current playback immediately.

        Safe to call even if nothing is playing.
        """
        ...

    @abstractmethod
    def is_playing(self) -> bool:
        """
        Check if audio is currently playing.

        Returns:
            True if audio is currently playing.
        """
        ...

    @abstractmethod
    def get_duration_ms(self, audio_data: bytes) -> int:
        """
        Get duration of audio data in milliseconds.

        Args:
            audio_data: Audio data (WAV format)

        Returns:
            Duration in milliseconds.

        Raises:
            AudioPlaybackError: If audio data is invalid.
        """
        ...
