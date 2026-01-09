"""Entity base classes for Domain layer."""

from __future__ import annotations

import uuid
from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ailoveshen.core.domain.value_objects import DomainEvent


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
