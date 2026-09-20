"""Tests for EmotionStyleService domain service."""

import pytest

from ailoveshen.core.domain.value_objects import EmotionState, EmotionType
from ailoveshen.tts.domain.services.emotion_style_service import EmotionStyleService


class TestEmotionStyleService:
    """Tests for EmotionStyleService."""

    @pytest.fixture
    def service(self) -> EmotionStyleService:
        """Create a default EmotionStyleService."""
        return EmotionStyleService()

    def test_get_style_for_neutral_emotion(self, service: EmotionStyleService):
        """Test style for neutral emotion."""
        emotion = EmotionState(primary=EmotionType.NEUTRAL, intensity=0.5)
        style = service.get_style_for_emotion(emotion)
        assert style == "Neutral"

    def test_get_style_for_happy_emotion(self, service: EmotionStyleService):
        """Test style for happy emotion."""
        emotion = EmotionState(primary=EmotionType.HAPPY, intensity=0.8)
        style = service.get_style_for_emotion(emotion)
        assert style == "Happy"

    def test_get_style_for_sad_emotion(self, service: EmotionStyleService):
        """Test style for sad emotion."""
        emotion = EmotionState(primary=EmotionType.SAD, intensity=0.7)
        style = service.get_style_for_emotion(emotion)
        assert style == "Sad"

    def test_get_style_for_angry_emotion(self, service: EmotionStyleService):
        """Test style for angry emotion."""
        emotion = EmotionState(primary=EmotionType.ANGRY, intensity=0.9)
        style = service.get_style_for_emotion(emotion)
        assert style == "Angry"

    def test_get_style_for_surprised_emotion(self, service: EmotionStyleService):
        """Test style for surprised emotion."""
        emotion = EmotionState(primary=EmotionType.SURPRISED, intensity=0.6)
        style = service.get_style_for_emotion(emotion)
        assert style == "Surprised"

    def test_get_style_for_scared_fallback(self, service: EmotionStyleService):
        """Test scared emotion falls back to Sad."""
        emotion = EmotionState(primary=EmotionType.SCARED, intensity=0.7)
        style = service.get_style_for_emotion(emotion)
        assert style == "Sad"

    def test_get_style_for_excited_fallback(self, service: EmotionStyleService):
        """Test excited emotion falls back to Happy."""
        emotion = EmotionState(primary=EmotionType.EXCITED, intensity=0.9)
        style = service.get_style_for_emotion(emotion)
        assert style == "Happy"

    def test_custom_style_map(self):
        """Test custom style mapping."""
        custom_map = {
            EmotionType.NEUTRAL: "Custom_Neutral",
            EmotionType.HAPPY: "Custom_Happy",
        }
        service = EmotionStyleService(style_map=custom_map)

        # Custom mapping should override
        emotion = EmotionState(primary=EmotionType.NEUTRAL)
        assert service.get_style_for_emotion(emotion) == "Custom_Neutral"

        # Non-overridden should use default
        emotion = EmotionState(primary=EmotionType.SAD)
        assert service.get_style_for_emotion(emotion) == "Sad"

    def test_default_style_for_unknown(self):
        """Test default style is used for unknown emotions."""
        service = EmotionStyleService(default_style="MyDefault")
        # Since all EmotionTypes are mapped, we test by providing empty map
        service._style_map = {}
        emotion = EmotionState(primary=EmotionType.NEUTRAL)
        assert service.get_style_for_emotion(emotion) == "MyDefault"

    def test_get_style_weight_low_intensity(self, service: EmotionStyleService):
        """Test style weight for low intensity."""
        emotion = EmotionState(primary=EmotionType.HAPPY, intensity=0.1)
        weight = service.get_style_weight(emotion)
        assert weight == pytest.approx(1.0)

    def test_get_style_weight_high_intensity(self, service: EmotionStyleService):
        """Test style weight for high intensity."""
        emotion = EmotionState(primary=EmotionType.HAPPY, intensity=1.0)
        weight = service.get_style_weight(emotion)
        assert weight == pytest.approx(10.0)

    def test_get_style_weight_mid_intensity(self, service: EmotionStyleService):
        """Test style weight for mid intensity."""
        emotion = EmotionState(primary=EmotionType.NEUTRAL, intensity=0.5)
        weight = service.get_style_weight(emotion)
        assert weight == pytest.approx(5.0)

    def test_get_available_styles(self, service: EmotionStyleService):
        """Test getting available styles."""
        styles = service.get_available_styles()
        # Should have unique styles
        assert "Neutral" in styles
        assert "Happy" in styles
        assert "Sad" in styles
        assert "Angry" in styles
        assert "Surprised" in styles
        # Should not have duplicates
        assert len(styles) == len(set(styles))
