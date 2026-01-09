"""Event bus implementation for async pub/sub messaging."""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

from loguru import logger

from ailoveshen.core.application.ports.output_ports import (
    IEventPublisher,
    IEventSubscriber,
)

if TYPE_CHECKING:
    from ailoveshen.core.domain.value_objects import DomainEvent

T = TypeVar("T", bound="DomainEvent")

# Type alias for event handlers
EventHandler = Callable[[T], Awaitable[None]]


class AsyncEventBus(IEventPublisher, IEventSubscriber):
    """
    Async event bus implementing pub/sub pattern.

    Features:
    - Type-safe event subscription
    - Async event handling
    - Error isolation (one handler failure doesn't affect others)
    - Thread-safe subscribe/unsubscribe operations
    - Logging for debugging
    """

    def __init__(self) -> None:
        """Initialize the event bus."""
        self._handlers: dict[type, list[EventHandler]] = defaultdict(list)
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    async def publish(self, event: "DomainEvent") -> None:
        """
        Publish a domain event to all registered handlers.

        Args:
            event: The domain event to publish.
        """
        event_type = type(event)

        # Get a snapshot of handlers under lock to avoid race conditions
        async with self._async_lock:
            handlers = list(self._handlers.get(event_type, []))

        if not handlers:
            logger.debug(f"No handlers registered for {event_type.__name__}")
            return

        logger.debug(
            f"Publishing {event_type.__name__} to {len(handlers)} handler(s)"
        )

        # Execute all handlers concurrently
        tasks = [self._safe_execute(handler, event) for handler in handlers]
        await asyncio.gather(*tasks)

    async def publish_all(self, events: list["DomainEvent"]) -> None:
        """
        Publish multiple domain events.

        Args:
            events: List of domain events to publish.
        """
        for event in events:
            await self.publish(event)

    def subscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> Callable[[], None]:
        """
        Subscribe to a specific event type.

        Thread-safe: can be called from any thread.

        Args:
            event_type: The type of event to subscribe to.
            handler: Async function to handle the event.

        Returns:
            Unsubscribe function.
        """
        with self._sync_lock:
            self._handlers[event_type].append(handler)
        logger.debug(f"Handler subscribed to {event_type.__name__}")

        # Return unsubscribe function
        def unsubscribe() -> None:
            self.unsubscribe(event_type, handler)

        return unsubscribe

    def unsubscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> bool:
        """
        Unsubscribe from a specific event type.

        Thread-safe: can be called from any thread.

        Args:
            event_type: The type of event to unsubscribe from.
            handler: The handler to remove.

        Returns:
            True if the handler was found and removed.
        """
        with self._sync_lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
                logger.debug(f"Handler unsubscribed from {event_type.__name__}")
                return True
        return False

    def clear(self) -> None:
        """Remove all subscriptions."""
        with self._sync_lock:
            self._handlers.clear()
        logger.debug("All event subscriptions cleared")

    def handler_count(self, event_type: type[T] | None = None) -> int:
        """
        Get the number of registered handlers.

        Args:
            event_type: If provided, count handlers for this type only.

        Returns:
            Number of handlers.
        """
        with self._sync_lock:
            if event_type is not None:
                return len(self._handlers.get(event_type, []))
            return sum(len(handlers) for handlers in self._handlers.values())

    async def _safe_execute(
        self,
        handler: EventHandler,
        event: "DomainEvent",
    ) -> None:
        """
        Execute a handler with error isolation.

        Args:
            handler: The handler function to execute.
            event: The event to pass to the handler.
        """
        try:
            await handler(event)
        except Exception as e:
            logger.exception(
                f"Error in event handler for {type(event).__name__}: {e}"
            )


class SyncEventBus(IEventPublisher, IEventSubscriber):
    """
    Synchronous event bus for testing and simple use cases.

    Wraps handlers in asyncio.run() for sync execution.
    """

    def __init__(self) -> None:
        """Initialize the sync event bus."""
        self._async_bus = AsyncEventBus()

    async def publish(self, event: "DomainEvent") -> None:
        """Publish an event."""
        await self._async_bus.publish(event)

    async def publish_all(self, events: list["DomainEvent"]) -> None:
        """Publish multiple events."""
        await self._async_bus.publish_all(events)

    def subscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> Callable[[], None]:
        """Subscribe to an event type."""
        return self._async_bus.subscribe(event_type, handler)

    def unsubscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> bool:
        """Unsubscribe from an event type."""
        return self._async_bus.unsubscribe(event_type, handler)

    def publish_sync(self, event: "DomainEvent") -> None:
        """
        Synchronously publish an event.

        Works correctly whether or not an event loop is already running.
        """
        try:
            loop = asyncio.get_running_loop()
            # Event loop is running - schedule coroutine
            future = asyncio.run_coroutine_threadsafe(self.publish(event), loop)
            future.result()
        except RuntimeError:
            # No event loop running - create one
            asyncio.run(self.publish(event))


# Convenience decorator for event handlers
def event_handler(event_type: type[T]):
    """
    Decorator to mark a function as an event handler.

    Usage:
        @event_handler(ChatMessageReceived)
        async def handle_chat_message(event: ChatMessageReceived):
            ...
    """

    def decorator(func: EventHandler[T]) -> EventHandler[T]:
        func._event_type = event_type  # type: ignore[attr-defined]
        return func

    return decorator
