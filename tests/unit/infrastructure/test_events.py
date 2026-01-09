"""Unit tests for event bus."""

from dataclasses import dataclass

import pytest

from ailoveshen.core.domain.value_objects import DomainEvent
from ailoveshen.core.infrastructure.events import AsyncEventBus, event_handler


@dataclass(frozen=True)
class TestEvent(DomainEvent):
    """Test event for unit tests."""

    message: str = ""


@dataclass(frozen=True)
class OtherEvent(DomainEvent):
    """Another test event."""

    value: int = 0


class TestAsyncEventBus:
    """Tests for AsyncEventBus."""

    @pytest.fixture
    def event_bus(self):
        """Create fresh event bus for each test."""
        return AsyncEventBus()

    @pytest.mark.asyncio
    async def test_publish_to_subscriber(self, event_bus):
        """Test event is delivered to subscriber."""
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
        """Test event is delivered to all subscribers."""
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
        """Test subscribers only receive their event type."""
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
        """Test publishing with no subscribers doesn't error."""
        event = TestEvent(message="Nobody listening")
        # Should not raise
        await event_bus.publish(event)

    @pytest.mark.asyncio
    async def test_unsubscribe(self, event_bus):
        """Test unsubscribing handler."""
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
        """Test unsubscribe returns False if handler not found."""

        async def handler(event: TestEvent):
            pass

        result = event_bus.unsubscribe(TestEvent, handler)
        assert result is False

    @pytest.mark.asyncio
    async def test_subscribe_returns_unsubscribe_function(self, event_bus):
        """Test subscribe returns working unsubscribe function."""
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
        """Test clear removes all subscriptions."""

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
        """Test handler_count returns correct counts."""

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
        """Test one handler error doesn't affect others."""
        successful_calls = []

        async def failing_handler(event: TestEvent):
            raise ValueError("Handler error")

        async def success_handler(event: TestEvent):
            successful_calls.append(event)

        event_bus.subscribe(TestEvent, failing_handler)
        event_bus.subscribe(TestEvent, success_handler)

        # Should not raise, error should be isolated
        await event_bus.publish(TestEvent(message="Test"))

        assert len(successful_calls) == 1

    @pytest.mark.asyncio
    async def test_publish_all(self, event_bus):
        """Test publishing multiple events."""
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
    """Tests for event_handler decorator."""

    def test_decorator_sets_event_type(self):
        """Test decorator sets _event_type attribute."""

        @event_handler(TestEvent)
        async def handler(event: TestEvent):
            pass

        assert handler._event_type == TestEvent

    def test_decorator_preserves_function(self):
        """Test decorator preserves function behavior."""

        @event_handler(TestEvent)
        async def handler(event: TestEvent):
            return "result"

        # Function should still be callable
        import asyncio

        result = asyncio.run(handler(TestEvent()))
        assert result == "result"
