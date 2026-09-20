"""Speak text use case implementation."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from loguru import logger

from ailoveshen.core.application.ports.output_ports import IEventPublisher
from ailoveshen.core.domain.value_objects import EmotionState, SpeechPriority, SpeechRequest
from ailoveshen.tts.application.dto.speech_dto import (
    SpeakTextRequest,
    SpeakTextResponse,
)
from ailoveshen.tts.application.ports.input.speak_text import ISpeakText
from ailoveshen.tts.application.ports.output.audio_player import IAudioPlayer
from ailoveshen.tts.application.ports.output.speech_synthesizer import ISpeechSynthesizer
from ailoveshen.tts.domain.events import SpeechCompletedEvent, SpeechStartedEvent
from ailoveshen.tts.domain.services.emotion_style_service import EmotionStyleService


class SpeakTextUseCase(ISpeakText):
    """
    Use case for synthesizing and playing speech.

    This is the core business logic for TTS operations.
    It coordinates:
    - Style selection based on emotion state
    - Speech synthesis via adapter
    - Audio playback via adapter
    - Domain event publishing
    """

    def __init__(
        self,
        synthesizer: ISpeechSynthesizer,
        audio_player: IAudioPlayer,
        event_publisher: IEventPublisher,
        emotion_style_service: EmotionStyleService,
        get_current_emotion: Callable[[], EmotionState],
    ) -> None:
        """
        Initialize use case with dependencies (Dependency Injection).

        Args:
            synthesizer: Speech synthesizer adapter
            audio_player: Audio player adapter
            event_publisher: Event publisher for domain events
            emotion_style_service: Domain service for emotion->style mapping
            get_current_emotion: Callable to get current emotion state
        """
        self._synthesizer = synthesizer
        self._audio_player = audio_player
        self._event_publisher = event_publisher
        self._emotion_style_service = emotion_style_service
        self._get_current_emotion = get_current_emotion

        # Interrupt handling
        self._interrupt_event = asyncio.Event()
        self._current_request: Optional[SpeechRequest] = None

    async def execute(self, request: SpeakTextRequest) -> SpeakTextResponse:
        """
        Execute the speak text use case.

        Flow:
        1. Validate and prepare request
        2. Determine voice style (emotion-based or override)
        3. Handle interrupt if needed
        4. Synthesize speech
        5. Publish started event
        6. Play audio
        7. Publish completed event
        """
        try:
            # Validate text
            text = request.text.strip()
            if not text:
                return SpeakTextResponse.error_response("Empty text")

            # Determine style
            if request.style is not None:
                style = request.style
            else:
                emotion = self._get_current_emotion()
                style = self._emotion_style_service.get_style_for_emotion(emotion)

            # Create domain value object
            speech_request = SpeechRequest(
                text=text,
                priority=request.priority,
                style=style,
                source=request.source,
            )

            # Handle interrupt priority
            if speech_request.should_interrupt() and self._audio_player.is_playing():
                logger.info(f"Interrupting current speech for: {text[:50]}...")
                self._audio_player.stop()
                self._interrupt_event.set()
                # Give time for current playback to stop
                await asyncio.sleep(0.1)

            self._current_request = speech_request
            self._interrupt_event.clear()

            # Synthesize speech
            logger.debug(f"Synthesizing: {text[:50]}... [style={style}]")
            audio_data = await self._synthesizer.synthesize(
                text=text,
                style=style,
                speaker_id=request.speaker_id,
                language=request.language,
            )

            # Get duration for events
            duration_ms = self._audio_player.get_duration_ms(audio_data)

            # Publish started event
            await self._event_publisher.publish(
                SpeechStartedEvent(
                    text=text,
                    source=request.source,
                    style=style,
                )
            )

            # Play audio
            logger.debug(f"Playing audio: {duration_ms}ms")
            completed = await self._audio_player.play(
                audio_data,
                interrupt_event=self._interrupt_event,
            )

            # Publish completed event
            await self._event_publisher.publish(
                SpeechCompletedEvent(
                    text=text,
                    source=request.source,
                    completed=completed,
                    duration_ms=duration_ms if completed else 0,
                )
            )

            self._current_request = None

            if completed:
                return SpeakTextResponse.ok(
                    message="Speech completed",
                    duration_ms=duration_ms,
                )
            else:
                return SpeakTextResponse.interrupted()

        except Exception as e:
            logger.error(f"Speech failed: {e}")
            self._current_request = None
            return SpeakTextResponse.error_response(str(e))

    def request_interrupt(self) -> None:
        """
        Request interruption of current playback.

        Safe to call even if nothing is playing.
        """
        self._interrupt_event.set()
        self._audio_player.stop()

    def is_speaking(self) -> bool:
        """Check if currently speaking."""
        return self._audio_player.is_playing()

    def get_current_request(self) -> Optional[SpeechRequest]:
        """Get the currently playing speech request, if any."""
        return self._current_request
