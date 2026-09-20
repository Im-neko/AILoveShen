"""Domain service for emotion to style mapping."""

from __future__ import annotations

from typing import Dict, Optional

from ailoveshen.core.domain.value_objects import EmotionState, EmotionType


class EmotionStyleService:
    """
    Domain service that maps emotion states to TTS voice styles.

    This is a domain service because:
    - It encapsulates domain logic (emotion -> style mapping)
    - It operates on domain value objects
    - It doesn't depend on external services
    """

    # Default mapping from EmotionType to Style-Bert-VITS2 style names
    DEFAULT_STYLE_MAP: Dict[EmotionType, str] = {
        EmotionType.NEUTRAL: "Neutral",
        EmotionType.HAPPY: "Happy",
        EmotionType.SAD: "Sad",
        EmotionType.ANGRY: "Angry",
        EmotionType.SURPRISED: "Surprised",
        EmotionType.SCARED: "Sad",  # Fallback - no direct match
        EmotionType.EXCITED: "Happy",  # Fallback - map to similar
    }

    def __init__(
        self,
        style_map: Optional[Dict[EmotionType, str]] = None,
        default_style: str = "Neutral",
    ) -> None:
        """
        Initialize with optional custom style mapping.

        Args:
            style_map: Custom emotion to style mapping. Merges with defaults.
            default_style: Fallback style when emotion type not found.
        """
        self._style_map = self.DEFAULT_STYLE_MAP.copy()
        if style_map:
            self._style_map.update(style_map)
        self._default_style = default_style

    def get_style_for_emotion(self, emotion_state: EmotionState) -> str:
        """
        Get the appropriate TTS style for the given emotion state.

        Args:
            emotion_state: Current emotion state

        Returns:
            TTS style name (e.g., "Happy", "Sad", "Neutral")
        """
        return self._style_map.get(emotion_state.primary, self._default_style)

    def get_style_weight(self, emotion_state: EmotionState) -> float:
        """
        Get style weight based on emotion intensity.

        Higher emotion intensity = stronger style application.
        Maps intensity (0.0-1.0) to style weight (0.0-10.0).

        Args:
            emotion_state: Current emotion state

        Returns:
            Style weight for TTS (0.0 - 10.0)
        """
        return emotion_state.intensity * 10.0

    def get_available_styles(self) -> list[str]:
        """
        Get list of all available styles.

        Returns:
            Unique style names from the mapping.
        """
        return list(set(self._style_map.values()))
