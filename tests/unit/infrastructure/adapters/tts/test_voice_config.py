"""Tests for Style-Bert-VITS2 voice configuration."""

import pytest

from ailoveshen.infrastructure.adapters.tts.voice_config import VoiceConfig


class TestVoiceConfig:
    """Tests for VoiceConfig value object."""

    def test_create_with_defaults(self):
        """Test creating config with defaults."""
        config = VoiceConfig()
        assert config.model_name == "default"
        assert config.speaker_id == 0
        assert config.language == "JP"
        assert config.sdp_ratio == 0.2
        assert config.noise == 0.6
        assert config.noisew == 0.8
        assert config.length == 1.0

    def test_create_with_custom_values(self):
        """Test creating config with custom values."""
        config = VoiceConfig(
            model_name="my_model",
            speaker_id=1,
            language="EN",
            sdp_ratio=0.3,
        )
        assert config.model_name == "my_model"
        assert config.speaker_id == 1
        assert config.language == "EN"
        assert config.sdp_ratio == 0.3

    def test_negative_speaker_id_raises(self):
        """Test that negative speaker_id raises ValueError."""
        with pytest.raises(ValueError, match="speaker_id must be non-negative"):
            VoiceConfig(speaker_id=-1)

    def test_is_immutable(self):
        """Test that VoiceConfig is immutable."""
        config = VoiceConfig()
        with pytest.raises(AttributeError):
            config.model_name = "modified"  # type: ignore
