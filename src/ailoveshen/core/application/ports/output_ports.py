"""Output ports (interfaces) for the Application layer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

if TYPE_CHECKING:
    from ailoveshen.core.domain.value_objects import DomainEvent

T = TypeVar("T", bound="DomainEvent")

# Type alias for event handlers
EventHandler = Callable[[T], Awaitable[None]]


class IEventPublisher(ABC):
    """
    Event publisher output port.

    Defines the interface for publishing domain events.
    Infrastructure layer provides the concrete implementation.
    """

    @abstractmethod
    async def publish(self, event: "DomainEvent") -> None:
        """
        Publish a domain event.

        Args:
            event: The domain event to publish.
        """
        ...

    @abstractmethod
    async def publish_all(self, events: list["DomainEvent"]) -> None:
        """
        Publish multiple domain events.

        Args:
            events: List of domain events to publish.
        """
        ...


class IEventSubscriber(ABC):
    """
    Event subscriber output port.

    Defines the interface for subscribing to domain events.
    """

    @abstractmethod
    def subscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> Callable[[], None]:
        """
        Subscribe to a specific event type.

        Args:
            event_type: The type of event to subscribe to.
            handler: Async function to handle the event.

        Returns:
            Unsubscribe function that can be called to remove the subscription.
        """
        ...

    @abstractmethod
    def unsubscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> bool:
        """
        Unsubscribe from a specific event type.

        Args:
            event_type: The type of event to unsubscribe from.
            handler: The handler to remove.

        Returns:
            True if the handler was found and removed, False otherwise.
        """
        ...
