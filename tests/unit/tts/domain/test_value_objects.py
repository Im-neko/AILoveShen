"""Tests for TTS domain value objects."""

import pytest
from datetime import timezone

from ailoveshen.tts.domain.value_objects import (
    SpeechResult,
    SpeechStatus,
    VoiceConfig,
)


class TestSpeechStatus:
    """Tests for SpeechStatus enum."""

    def test_status_values(self):
        """Test all status values exist."""
        assert SpeechStatus.QUEUED == "queued"
        assert SpeechStatus.SYNTHESIZING == "synthesizing"
        assert SpeechStatus.PLAYING == "playing"
        assert SpeechStatus.COMPLETED == "completed"
        assert SpeechStatus.INTERRUPTED == "interrupted"
        assert SpeechStatus.FAILED == "failed"

    def test_status_is_string_enum(self):
        """Test status can be used as string."""
        assert str(SpeechStatus.COMPLETED) == "SpeechStatus.COMPLETED"
        assert SpeechStatus.COMPLETED.value == "completed"


class TestSpeechResult:
    """Tests for SpeechResult value object."""

    def test_create_basic_result(self):
        """Test creating a basic result."""
        result = SpeechResult(
            request_text="hello",
            status=SpeechStatus.COMPLETED,
        )
        assert result.request_text == "hello"
        assert result.status == SpeechStatus.COMPLETED
        assert result.audio_duration_ms is None
        assert result.error_message is None

    def test_completed_at_uses_utc(self):
        """Test completed_at is UTC timezone aware."""
        result = SpeechResult(
            request_text="test",
            status=SpeechStatus.COMPLETED,
        )
        assert result.completed_at.tzinfo == timezone.utc

    def test_completed_factory(self):
        """Test completed factory method."""
        result = SpeechResult.completed("hello", 1000)
        assert result.request_text == "hello"
        assert result.status == SpeechStatus.COMPLETED
        assert result.audio_duration_ms == 1000
        assert result.is_success is True

    def test_interrupted_factory(self):
        """Test interrupted factory method."""
        result = SpeechResult.interrupted("hello")
        assert result.status == SpeechStatus.INTERRUPTED
        assert result.is_success is False

    def test_failed_factory(self):
        """Test failed factory method."""
        result = SpeechResult.failed("hello", "Connection error")
        assert result.status == SpeechStatus.FAILED
        assert result.error_message == "Connection error"
        assert result.is_success is False

    def test_queued_factory(self):
        """Test queued factory method."""
        result = SpeechResult.queued("hello")
        assert result.status == SpeechStatus.QUEUED
        assert result.is_success is False

    def test_is_immutable(self):
        """Test that SpeechResult is immutable."""
        result = SpeechResult.completed("test", 500)
        with pytest.raises(AttributeError):
            result.request_text = "modified"  # type: ignore


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
