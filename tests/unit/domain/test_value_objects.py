"""Unit tests for domain value objects."""

import pytest

from ailoveshen.core.domain.value_objects import (
    DomainEvent,
    EmotionState,
    EmotionType,
    FilterResult,
    Position,
    Rotation,
    SpeechPriority,
    SpeechRequest,
)


class TestEmotionState:
    """Tests for EmotionState value object."""

    def test_default_values(self):
        """Test default emotion state."""
        state = EmotionState()
        assert state.primary == EmotionType.NEUTRAL
        assert state.intensity == 0.5

    def test_create_with_values(self):
        """Test creating with specific values."""
        state = EmotionState(EmotionType.HAPPY, 0.8)
        assert state.primary == EmotionType.HAPPY
        assert state.intensity == 0.8

    def test_invalid_intensity_raises_error(self):
        """Test that invalid intensity raises ValueError."""
        with pytest.raises(ValueError, match="intensity must be between"):
            EmotionState(EmotionType.HAPPY, 1.5)

        with pytest.raises(ValueError, match="intensity must be between"):
            EmotionState(EmotionType.SAD, -0.5)

    def test_with_intensity(self):
        """Test creating new state with different intensity."""
        original = EmotionState(EmotionType.HAPPY, 0.8)
        new_state = original.with_intensity(0.5)

        assert new_state.primary == EmotionType.HAPPY
        assert new_state.intensity == 0.5
        # Original unchanged (immutable)
        assert original.intensity == 0.8

    def test_with_intensity_clamps_values(self):
        """Test with_intensity clamps values to valid range."""
        state = EmotionState(EmotionType.HAPPY, 0.5)

        high = state.with_intensity(1.5)
        assert high.intensity == 1.0

        low = state.with_intensity(-0.5)
        assert low.intensity == 0.0

    def test_decay_reduces_intensity(self):
        """Test decay reduces intensity."""
        state = EmotionState(EmotionType.EXCITED, 0.8)
        decayed = state.decay(0.2)

        assert decayed.primary == EmotionType.EXCITED
        assert decayed.intensity == pytest.approx(0.6)

    def test_decay_returns_neutral_when_low(self):
        """Test decay returns neutral when intensity drops too low."""
        state = EmotionState(EmotionType.SCARED, 0.3)
        decayed = state.decay(0.1)

        assert decayed.primary == EmotionType.NEUTRAL
        assert decayed.intensity == 0.5

    def test_immutable(self):
        """Test that EmotionState is immutable."""
        state = EmotionState()
        with pytest.raises(AttributeError):
            state.primary = EmotionType.HAPPY


class TestSpeechRequest:
    """Tests for SpeechRequest value object."""

    def test_default_values(self):
        """Test default speech request."""
        request = SpeechRequest(text="Hello")
        assert request.text == "Hello"
        assert request.priority == SpeechPriority.NORMAL
        assert request.style == "Neutral"
        assert request.source == "unknown"

    def test_should_interrupt_low_priority(self):
        """Test low priority doesn't interrupt."""
        request = SpeechRequest(text="Test", priority=SpeechPriority.LOW)
        assert not request.should_interrupt()

    def test_should_interrupt_normal_priority(self):
        """Test normal priority doesn't interrupt."""
        request = SpeechRequest(text="Test", priority=SpeechPriority.NORMAL)
        assert not request.should_interrupt()

    def test_should_interrupt_high_priority(self):
        """Test high priority doesn't interrupt."""
        request = SpeechRequest(text="Test", priority=SpeechPriority.HIGH)
        assert not request.should_interrupt()

    def test_should_interrupt_interrupt_priority(self):
        """Test interrupt priority does interrupt."""
        request = SpeechRequest(text="Test", priority=SpeechPriority.INTERRUPT)
        assert request.should_interrupt()


class TestPosition:
    """Tests for Position value object."""

    def test_default_values(self):
        """Test default position."""
        pos = Position()
        assert pos.x == 0.0
        assert pos.y == 0.0
        assert pos.z == 0.0

    def test_distance_to_same_point(self):
        """Test distance to same point is zero."""
        pos1 = Position(1.0, 2.0, 3.0)
        pos2 = Position(1.0, 2.0, 3.0)
        assert pos1.distance_to(pos2) == 0.0

    def test_distance_to_different_point(self):
        """Test distance calculation."""
        pos1 = Position(0.0, 0.0, 0.0)
        pos2 = Position(3.0, 4.0, 0.0)
        assert pos1.distance_to(pos2) == 5.0

    def test_str_representation(self):
        """Test string representation."""
        pos = Position(1.5, 2.5, 3.5)
        assert str(pos) == "(1.5, 2.5, 3.5)"


class TestRotation:
    """Tests for Rotation value object."""

    def test_default_values(self):
        """Test default rotation."""
        rot = Rotation()
        assert rot.yaw == 0.0
        assert rot.pitch == 0.0

    def test_str_representation(self):
        """Test string representation."""
        rot = Rotation(45.0, -30.0)
        assert str(rot) == "(yaw=45.0, pitch=-30.0)"

    def test_valid_edge_values(self):
        """Test valid edge values for rotation."""
        rot = Rotation(yaw=-180.0, pitch=-90.0)
        assert rot.yaw == -180.0
        assert rot.pitch == -90.0

        rot = Rotation(yaw=180.0, pitch=90.0)
        assert rot.yaw == 180.0
        assert rot.pitch == 90.0

    def test_invalid_yaw_raises_error(self):
        """Test that invalid yaw raises ValueError."""
        with pytest.raises(ValueError, match="yaw must be between"):
            Rotation(yaw=181.0, pitch=0.0)

        with pytest.raises(ValueError, match="yaw must be between"):
            Rotation(yaw=-181.0, pitch=0.0)

    def test_invalid_pitch_raises_error(self):
        """Test that invalid pitch raises ValueError."""
        with pytest.raises(ValueError, match="pitch must be between"):
            Rotation(yaw=0.0, pitch=91.0)

        with pytest.raises(ValueError, match="pitch must be between"):
            Rotation(yaw=0.0, pitch=-91.0)


class TestFilterResult:
    """Tests for FilterResult value object."""

    def test_accept_factory(self):
        """Test accept factory method."""
        result = FilterResult.accept(0.8, "Interesting comment")
        assert result.should_respond is True
        assert result.score == 0.8
        assert result.reason == "Interesting comment"

    def test_reject_factory(self):
        """Test reject factory method."""
        result = FilterResult.reject(0.2, "Spam detected")
        assert result.should_respond is False
        assert result.score == 0.2
        assert result.reason == "Spam detected"

    def test_invalid_score_raises_error(self):
        """Test that invalid score raises ValueError."""
        with pytest.raises(ValueError, match="score must be between"):
            FilterResult(should_respond=True, score=1.5, reason="test")

        with pytest.raises(ValueError, match="score must be between"):
            FilterResult(should_respond=False, score=-0.1, reason="test")


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
