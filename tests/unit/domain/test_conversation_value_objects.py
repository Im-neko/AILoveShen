"""会話の値オブジェクトのテスト。"""

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
    """ConversationMessage 値オブジェクトのテスト。"""

    def test_from_viewer(self):
        """視聴者のメッセージのファクトリ。"""
        message = ConversationMessage.from_viewer("hello", "neko", "42")
        assert message.role == MessageRole.VIEWER
        assert message.message_type == MessageType.CHAT
        assert message.speaker_name == "neko"
        assert message.speaker_id == "42"

    def test_from_streamer(self):
        """配信者のメッセージのファクトリ。"""
        message = ConversationMessage.from_streamer("やったー", MessageType.RESPONSE)
        assert message.role == MessageRole.STREAMER
        assert message.message_type == MessageType.RESPONSE
        assert message.speaker_name == ""

    def test_timestamp_is_utc(self):
        """タイムスタンプはタイムゾーンつきの UTC。"""
        message = ConversationMessage.from_viewer("hello", "neko")
        assert message.timestamp.tzinfo == timezone.utc

    def test_empty_content_raises(self):
        """内容が空なら ValueError。"""
        with pytest.raises(ValueError, match="content must not be empty"):
            ConversationMessage.from_viewer("   ", "neko")

    def test_is_immutable(self):
        """ConversationMessage は変更できない。"""
        message = ConversationMessage.from_viewer("hello", "neko")
        with pytest.raises(AttributeError):
            message.content = "changed"  # type: ignore


class TestCharacterProfile:
    """CharacterProfile 値オブジェクトのテスト。"""

    def test_defaults(self):
        """キャラクタープロフィールの既定値。"""
        profile = CharacterProfile()
        assert profile.name == "AILoveShen"
        assert profile.first_person == "私"
        assert "だよ" in profile.sentence_endings

    def test_empty_name_raises(self):
        """名前が空なら ValueError。"""
        with pytest.raises(ValueError, match="name must not be empty"):
            CharacterProfile(name="")


class TestGenerationContext:
    """GenerationContext 値オブジェクトのテスト。"""

    def test_defaults(self):
        """生成コンテキストの既定値。"""
        context = GenerationContext()
        assert context.emotion_state == EmotionState()
        assert context.activity is None
        assert context.recent_events == ()
        assert context.recent_messages == ()
