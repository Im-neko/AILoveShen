"""SpeakTextUseCase のテスト。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.domain.value_objects import EmotionState, EmotionType, SpeechPriority
from ailoveshen.application.dto.speech_dto import SpeakTextRequest
from ailoveshen.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.domain.events import SpeechCompletedEvent, SpeechStartedEvent


@pytest.fixture
def mock_synthesizer():
    """音声合成のモック。"""
    synthesizer = AsyncMock()
    synthesizer.synthesize.return_value = b"fake_audio_data"
    return synthesizer


@pytest.fixture
def mock_audio_player():
    """音声再生のモック。"""
    player = AsyncMock()
    player.play.return_value = True
    # 同期メソッドなので AsyncMock でなく Mock を使う
    player.is_playing = Mock(return_value=False)
    player.get_duration_ms = Mock(return_value=1000)
    player.stop = Mock()
    return player


@pytest.fixture
def mock_event_publisher():
    """イベント発行のモック。"""
    return AsyncMock()


@pytest.fixture
def neutral_emotion_provider():
    """neutral の感情を返すプロバイダ。"""
    return lambda: EmotionState(primary=EmotionType.NEUTRAL, intensity=0.5)


@pytest.fixture
def use_case(
    mock_synthesizer,
    mock_audio_player,
    mock_event_publisher,
    neutral_emotion_provider,
):
    """依存をモックにした SpeakTextUseCase。"""
    return SpeakTextUseCase(
        synthesizer=mock_synthesizer,
        audio_player=mock_audio_player,
        event_publisher=mock_event_publisher,
        get_current_emotion=neutral_emotion_provider,
    )


class TestSpeakTextUseCase:
    """SpeakTextUseCase のテスト。"""

    @pytest.mark.asyncio
    async def test_execute_successful_speech(self, use_case, mock_synthesizer, mock_audio_player):
        """音声を合成して再生できる。"""
        request = SpeakTextRequest(text="Hello world")

        response = await use_case.execute(request)

        assert response.success is True
        assert response.duration_ms == 1000
        mock_synthesizer.synthesize.assert_called_once()
        mock_audio_player.play.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_empty_text_returns_error(self, use_case):
        """テキストが空ならエラーの応答を返す。"""
        request = SpeakTextRequest(text="")

        response = await use_case.execute(request)

        assert response.success is False
        assert "Empty text" in response.error

    @pytest.mark.asyncio
    async def test_execute_whitespace_text_returns_error(self, use_case):
        """空白だけのテキストならエラーの応答を返す。"""
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
        """感情の指定がなければ、今の感情を使う。"""
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
        """感情を指定したら、その感情を使う。"""
        sad = EmotionState(primary=EmotionType.SAD, intensity=0.7)
        request = SpeakTextRequest(text="Hello", emotion=sad)

        await use_case.execute(request)

        call_kwargs = mock_synthesizer.synthesize.call_args.kwargs
        assert call_kwargs["emotion"] == sad

    @pytest.mark.asyncio
    async def test_execute_publishes_started_event(self, use_case, mock_event_publisher):
        """SpeechStartedEvent が発行される。"""
        request = SpeakTextRequest(text="Hello", source="test")

        await use_case.execute(request)

        # SpeechStartedEvent で publish が呼ばれたか
        calls = mock_event_publisher.publish.call_args_list
        started_events = [c for c in calls if isinstance(c[0][0], SpeechStartedEvent)]
        assert len(started_events) == 1
        assert started_events[0][0][0].text == "Hello"
        assert started_events[0][0][0].source == "test"

    @pytest.mark.asyncio
    async def test_execute_publishes_completed_event(self, use_case, mock_event_publisher):
        """SpeechCompletedEvent が発行される。"""
        request = SpeakTextRequest(text="Hello", source="test")

        await use_case.execute(request)

        # SpeechCompletedEvent で publish が呼ばれたか
        calls = mock_event_publisher.publish.call_args_list
        completed_events = [c for c in calls if isinstance(c[0][0], SpeechCompletedEvent)]
        assert len(completed_events) == 1
        assert completed_events[0][0][0].text == "Hello"
        assert completed_events[0][0][0].completed is True

    @pytest.mark.asyncio
    async def test_execute_interrupted_speech(
        self, use_case, mock_audio_player, mock_event_publisher
    ):
        """中断した発話は、それに合ったイベントを発行する。"""
        mock_audio_player.play.return_value = False  # 中断を表す

        request = SpeakTextRequest(text="Hello")
        response = await use_case.execute(request)

        assert response.success is True
        assert "interrupted" in response.message.lower()

        # 完了イベントが未完了を示すか
        calls = mock_event_publisher.publish.call_args_list
        completed_events = [c for c in calls if isinstance(c[0][0], SpeechCompletedEvent)]
        assert completed_events[0][0][0].completed is False

    @pytest.mark.asyncio
    async def test_execute_interrupt_priority_stops_current(
        self,
        mock_synthesizer,
        mock_audio_player,
        mock_event_publisher,
        neutral_emotion_provider,
    ):
        """interrupt の優先度は、今の再生を止める。"""
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
        """合成に失敗したときの扱い。"""
        mock_synthesizer.synthesize.side_effect = Exception("Synthesis failed")

        request = SpeakTextRequest(text="Hello")
        response = await use_case.execute(request)

        assert response.success is False
        assert "Synthesis failed" in response.error

    def test_is_speaking(self, use_case, mock_audio_player):
        """is_speaking は音声再生に委ねる。"""
        mock_audio_player.is_playing.return_value = True
        assert use_case.is_speaking() is True

        mock_audio_player.is_playing.return_value = False
        assert use_case.is_speaking() is False

    def test_request_interrupt(self, use_case, mock_audio_player):
        """request_interrupt は再生を止める。"""
        use_case.request_interrupt()
        mock_audio_player.stop.assert_called()
