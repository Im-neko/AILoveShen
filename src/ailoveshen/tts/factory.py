"""TTS module factory (Composition Root)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ailoveshen.core.application.ports.output_ports import IEventPublisher
from ailoveshen.core.domain.value_objects import EmotionState, EmotionType
from ailoveshen.tts.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.tts.domain.services.emotion_style_service import EmotionStyleService
from ailoveshen.tts.infrastructure.adapters.audio.sounddevice_player import (
    SounddevicePlayer,
)
from ailoveshen.tts.infrastructure.adapters.tts.style_bert_vits2_client import (
    StyleBertVits2Client,
)
from ailoveshen.tts.presentation.services.tts_service import TTSService


def _default_emotion_provider() -> EmotionState:
    """Default emotion provider returning neutral state."""
    return EmotionState(primary=EmotionType.NEUTRAL, intensity=0.5)


def _parse_emotion_style_map(
    config_map: Optional[Dict[str, str]],
) -> Optional[Dict[EmotionType, str]]:
    """
    Parse emotion style map from config format to EmotionType keys.

    Args:
        config_map: Map from string emotion names to style names

    Returns:
        Map from EmotionType to style names, or None if config_map is None
    """
    if not config_map:
        return None

    result = {}
    for emotion_str, style in config_map.items():
        try:
            emotion_type = EmotionType(emotion_str.lower())
            result[emotion_type] = style
        except ValueError:
            # Skip unknown emotion types
            pass

    return result


def create_tts_service(
    config: Dict[str, Any],
    event_publisher: IEventPublisher,
    get_current_emotion: Optional[Callable[[], EmotionState]] = None,
) -> TTSService:
    """
    Create TTS service with all dependencies wired up.

    This is the Composition Root for the TTS module.
    It creates and wires all components according to Clean Architecture.

    Args:
        config: TTS configuration dict (from YAML config["tts"])
        event_publisher: Event publisher for domain events
        get_current_emotion: Callable to get current emotion state.
                            If None, uses default neutral emotion.

    Returns:
        Configured TTSService ready to use

    Example:
        ```python
        from ailoveshen.core.infrastructure.config import Settings
        from ailoveshen.core.infrastructure.events import AsyncEventBus

        settings = Settings()
        event_bus = AsyncEventBus()
        tts_config = settings.get("tts", {})

        tts_service = create_tts_service(
            config=tts_config,
            event_publisher=event_bus,
        )

        await tts_service.start()
        await tts_service.speak("Hello!")
        await tts_service.stop()
        ```
    """
    # Extract config sections with defaults
    server_config = config.get("server", {})
    voice_config = config.get("voice", {})
    synthesis_config = config.get("synthesis", {})
    queue_config = config.get("queue", {})
    audio_config = config.get("audio", {})
    emotion_style_map_config = config.get("emotion_style_map")

    # Create infrastructure adapters
    synthesizer = StyleBertVits2Client(
        host=server_config.get("host", "localhost"),
        port=server_config.get("port", 5000),
        timeout_seconds=server_config.get("timeout_seconds", 30.0),
        model_name=voice_config.get("model_name", "default"),
        sdp_ratio=synthesis_config.get("sdp_ratio", 0.2),
        noise=synthesis_config.get("noise", 0.6),
        noisew=synthesis_config.get("noisew", 0.8),
        length=synthesis_config.get("length", 1.0),
    )

    audio_player = SounddevicePlayer(
        device=audio_config.get("device"),
        blocksize=audio_config.get("blocksize", 1024),
    )

    # Create domain service
    emotion_style_map = _parse_emotion_style_map(emotion_style_map_config)
    emotion_style_service = EmotionStyleService(
        style_map=emotion_style_map,
        default_style=voice_config.get("default_style", "Neutral"),
    )

    # Use provided emotion provider or default
    emotion_provider = get_current_emotion or _default_emotion_provider

    # Create use case
    speak_text_use_case = SpeakTextUseCase(
        synthesizer=synthesizer,
        audio_player=audio_player,
        event_publisher=event_publisher,
        emotion_style_service=emotion_style_service,
        get_current_emotion=emotion_provider,
    )

    # Create presentation service
    return TTSService(
        speak_text_use_case=speak_text_use_case,
        max_queue_size=queue_config.get("max_size", 10),
    )


async def create_and_connect_tts_service(
    config: Dict[str, Any],
    event_publisher: IEventPublisher,
    get_current_emotion: Optional[Callable[[], EmotionState]] = None,
) -> TTSService:
    """
    Create TTS service and connect to the TTS server.

    Convenience function that creates the service and establishes
    the connection to the TTS server.

    Args:
        config: TTS configuration dict
        event_publisher: Event publisher for domain events
        get_current_emotion: Callable to get current emotion state

    Returns:
        Connected and started TTSService

    Raises:
        ConnectionError: If connection to TTS server fails
    """
    # Create service
    tts_service = create_tts_service(
        config=config,
        event_publisher=event_publisher,
        get_current_emotion=get_current_emotion,
    )

    # Access internal synthesizer to connect
    # This is a slight violation of encapsulation, but necessary for setup
    use_case = tts_service._use_case  # type: ignore[attr-defined]
    if hasattr(use_case, "_synthesizer"):
        await use_case._synthesizer.connect()  # type: ignore[attr-defined]

    # Start the service
    await tts_service.start()

    return tts_service
