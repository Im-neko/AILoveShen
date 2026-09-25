"""イベントバスの単体テスト。"""

from dataclasses import dataclass

import pytest

from ailoveshen.domain.events import DomainEvent
from ailoveshen.infrastructure.events import AsyncEventBus, event_handler


@dataclass(frozen=True)
class TestEvent(DomainEvent):
    """単体テスト用のイベント。"""

    message: str = ""


@dataclass(frozen=True)
class OtherEvent(DomainEvent):
    """もう 1 つのテスト用のイベント。"""

    value: int = 0


class TestAsyncEventBus:
    """AsyncEventBus のテスト。"""

    @pytest.fixture
    def event_bus(self):
        """テストごとに新しいイベントバスを作る。"""
        return AsyncEventBus()

    @pytest.mark.asyncio
    async def test_publish_to_subscriber(self, event_bus):
        """イベントが購読者に届く。"""
        received_events = []

        async def handler(event: TestEvent):
            received_events.append(event)

        event_bus.subscribe(TestEvent, handler)
        event = TestEvent(message="Hello")
        await event_bus.publish(event)

        assert len(received_events) == 1
        assert received_events[0].message == "Hello"

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(self, event_bus):
        """イベントがすべての購読者に届く。"""
        received_1 = []
        received_2 = []

        async def handler1(event: TestEvent):
            received_1.append(event)

        async def handler2(event: TestEvent):
            received_2.append(event)

        event_bus.subscribe(TestEvent, handler1)
        event_bus.subscribe(TestEvent, handler2)

        event = TestEvent(message="Test")
        await event_bus.publish(event)

        assert len(received_1) == 1
        assert len(received_2) == 1

    @pytest.mark.asyncio
    async def test_subscribers_receive_correct_type_only(self, event_bus):
        """購読者には自分のイベントの種類だけが届く。"""
        test_events = []
        other_events = []

        async def test_handler(event: TestEvent):
            test_events.append(event)

        async def other_handler(event: OtherEvent):
            other_events.append(event)

        event_bus.subscribe(TestEvent, test_handler)
        event_bus.subscribe(OtherEvent, other_handler)

        await event_bus.publish(TestEvent(message="Test"))
        await event_bus.publish(OtherEvent(value=42))

        assert len(test_events) == 1
        assert len(other_events) == 1
        assert test_events[0].message == "Test"
        assert other_events[0].value == 42

    @pytest.mark.asyncio
    async def test_publish_with_no_subscribers(self, event_bus):
        """購読者がいなくても発行はエラーにならない。"""
        event = TestEvent(message="Nobody listening")
        # 例外にならない
        await event_bus.publish(event)

    @pytest.mark.asyncio
    async def test_unsubscribe(self, event_bus):
        """ハンドラの購読をやめる。"""
        received = []

        async def handler(event: TestEvent):
            received.append(event)

        event_bus.subscribe(TestEvent, handler)
        await event_bus.publish(TestEvent(message="First"))

        result = event_bus.unsubscribe(TestEvent, handler)
        assert result is True

        await event_bus.publish(TestEvent(message="Second"))

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_returns_false_if_not_found(self, event_bus):
        """ハンドラがなければ unsubscribe は False を返す。"""

        async def handler(event: TestEvent):
            pass

        result = event_bus.unsubscribe(TestEvent, handler)
        assert result is False

    @pytest.mark.asyncio
    async def test_subscribe_returns_unsubscribe_function(self, event_bus):
        """subscribe は使える購読解除の関数を返す。"""
        received = []

        async def handler(event: TestEvent):
            received.append(event)

        unsubscribe = event_bus.subscribe(TestEvent, handler)
        await event_bus.publish(TestEvent(message="First"))

        unsubscribe()
        await event_bus.publish(TestEvent(message="Second"))

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_clear_removes_all_subscriptions(self, event_bus):
        """clear は購読を全部消す。"""

        async def handler1(event: TestEvent):
            pass

        async def handler2(event: OtherEvent):
            pass

        event_bus.subscribe(TestEvent, handler1)
        event_bus.subscribe(OtherEvent, handler2)

        assert event_bus.handler_count() == 2

        event_bus.clear()

        assert event_bus.handler_count() == 0

    def test_handler_count(self, event_bus):
        """handler_count は正しい数を返す。"""

        async def handler1(event: TestEvent):
            pass

        async def handler2(event: TestEvent):
            pass

        async def handler3(event: OtherEvent):
            pass

        assert event_bus.handler_count() == 0

        event_bus.subscribe(TestEvent, handler1)
        event_bus.subscribe(TestEvent, handler2)
        event_bus.subscribe(OtherEvent, handler3)

        assert event_bus.handler_count() == 3
        assert event_bus.handler_count(TestEvent) == 2
        assert event_bus.handler_count(OtherEvent) == 1

    @pytest.mark.asyncio
    async def test_handler_error_isolation(self, event_bus):
        """1 つのハンドラのエラーはほかに影響しない。"""
        successful_calls = []

        async def failing_handler(event: TestEvent):
            raise ValueError("Handler error")

        async def success_handler(event: TestEvent):
            successful_calls.append(event)

        event_bus.subscribe(TestEvent, failing_handler)
        event_bus.subscribe(TestEvent, success_handler)

        # 例外にならず、エラーはそこで閉じる
        await event_bus.publish(TestEvent(message="Test"))

        assert len(successful_calls) == 1

    @pytest.mark.asyncio
    async def test_publish_all(self, event_bus):
        """複数のイベントを発行する。"""
        received = []

        async def handler(event: TestEvent):
            received.append(event)

        event_bus.subscribe(TestEvent, handler)

        events = [
            TestEvent(message="First"),
            TestEvent(message="Second"),
            TestEvent(message="Third"),
        ]
        await event_bus.publish_all(events)

        assert len(received) == 3


class TestEventHandlerDecorator:
    """event_handler デコレータのテスト。"""

    def test_decorator_sets_event_type(self):
        """デコレータは _event_type 属性を設定する。"""

        @event_handler(TestEvent)
        async def handler(event: TestEvent):
            pass

        assert handler._event_type == TestEvent

    def test_decorator_preserves_function(self):
        """デコレータは関数の振る舞いを変えない。"""

        @event_handler(TestEvent)
        async def handler(event: TestEvent):
            return "result"

        # 関数はそのまま呼べる
        import asyncio

        result = asyncio.run(handler(TestEvent()))
        assert result == "result"
