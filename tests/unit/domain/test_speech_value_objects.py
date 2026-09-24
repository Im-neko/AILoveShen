"""Tests for speech value objects."""

import pytest
from datetime import timezone

from ailoveshen.domain.value_objects import (
    SpeechResult,
    SpeechStatus,
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
