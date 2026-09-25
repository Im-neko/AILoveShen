"""Conversation エンティティのテスト。"""

import pytest

from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.value_objects import MessageRole, MessageType


class TestConversation:
    """Conversation エンティティのテスト。"""

    def test_add_viewer_message(self):
        """視聴者のコメントを足す。"""
        conversation = Conversation()
        message = conversation.add_viewer_message("こんにちは", "neko", "123")

        assert message.role == MessageRole.VIEWER
        assert message.message_type == MessageType.CHAT
        assert message.speaker_name == "neko"
        assert message.speaker_id == "123"
        assert len(conversation) == 1

    def test_add_streamer_message(self):
        """配信者の発話を足す。"""
        conversation = Conversation()
        message = conversation.add_streamer_message("洞窟だ！", MessageType.COMMENTARY)

        assert message.role == MessageRole.STREAMER
        assert message.message_type == MessageType.COMMENTARY
        assert len(conversation) == 1

    def test_respects_max_history(self):
        """最新の max_history 件だけを残す。"""
        conversation = Conversation(max_history=5)
        for i in range(10):
            conversation.add_viewer_message(f"Message {i}", "user")

        assert len(conversation) == 5
        assert conversation.recent_messages()[0].content == "Message 5"

    def test_invalid_max_history_raises(self):
        """max_history が正でなければ ValueError。"""
        with pytest.raises(ValueError, match="max_history must be positive"):
            Conversation(max_history=0)

    def test_recent_messages_limit(self):
        """recent_messages は最新のメッセージを古い順に返す。"""
        conversation = Conversation()
        for i in range(5):
            conversation.add_viewer_message(f"m{i}", "user")

        recent = conversation.recent_messages(limit=2)
        assert [m.content for m in recent] == ["m3", "m4"]
        assert conversation.recent_messages(limit=0) == ()

    def test_recent_viewer_messages(self):
        """recent_viewer_messages は配信者の発話を含まない。"""
        conversation = Conversation()
        conversation.add_viewer_message("chat1", "a")
        conversation.add_streamer_message("commentary", MessageType.COMMENTARY)
        conversation.add_viewer_message("chat2", "b")

        viewer = conversation.recent_viewer_messages()
        assert [m.content for m in viewer] == ["chat1", "chat2"]

    def test_clear(self):
        """clear はメッセージを全部忘れる。"""
        conversation = Conversation()
        conversation.add_viewer_message("hello", "user")
        conversation.clear()
        assert len(conversation) == 0

    def test_updated_at_changes_on_add(self):
        """メッセージを足すと updated_at が変わる。"""
        conversation = Conversation()
        before = conversation.updated_at
        conversation.add_viewer_message("hello", "user")
        assert conversation.updated_at >= before

    def test_equality_by_id(self):
        """会話が等しいのは id が同じときだけ。"""
        a = Conversation()
        b = Conversation(id=a.id)
        a.add_viewer_message("hello", "user")

        assert a == b
        assert a != Conversation()
        assert hash(a) == hash(b)
