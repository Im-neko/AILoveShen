"""Entity base classes for Domain layer."""

from __future__ import annotations

import uuid
from abc import ABC
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from ailoveshen.domain.value_objects import ConversationMessage, MessageRole, MessageType

if TYPE_CHECKING:
    from ailoveshen.domain.events import DomainEvent


def generate_id() -> str:
    """Generate a unique identifier."""
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


@dataclass
class Entity(ABC):
    """
    Base class for all domain entities.

    Entities are objects that have a distinct identity that runs through time
    and different states. Two entities are equal if they have the same id.
    """

    id: str = field(default_factory=generate_id)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __eq__(self, other: Any) -> bool:
        """Entities are equal if they have the same id."""
        if not isinstance(other, Entity):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash based on id for use in sets and dicts."""
        return hash(self.id)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.id!r})"


@dataclass
class AggregateRoot(Entity):
    """
    Base class for aggregate roots.

    An aggregate root is an entity that acts as a gateway to a cluster
    of related objects. All external references should be to the aggregate root.
    """

    _domain_events: list["DomainEvent"] = field(default_factory=list, repr=False)

    def add_domain_event(self, event: "DomainEvent") -> None:
        """Add a domain event to be dispatched."""
        self._domain_events.append(event)

    def clear_domain_events(self) -> list["DomainEvent"]:
        """Clear and return all domain events."""
        events = self._domain_events.copy()
        self._domain_events.clear()
        return events


@dataclass(eq=False)
class Conversation(Entity):
    """
    The stream's short-term conversation history.

    Holds viewer chats and the streamer's utterances in order, keeping only
    the latest max_history messages.

    Raises:
        ValueError: If max_history is not positive.
    """

    max_history: int = 20
    _messages: deque[ConversationMessage] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate max_history and create the bounded history."""
        if self.max_history <= 0:
            raise ValueError(f"max_history must be positive, got {self.max_history}")
        self._messages = deque(maxlen=self.max_history)

    def add_viewer_message(
        self,
        content: str,
        user_name: str,
        user_id: Optional[str] = None,
    ) -> ConversationMessage:
        """Record a chat message from a viewer."""
        return self._append(ConversationMessage.from_viewer(content, user_name, user_id))

    def add_streamer_message(
        self,
        content: str,
        message_type: MessageType,
    ) -> ConversationMessage:
        """Record something the streamer said."""
        return self._append(ConversationMessage.from_streamer(content, message_type))

    def recent_messages(self, limit: int = 10) -> tuple[ConversationMessage, ...]:
        """Return the latest messages, oldest first."""
        if limit <= 0:
            return ()
        return tuple(self._messages)[-limit:]

    def recent_viewer_messages(self, limit: int = 5) -> tuple[ConversationMessage, ...]:
        """Return the latest viewer chat messages, oldest first."""
        if limit <= 0:
            return ()
        viewer = [m for m in self._messages if m.role == MessageRole.VIEWER]
        return tuple(viewer[-limit:])

    def clear(self) -> None:
        """Forget the whole history."""
        self._messages.clear()
        self.updated_at = _utc_now()

    def _append(self, message: ConversationMessage) -> ConversationMessage:
        self._messages.append(message)
        self.updated_at = _utc_now()
        return message

    def __iter__(self) -> Iterator[ConversationMessage]:
        return iter(self._messages)

    def __len__(self) -> int:
        return len(self._messages)
