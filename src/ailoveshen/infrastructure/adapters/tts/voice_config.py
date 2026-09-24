"""Style-Bert-VITS2 voice configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceConfig:
    """
    Voice configuration value object.

    Contains all settings needed for TTS synthesis.

    Raises:
        ValueError: If speaker_id is negative.
    """

    model_name: str = "default"
    speaker_id: int = 0
    language: str = "JP"
    sdp_ratio: float = 0.2
    noise: float = 0.6
    noisew: float = 0.8
    length: float = 1.0

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.speaker_id < 0:
            raise ValueError(f"speaker_id must be non-negative, got {self.speaker_id}")
