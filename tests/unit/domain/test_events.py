"""ドメインイベントのテスト。"""

import pytest

from ailoveshen.domain.events import (
    ChatResponseGeneratedEvent,
    CommentaryGeneratedEvent,
    DomainEvent,
)


class TestDomainEvent:
    """DomainEvent 基底クラスのテスト。"""

    def test_event_id_generated(self):
        """イベント ID は自動で作られる。"""
        event1 = DomainEvent()
        event2 = DomainEvent()
        assert event1.event_id != event2.event_id

    def test_occurred_at_set(self):
        """occurred_at が設定される。"""
        event = DomainEvent()
        assert event.occurred_at is not None

    def test_occurred_at_is_utc(self):
        """occurred_at は UTC。"""
        from datetime import timezone

        event = DomainEvent()
        assert event.occurred_at.tzinfo == timezone.utc

    def test_event_type_property(self):
        """event_type はクラス名を返す。"""
        event = DomainEvent()
        assert event.event_type == "DomainEvent"

    def test_immutable(self):
        """DomainEvent は変更できない。"""
        event = DomainEvent()
        with pytest.raises(AttributeError):
            event.event_id = "new-id"


class TestConversationEvents:
    """LLM の生成イベントのテスト。"""

    def test_commentary_generated_event(self):
        """CommentaryGeneratedEvent はテキストを持つ。"""
        event = CommentaryGeneratedEvent(text="洞窟だ！")
        assert event.text == "洞窟だ！"
        assert event.event_type == "CommentaryGeneratedEvent"

    def test_chat_response_generated_event(self):
        """ChatResponseGeneratedEvent は返答と元のコメントを持つ。"""
        event = ChatResponseGeneratedEvent(
            text="nekoさんありがとう！",
            original_message="がんばれ",
            user_name="neko",
        )
        assert event.text == "nekoさんありがとう！"
        assert event.original_message == "がんばれ"
        assert event.user_name == "neko"
