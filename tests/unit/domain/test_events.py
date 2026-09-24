"""Tests for domain events."""

import pytest

from ailoveshen.domain.events import DomainEvent


class TestDomainEvent:
    """Tests for DomainEvent base class."""

    def test_event_id_generated(self):
        """Test event ID is auto-generated."""
        event1 = DomainEvent()
        event2 = DomainEvent()
        assert event1.event_id != event2.event_id

    def test_occurred_at_set(self):
        """Test occurred_at is set."""
        event = DomainEvent()
        assert event.occurred_at is not None

    def test_occurred_at_is_utc(self):
        """Test occurred_at is in UTC timezone."""
        from datetime import timezone

        event = DomainEvent()
        assert event.occurred_at.tzinfo == timezone.utc

    def test_event_type_property(self):
        """Test event_type returns class name."""
        event = DomainEvent()
        assert event.event_type == "DomainEvent"

    def test_immutable(self):
        """Test that DomainEvent is immutable."""
        event = DomainEvent()
        with pytest.raises(AttributeError):
            event.event_id = "new-id"
