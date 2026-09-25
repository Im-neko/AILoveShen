"""domain のエンティティの単体テスト。"""

import pytest

from ailoveshen.domain.entities import AggregateRoot, Entity, generate_id
from ailoveshen.domain.events import DomainEvent


class TestGenerateId:
    """ID の生成のテスト。"""

    def test_generates_unique_ids(self):
        """生成した ID は重ならない。"""
        ids = [generate_id() for _ in range(100)]
        assert len(set(ids)) == 100


class TestEntity:
    """Entity 基底クラスのテスト。"""

    def test_auto_generates_id(self):
        """エンティティは ID を自動で作る。"""

        class TestEntity(Entity):
            pass

        entity = TestEntity()
        assert entity.id is not None
        assert len(entity.id) == 36  # UUID の形式

    def test_sets_timestamps(self):
        """エンティティは created_at と updated_at を設定する。"""

        class TestEntity(Entity):
            pass

        entity = TestEntity()
        assert entity.created_at is not None
        assert entity.updated_at is not None

    def test_timestamps_are_utc(self):
        """エンティティのタイムスタンプは UTC。"""
        from datetime import timezone

        class TestEntity(Entity):
            pass

        entity = TestEntity()
        assert entity.created_at.tzinfo == timezone.utc
        assert entity.updated_at.tzinfo == timezone.utc

    def test_equality_by_id(self):
        """ID が同じエンティティは等しい。"""

        class TestEntity(Entity):
            pass

        entity1 = TestEntity(id="same-id")
        entity2 = TestEntity(id="same-id")
        entity3 = TestEntity(id="different-id")

        assert entity1 == entity2
        assert entity1 != entity3

    def test_not_equal_to_non_entity(self):
        """エンティティはエンティティでないものと等しくない。"""

        class TestEntity(Entity):
            pass

        entity = TestEntity(id="test-id")
        assert entity != "test-id"
        assert entity != 123
        assert entity != None

    def test_hashable(self):
        """エンティティはハッシュ可能（set や dict に使える）。"""

        class TestEntity(Entity):
            pass

        entity1 = TestEntity(id="test-id")
        entity2 = TestEntity(id="test-id")
        entity3 = TestEntity(id="other-id")

        # set に入れられる
        entities = {entity1, entity2, entity3}
        assert len(entities) == 2  # entity1 と entity2 は同じ ID

        # dict のキーに使える
        entity_dict = {entity1: "value1"}
        assert entity_dict[entity2] == "value1"

    def test_repr(self):
        """文字列表現。"""

        class TestEntity(Entity):
            pass

        entity = TestEntity(id="test-id")
        assert "TestEntity" in repr(entity)
        assert "test-id" in repr(entity)


class TestAggregateRoot:
    """AggregateRoot 基底クラスのテスト。"""

    def test_inherits_from_entity(self):
        """AggregateRoot は Entity の振る舞いを受け継ぐ。"""

        class TestAggregate(AggregateRoot):
            pass

        aggregate = TestAggregate()
        assert aggregate.id is not None
        assert aggregate.created_at is not None

    def test_add_domain_event(self):
        """ドメインイベントを足す。"""

        class TestAggregate(AggregateRoot):
            pass

        aggregate = TestAggregate()
        event = DomainEvent()

        aggregate.add_domain_event(event)
        assert len(aggregate._domain_events) == 1

    def test_clear_domain_events(self):
        """ドメインイベントを返して消す。"""

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
