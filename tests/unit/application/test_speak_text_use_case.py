"""Tests for SpeakTextUseCase."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.domain.value_objects import EmotionState, EmotionType, SpeechPriority
from ailoveshen.application.dto.speech_dto import SpeakTextRequest
from ailoveshen.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.domain.events import SpeechCompletedEvent, SpeechStartedEvent


@pytest.fixture
def mock_synthesizer():
    """Create mock synthesizer."""
    synthesizer = AsyncMock()
    synthesizer.synthesize.return_value = b"fake_audio_data"
    return synthesizer


@pytest.fixture
def mock_audio_player():
    """Create mock audio player."""
    player = AsyncMock()
    player.play.return_value = True
    # These are sync methods, use Mock not AsyncMock
    player.is_playing = Mock(return_value=False)
    player.get_duration_ms = Mock(return_value=1000)
    player.stop = Mock()
    return player


@pytest.fixture
def mock_event_publisher():
    """Create mock event publisher."""
    return AsyncMock()


@pytest.fixture
def neutral_emotion_provider():
    """Create neutral emotion provider."""
    return lambda: EmotionState(primary=EmotionType.NEUTRAL, intensity=0.5)


@pytest.fixture
def use_case(
    mock_synthesizer,
    mock_audio_player,
    mock_event_publisher,
    neutral_emotion_provider,
):
    """Create SpeakTextUseCase with mocked dependencies."""
    return SpeakTextUseCase(
        synthesizer=mock_synthesizer,
        audio_player=mock_audio_player,
        event_publisher=mock_event_publisher,
        get_current_emotion=neutral_emotion_provider,
    )


class TestSpeakTextUseCase:
    """Tests for SpeakTextUseCase."""

    @pytest.mark.asyncio
    async def test_execute_successful_speech(
        self, use_case, mock_synthesizer, mock_audio_player
    ):
        """Test successful speech synthesis and playback."""
        request = SpeakTextRequest(text="Hello world")

        response = await use_case.execute(request)

        assert response.success is True
        assert response.duration_ms == 1000
        mock_synthesizer.synthesize.assert_called_once()
        mock_audio_player.play.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_empty_text_returns_error(self, use_case):
        """Test empty text returns error response."""
        request = SpeakTextRequest(text="")

        response = await use_case.execute(request)

        assert response.success is False
        assert "Empty text" in response.error

    @pytest.mark.asyncio
    async def test_execute_whitespace_text_returns_error(self, use_case):
        """Test whitespace-only text returns error response."""
        request = SpeakTextRequest(text="   ")

        response = await use_case.execute(request)

        assert response.success is False
        assert "Empty text" in response.error

    @pytest.mark.asyncio
    async def test_execute_uses_current_emotion(
        self,
        mock_synthesizer,
        mock_audio_player,
        mock_event_publisher,
    ):
        """Test that current emotion is used when no emotion specified."""
        happy_emotion = lambda: EmotionState(primary=EmotionType.HAPPY, intensity=0.8)

        use_case = SpeakTextUseCase(
            synthesizer=mock_synthesizer,
            audio_player=mock_audio_player,
            event_publisher=mock_event_publisher,
            get_current_emotion=happy_emotion,
        )

        request = SpeakTextRequest(text="Hello")
        await use_case.execute(request)

        mock_synthesizer.synthesize.assert_called_once()
        call_kwargs = mock_synthesizer.synthesize.call_args.kwargs
        assert call_kwargs["emotion"] == happy_emotion()

    @pytest.mark.asyncio
    async def test_execute_uses_override_emotion(self, use_case, mock_synthesizer):
        """Test that override emotion is used when specified."""
        sad = EmotionState(primary=EmotionType.SAD, intensity=0.7)
        request = SpeakTextRequest(text="Hello", emotion=sad)

        await use_case.execute(request)

        call_kwargs = mock_synthesizer.synthesize.call_args.kwargs
        assert call_kwargs["emotion"] == sad

    @pytest.mark.asyncio
    async def test_execute_publishes_started_event(self, use_case, mock_event_publisher):
        """Test that SpeechStartedEvent is published."""
        request = SpeakTextRequest(text="Hello", source="test")

        await use_case.execute(request)

        # Check that publish was called with SpeechStartedEvent
        calls = mock_event_publisher.publish.call_args_list
        started_events = [c for c in calls if isinstance(c[0][0], SpeechStartedEvent)]
        assert len(started_events) == 1
        assert started_events[0][0][0].text == "Hello"
        assert started_events[0][0][0].source == "test"

    @pytest.mark.asyncio
    async def test_execute_publishes_completed_event(
        self, use_case, mock_event_publisher
    ):
        """Test that SpeechCompletedEvent is published."""
        request = SpeakTextRequest(text="Hello", source="test")

        await use_case.execute(request)

        # Check that publish was called with SpeechCompletedEvent
        calls = mock_event_publisher.publish.call_args_list
        completed_events = [
            c for c in calls if isinstance(c[0][0], SpeechCompletedEvent)
        ]
        assert len(completed_events) == 1
        assert completed_events[0][0][0].text == "Hello"
        assert completed_events[0][0][0].completed is True

    @pytest.mark.asyncio
    async def test_execute_interrupted_speech(
        self, use_case, mock_audio_player, mock_event_publisher
    ):
        """Test interrupted speech publishes correct event."""
        mock_audio_player.play.return_value = False  # Indicates interrupted

        request = SpeakTextRequest(text="Hello")
        response = await use_case.execute(request)

        assert response.success is True
        assert "interrupted" in response.message.lower()

        # Check completed event shows not completed
        calls = mock_event_publisher.publish.call_args_list
        completed_events = [
            c for c in calls if isinstance(c[0][0], SpeechCompletedEvent)
        ]
        assert completed_events[0][0][0].completed is False

    @pytest.mark.asyncio
    async def test_execute_interrupt_priority_stops_current(
        self,
        mock_synthesizer,
        mock_audio_player,
        mock_event_publisher,
        neutral_emotion_provider,
    ):
        """Test interrupt priority stops current playback."""
        mock_audio_player.is_playing.return_value = True

        use_case = SpeakTextUseCase(
            synthesizer=mock_synthesizer,
            audio_player=mock_audio_player,
            event_publisher=mock_event_publisher,
            get_current_emotion=neutral_emotion_provider,
        )

        request = SpeakTextRequest(text="Interrupt!", priority=SpeechPriority.INTERRUPT)
        await use_case.execute(request)

        mock_audio_player.stop.assert_called()

    @pytest.mark.asyncio
    async def test_execute_handles_synthesis_error(self, use_case, mock_synthesizer):
        """Test error handling when synthesis fails."""
        mock_synthesizer.synthesize.side_effect = Exception("Synthesis failed")

        request = SpeakTextRequest(text="Hello")
        response = await use_case.execute(request)

        assert response.success is False
        assert "Synthesis failed" in response.error

    def test_is_speaking(self, use_case, mock_audio_player):
        """Test is_speaking delegates to audio player."""
        mock_audio_player.is_playing.return_value = True
        assert use_case.is_speaking() is True

        mock_audio_player.is_playing.return_value = False
        assert use_case.is_speaking() is False

    def test_request_interrupt(self, use_case, mock_audio_player):
        """Test request_interrupt stops playback."""
        use_case.request_interrupt()
        mock_audio_player.stop.assert_called()
