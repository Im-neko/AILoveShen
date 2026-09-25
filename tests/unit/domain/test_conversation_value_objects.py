"""Tests for conversation value objects."""

from datetime import timezone

import pytest

from ailoveshen.domain.value_objects import (
    CharacterProfile,
    ConversationMessage,
    EmotionState,
    GenerationContext,
    MessageRole,
    MessageType,
)


class TestConversationMessage:
    """Tests for ConversationMessage value object."""

    def test_from_viewer(self):
        """Test viewer message factory."""
        message = ConversationMessage.from_viewer("hello", "neko", "42")
        assert message.role == MessageRole.VIEWER
        assert message.message_type == MessageType.CHAT
        assert message.speaker_name == "neko"
        assert message.speaker_id == "42"

    def test_from_streamer(self):
        """Test streamer message factory."""
        message = ConversationMessage.from_streamer("やったー", MessageType.RESPONSE)
        assert message.role == MessageRole.STREAMER
        assert message.message_type == MessageType.RESPONSE
        assert message.speaker_name == ""

    def test_timestamp_is_utc(self):
        """Test timestamp is timezone-aware UTC."""
        message = ConversationMessage.from_viewer("hello", "neko")
        assert message.timestamp.tzinfo == timezone.utc

    def test_empty_content_raises(self):
        """Test that empty content raises ValueError."""
        with pytest.raises(ValueError, match="content must not be empty"):
            ConversationMessage.from_viewer("   ", "neko")

    def test_is_immutable(self):
        """Test that ConversationMessage is immutable."""
        message = ConversationMessage.from_viewer("hello", "neko")
        with pytest.raises(AttributeError):
            message.content = "changed"  # type: ignore


class TestCharacterProfile:
    """Tests for CharacterProfile value object."""

    def test_defaults(self):
        """Test default character profile."""
        profile = CharacterProfile()
        assert profile.name == "AILoveShen"
        assert profile.first_person == "私"
        assert "だよ" in profile.sentence_endings

    def test_empty_name_raises(self):
        """Test that empty name raises ValueError."""
        with pytest.raises(ValueError, match="name must not be empty"):
            CharacterProfile(name="")


class TestGenerationContext:
    """Tests for GenerationContext value object."""

    def test_defaults(self):
        """Test default generation context."""
        context = GenerationContext()
        assert context.emotion_state == EmotionState()
        assert context.activity is None
        assert context.recent_events == ()
        assert context.recent_messages == ()
