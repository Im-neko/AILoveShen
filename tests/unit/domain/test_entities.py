"""Unit tests for domain entities."""

import pytest

from ailoveshen.core.domain.entities import AggregateRoot, Entity, generate_id
from ailoveshen.core.domain.value_objects import DomainEvent


class TestGenerateId:
    """Tests for ID generation."""

    def test_generates_unique_ids(self):
        """Test that generated IDs are unique."""
        ids = [generate_id() for _ in range(100)]
        assert len(set(ids)) == 100


class TestEntity:
    """Tests for Entity base class."""

    def test_auto_generates_id(self):
        """Test entity auto-generates ID."""

        class TestEntity(Entity):
            pass

        entity = TestEntity()
        assert entity.id is not None
        assert len(entity.id) == 36  # UUID format

    def test_sets_timestamps(self):
        """Test entity sets created_at and updated_at."""

        class TestEntity(Entity):
            pass

        entity = TestEntity()
        assert entity.created_at is not None
        assert entity.updated_at is not None

    def test_timestamps_are_utc(self):
        """Test entity timestamps are in UTC timezone."""
        from datetime import timezone

        class TestEntity(Entity):
            pass

        entity = TestEntity()
        assert entity.created_at.tzinfo == timezone.utc
        assert entity.updated_at.tzinfo == timezone.utc

    def test_equality_by_id(self):
        """Test entities are equal if they have same ID."""

        class TestEntity(Entity):
            pass

        entity1 = TestEntity(id="same-id")
        entity2 = TestEntity(id="same-id")
        entity3 = TestEntity(id="different-id")

        assert entity1 == entity2
        assert entity1 != entity3

    def test_not_equal_to_non_entity(self):
        """Test entity is not equal to non-entity."""

        class TestEntity(Entity):
            pass

        entity = TestEntity(id="test-id")
        assert entity != "test-id"
        assert entity != 123
        assert entity != None

    def test_hashable(self):
        """Test entity is hashable (can be used in sets/dicts)."""

        class TestEntity(Entity):
            pass

        entity1 = TestEntity(id="test-id")
        entity2 = TestEntity(id="test-id")
        entity3 = TestEntity(id="other-id")

        # Can add to set
        entities = {entity1, entity2, entity3}
        assert len(entities) == 2  # entity1 and entity2 have same ID

        # Can use as dict key
        entity_dict = {entity1: "value1"}
        assert entity_dict[entity2] == "value1"

    def test_repr(self):
        """Test string representation."""

        class TestEntity(Entity):
            pass

        entity = TestEntity(id="test-id")
        assert "TestEntity" in repr(entity)
        assert "test-id" in repr(entity)


class TestAggregateRoot:
    """Tests for AggregateRoot base class."""

    def test_inherits_from_entity(self):
        """Test AggregateRoot inherits Entity behavior."""

        class TestAggregate(AggregateRoot):
            pass

        aggregate = TestAggregate()
        assert aggregate.id is not None
        assert aggregate.created_at is not None

    def test_add_domain_event(self):
        """Test adding domain events."""

        class TestAggregate(AggregateRoot):
            pass

        aggregate = TestAggregate()
        event = DomainEvent()

        aggregate.add_domain_event(event)
        assert len(aggregate._domain_events) == 1

    def test_clear_domain_events(self):
        """Test clearing and returning domain events."""

        class TestAggregate(AggregateRoot):
            pass

        aggregate = TestAggregate()
        event1 = DomainEvent()
        event2 = DomainEvent()

        aggregate.add_domain_event(event1)
        aggregate.add_domain_event(event2)

        events = aggregate.clear_domain_events()

        assert len(events) == 2
        assert event1 in events
        assert event2 in events
        assert len(aggregate._domain_events) == 0
