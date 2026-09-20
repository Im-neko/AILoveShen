"""Speech synthesizer output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class ISpeechSynthesizer(ABC):
    """
    Output port for speech synthesis.

    This interface is defined in the Application layer.
    Infrastructure adapters implement this interface.
    """

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        style: str = "Neutral",
        speaker_id: int = 0,
        language: str = "JP",
    ) -> bytes:
        """
        Synthesize speech from text.

        Args:
            text: Text to synthesize
            style: Voice style (e.g., "Neutral", "Happy", "Sad")
            speaker_id: Speaker ID for multi-speaker models
            language: Language code ("JP", "EN", "ZH")

        Returns:
            Audio data as bytes (WAV format)

        Raises:
            SynthesisError: If synthesis fails
        """
        ...

    @abstractmethod
    async def get_available_styles(self) -> List[str]:
        """
        Get list of available voice styles.

        Returns:
            List of style names available for the current model.
        """
        ...

    @abstractmethod
    async def connect(self) -> None:
        """
        Establish connection to synthesis service.

        Should be called before synthesis operations.

        Raises:
            ConnectionError: If connection fails
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """
        Close connection to synthesis service.

        Should be called during cleanup.
        """
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """
        Check if connected to synthesis service.

        Returns:
            True if connected and ready for synthesis.
        """
        ...
