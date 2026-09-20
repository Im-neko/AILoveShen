#!/usr/bin/env python3
"""
Phase 2 Demo: TTS Pipeline

This demo showcases the TTS (Text-to-Speech) pipeline implemented in Phase 2.
It demonstrates:
- Domain layer: Value objects, Domain services, Events
- Application layer: Use cases, DTOs, Ports
- Presentation layer: TTSService with queue management

Note: This demo uses mock adapters since it doesn't require a running TTS server.
For real TTS, install `pip install ailoveshen[tts]` and run Style-Bert-VITS2 server.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List
from unittest.mock import AsyncMock, Mock

from ailoveshen.core.domain.value_objects import (
    EmotionState,
    EmotionType,
    SpeechPriority,
)
from ailoveshen.core.infrastructure.events import AsyncEventBus
from ailoveshen.tts.domain.value_objects import SpeechResult, SpeechStatus, VoiceConfig
from ailoveshen.tts.domain.events import SpeechStartedEvent, SpeechCompletedEvent
from ailoveshen.tts.domain.services.emotion_style_service import EmotionStyleService
from ailoveshen.tts.application.dto.speech_dto import SpeakTextRequest, SpeakTextResponse
from ailoveshen.tts.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.tts.presentation.services.tts_service import TTSService


def print_header(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def demo_domain_value_objects() -> None:
    """Demonstrate domain value objects."""
    print_header("Domain Value Objects")

    # SpeechResult
    print("SpeechResult:")
    result_completed = SpeechResult.completed("Hello world", 1500)
    print(f"  Completed: {result_completed}")
    print(f"    - is_success: {result_completed.is_success}")
    print(f"    - duration_ms: {result_completed.audio_duration_ms}")

    result_failed = SpeechResult.failed("Error text", "Connection timeout")
    print(f"  Failed: {result_failed}")
    print(f"    - error_message: {result_failed.error_message}")

    # VoiceConfig
    print("\nVoiceConfig:")
    config = VoiceConfig(
        model_name="my_model",
        speaker_id=1,
        language="JP",
    )
    print(f"  {config}")

    # Validation
    print("\n  Validation test:")
    try:
        VoiceConfig(speaker_id=-1)
    except ValueError as e:
        print(f"    Invalid speaker_id raises: {e}")


def demo_emotion_style_service() -> None:
    """Demonstrate emotion to style mapping."""
    print_header("EmotionStyleService (Domain Service)")

    service = EmotionStyleService()

    emotions = [
        (EmotionType.NEUTRAL, 0.5),
        (EmotionType.HAPPY, 0.8),
        (EmotionType.SAD, 0.6),
        (EmotionType.ANGRY, 0.9),
        (EmotionType.EXCITED, 0.7),
    ]

    print("Emotion -> Style Mapping:")
    for emotion_type, intensity in emotions:
        state = EmotionState(primary=emotion_type, intensity=intensity)
        style = service.get_style_for_emotion(state)
        weight = service.get_style_weight(state)
        print(f"  {emotion_type.value:12} (intensity={intensity}) -> Style: {style:10} Weight: {weight:.1f}")

    print(f"\nAvailable styles: {service.get_available_styles()}")


def demo_domain_events() -> None:
    """Demonstrate domain events."""
    print_header("Domain Events")

    started = SpeechStartedEvent(
        text="Hello from AI",
        source="commentary",
        style="Happy",
    )
    print(f"SpeechStartedEvent:")
    print(f"  event_type: {started.event_type}")
    print(f"  event_id: {started.event_id[:8]}...")
    print(f"  occurred_at: {started.occurred_at}")
    print(f"  text: {started.text}")
    print(f"  source: {started.source}")
    print(f"  style: {started.style}")

    completed = SpeechCompletedEvent(
        text="Hello from AI",
        source="commentary",
        completed=True,
        duration_ms=1200,
    )
    print(f"\nSpeechCompletedEvent:")
    print(f"  completed: {completed.completed}")
    print(f"  duration_ms: {completed.duration_ms}")


async def demo_use_case() -> None:
    """Demonstrate SpeakTextUseCase with mocked dependencies."""
    print_header("SpeakTextUseCase (Application Layer)")

    # Create mock adapters
    mock_synthesizer = AsyncMock()
    mock_synthesizer.synthesize.return_value = b"fake_audio_data"

    mock_audio_player = AsyncMock()
    mock_audio_player.play.return_value = True
    mock_audio_player.is_playing = Mock(return_value=False)
    mock_audio_player.get_duration_ms = Mock(return_value=1500)
    mock_audio_player.stop = Mock()

    # Use real event bus
    event_bus = AsyncEventBus()
    events_received: List[str] = []

    async def on_started(event: SpeechStartedEvent) -> None:
        events_received.append(f"Started: {event.text[:20]}...")

    async def on_completed(event: SpeechCompletedEvent) -> None:
        events_received.append(f"Completed: {event.text[:20]}... (completed={event.completed})")

    event_bus.subscribe(SpeechStartedEvent, on_started)
    event_bus.subscribe(SpeechCompletedEvent, on_completed)

    # Create emotion state holder
    current_emotion = EmotionState(primary=EmotionType.HAPPY, intensity=0.7)

    # Create use case
    use_case = SpeakTextUseCase(
        synthesizer=mock_synthesizer,
        audio_player=mock_audio_player,
        event_publisher=event_bus,
        emotion_style_service=EmotionStyleService(),
        get_current_emotion=lambda: current_emotion,
    )

    # Execute use case
    print("Executing speak text use case...")
    request = SpeakTextRequest(
        text="This is a test of the TTS system.",
        source="demo",
    )
    response = await use_case.execute(request)

    print(f"\nResponse:")
    print(f"  success: {response.success}")
    print(f"  message: {response.message}")
    print(f"  duration_ms: {response.duration_ms}")

    # Wait for events to be processed
    await asyncio.sleep(0.1)

    print(f"\nEvents received:")
    for event in events_received:
        print(f"  - {event}")

    # Verify mock calls
    print(f"\nMock verification:")
    print(f"  synthesizer.synthesize called: {mock_synthesizer.synthesize.called}")
    call_args = mock_synthesizer.synthesize.call_args
    print(f"  - style used: {call_args.kwargs['style']}")  # Should be "Happy" from emotion


async def demo_tts_service() -> None:
    """Demonstrate TTSService presentation layer."""
    print_header("TTSService (Presentation Layer)")

    # Create mock use case
    mock_use_case = AsyncMock()
    mock_use_case.execute.return_value = SpeakTextResponse.ok(duration_ms=1000)

    # Create service
    tts_service = TTSService(
        speak_text_use_case=mock_use_case,
        max_queue_size=5,
    )

    print("Starting TTS service...")
    await tts_service.start()
    print(f"  is_running: {tts_service.is_running()}")

    # Queue some speech
    print("\nQueuing speech requests...")
    responses = []

    # Normal priority goes to queue
    response = await tts_service.speak(
        text="First message, normal priority",
        priority=SpeechPriority.NORMAL,
        source="demo",
    )
    responses.append(("NORMAL", response))

    response = await tts_service.speak(
        text="Second message, high priority",
        priority=SpeechPriority.HIGH,
        source="demo",
    )
    responses.append(("HIGH", response))

    print(f"  Queue size after adding: {tts_service.get_queue_size()}")

    for priority, resp in responses:
        print(f"  [{priority}] queued={resp.queued}, success={resp.success}")

    # Interrupt priority - processed immediately
    print("\nSending interrupt priority message...")
    response = await tts_service.speak_now(
        text="Interrupt! Chat response",
        source="chat",
    )
    print(f"  Interrupt response: success={response.success}, queued={response.queued}")

    # Let queue process
    await asyncio.sleep(0.2)

    print("\nStopping TTS service...")
    await tts_service.stop()
    print(f"  is_running: {tts_service.is_running()}")


async def demo_event_bus_integration() -> None:
    """Demonstrate event bus integration for speech events."""
    print_header("Event Bus Integration")

    event_bus = AsyncEventBus()

    # Simulate external components listening for speech events
    speech_log: List[str] = []

    async def log_speech_started(event: SpeechStartedEvent) -> None:
        speech_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] STARTED: {event.text}")

    async def log_speech_completed(event: SpeechCompletedEvent) -> None:
        status = "completed" if event.completed else "interrupted"
        speech_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {status.upper()}: {event.text}")

    event_bus.subscribe(SpeechStartedEvent, log_speech_started)
    event_bus.subscribe(SpeechCompletedEvent, log_speech_completed)

    print("Publishing speech events...")

    await event_bus.publish(SpeechStartedEvent(
        text="Hello viewers!",
        source="greeting",
        style="Happy",
    ))

    await event_bus.publish(SpeechCompletedEvent(
        text="Hello viewers!",
        source="greeting",
        completed=True,
        duration_ms=800,
    ))

    await event_bus.publish(SpeechStartedEvent(
        text="Let me explain this...",
        source="commentary",
        style="Neutral",
    ))

    await event_bus.publish(SpeechCompletedEvent(
        text="Let me explain this...",
        source="commentary",
        completed=False,  # Interrupted
        duration_ms=0,
    ))

    await asyncio.sleep(0.1)

    print("\nSpeech log (from external listener):")
    for entry in speech_log:
        print(f"  {entry}")


async def main() -> None:
    """Run all demos."""
    print("\n" + "=" * 60)
    print("  AILoveShen Phase 2: TTS Pipeline Demo")
    print("=" * 60)

    # Domain layer demos
    demo_domain_value_objects()
    demo_emotion_style_service()
    demo_domain_events()

    # Application layer demo
    await demo_use_case()

    # Presentation layer demo
    await demo_tts_service()

    # Integration demo
    await demo_event_bus_integration()

    print_header("Demo Complete")
    print("Phase 2 TTS Pipeline components demonstrated successfully!")
    print("\nComponents tested:")
    print("  - Domain: SpeechResult, VoiceConfig, SpeechStatus")
    print("  - Domain: EmotionStyleService")
    print("  - Domain: SpeechStartedEvent, SpeechCompletedEvent")
    print("  - Application: SpeakTextUseCase, SpeakTextRequest/Response")
    print("  - Presentation: TTSService with queue management")
    print("  - Infrastructure: Mocked StyleBertVits2Client, SounddevicePlayer")
    print("\nFor real TTS, install dependencies and run Style-Bert-VITS2 server:")
    print("  pip install ailoveshen[tts]")
    print("  python server_fastapi.py")


if __name__ == "__main__":
    asyncio.run(main())
