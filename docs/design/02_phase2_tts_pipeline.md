# Phase 2: TTS Pipeline 詳細設計書 (Issue #5)

## 1. 概要

Style-Bert-VITS2を使用したリアルタイム音声合成パイプラインを実装します。

### 要件（Issue #5より）
- Style-Bert-VITS2との連携（既存FastAPIサーバー使用）
- テキスト生成から音声出力までの低レイテンシ
- 感情・スタイル制御（MCP連携）

## 2. クリーンアーキテクチャに基づくコンポーネント構成

```
src/ailoveshen/
├── domain/
│   ├── value_objects/
│   │   ├── speech_request.py      # SpeechRequest, SpeechPriority
│   │   └── speech_result.py       # SpeechResult
│   └── services/
│       └── emotion_style_service.py  # EmotionStyleService
│
├── application/
│   ├── ports/
│   │   ├── input/
│   │   │   └── speak_text.py      # ISpeakText use case interface
│   │   └── output/
│   │       ├── speech_synthesizer.py  # ISpeechSynthesizer
│   │       └── audio_player.py        # IAudioPlayer
│   ├── use_cases/
│   │   └── speak_text.py          # SpeakTextUseCase
│   └── dto/
│       └── speech_dto.py          # SpeakTextRequest/Response
│
├── infrastructure/
│   └── adapters/
│       ├── tts/
│       │   └── style_bert_vits2_client.py  # StyleBertVits2Client
│       └── audio/
│           └── sounddevice_player.py       # SounddevicePlayer
│
└── presentation/
    └── services/
        └── tts_service.py         # TTSService (coordinates use case)
```

## 3. Domain Layer

### 3.1 Value Objects

#### SpeechRequest (domain/value_objects/speech_request.py)

```python
"""Speech request value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Optional


class SpeechPriority(IntEnum):
    """Speech priority levels."""
    LOW = 0         # Background commentary
    NORMAL = 1      # Regular commentary
    HIGH = 2        # Important game events
    INTERRUPT = 3   # Chat responses (interrupts current speech)


@dataclass(frozen=True)
class SpeechRequest:
    """
    Value object representing a speech synthesis request.

    Immutable to ensure integrity.
    """
    text: str
    style: str = "Neutral"
    speaker_id: int = 0
    language: str = "JP"
    priority: SpeechPriority = SpeechPriority.NORMAL
    source: str = ""
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        """Validate the request."""
        if not self.text or not self.text.strip():
            raise ValueError("Speech text cannot be empty")

    def is_interrupt(self) -> bool:
        """Check if this request should interrupt current speech."""
        return self.priority >= SpeechPriority.INTERRUPT

    def with_style(self, style: str) -> SpeechRequest:
        """Create new request with different style."""
        return SpeechRequest(
            text=self.text,
            style=style,
            speaker_id=self.speaker_id,
            language=self.language,
            priority=self.priority,
            source=self.source,
            created_at=self.created_at,
        )
```

#### SpeechResult (domain/value_objects/speech_result.py)

```python
"""Speech result value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class SpeechStatus(str, Enum):
    """Speech playback status."""
    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    PLAYING = "playing"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass(frozen=True)
class SpeechResult:
    """
    Value object representing speech synthesis/playback result.
    """
    request_text: str
    status: SpeechStatus
    audio_duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    completed_at: Optional[datetime] = None

    @classmethod
    def completed(cls, text: str, duration_ms: int) -> SpeechResult:
        """Create a completed result."""
        return cls(
            request_text=text,
            status=SpeechStatus.COMPLETED,
            audio_duration_ms=duration_ms,
            completed_at=datetime.now(),
        )

    @classmethod
    def interrupted(cls, text: str) -> SpeechResult:
        """Create an interrupted result."""
        return cls(
            request_text=text,
            status=SpeechStatus.INTERRUPTED,
            completed_at=datetime.now(),
        )

    @classmethod
    def failed(cls, text: str, error: str) -> SpeechResult:
        """Create a failed result."""
        return cls(
            request_text=text,
            status=SpeechStatus.FAILED,
            error_message=error,
            completed_at=datetime.now(),
        )
```

### 3.2 Domain Services

#### EmotionStyleService (domain/services/emotion_style_service.py)

```python
"""Domain service for emotion to style mapping."""

from __future__ import annotations

from ailoveshen.domain.value_objects.emotion import EmotionState, EmotionType


class EmotionStyleService:
    """
    Domain service that maps emotion states to TTS voice styles.

    This is a domain service because:
    - It encapsulates domain logic (emotion → style mapping)
    - It operates on domain value objects
    - It doesn't depend on external services
    """

    # Default mapping - can be overridden via config
    DEFAULT_STYLE_MAP = {
        EmotionType.NEUTRAL: "Neutral",
        EmotionType.HAPPY: "Happy",
        EmotionType.SAD: "Sad",
        EmotionType.ANGRY: "Angry",
        EmotionType.SURPRISED: "Surprised",
        EmotionType.SCARED: "Sad",       # Fallback
        EmotionType.DISGUSTED: "Angry",  # Fallback
        EmotionType.EXCITED: "Happy",    # Fallback
        EmotionType.CALM: "Neutral",     # Fallback
    }

    def __init__(self, style_map: dict[EmotionType, str] | None = None) -> None:
        """
        Initialize with optional custom style mapping.

        Args:
            style_map: Custom emotion to style mapping
        """
        self._style_map = style_map or self.DEFAULT_STYLE_MAP.copy()

    def get_style_for_emotion(self, emotion_state: EmotionState) -> str:
        """
        Get the appropriate TTS style for the given emotion state.

        Args:
            emotion_state: Current emotion state

        Returns:
            TTS style name
        """
        return self._style_map.get(emotion_state.primary, "Neutral")

    def get_style_intensity(self, emotion_state: EmotionState) -> float:
        """
        Get style intensity based on emotion intensity.

        Higher emotion intensity = stronger style application.

        Args:
            emotion_state: Current emotion state

        Returns:
            Style weight (0.0 - 10.0)
        """
        return emotion_state.intensity * 10.0
```

## 4. Application Layer

### 4.1 Output Ports (Interfaces)

#### ISpeechSynthesizer (application/ports/output/speech_synthesizer.py)

```python
"""Speech synthesizer output port."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ISpeechSynthesizer(ABC):
    """
    Output port for speech synthesis.

    This interface is defined in the Application layer.
    Infrastructure adapters implement this interface.
    """

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        style: str = "Neutral",
        speaker_id: int = 0,
        language: str = "JP",
    ) -> bytes:
        """
        Synthesize speech from text.

        Args:
            text: Text to synthesize
            style: Voice style
            speaker_id: Speaker ID
            language: Language code

        Returns:
            Audio data as bytes (WAV format)

        Raises:
            SynthesisError: If synthesis fails
        """
        ...

    @abstractmethod
    async def get_available_styles(self) -> list[str]:
        """Get list of available voice styles."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to synthesis service."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to synthesis service."""
        ...
```

#### IAudioPlayer (application/ports/output/audio_player.py)

```python
"""Audio player output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from typing import Optional


class IAudioPlayer(ABC):
    """
    Output port for audio playback.

    This interface is defined in the Application layer.
    Infrastructure adapters implement this interface.
    """

    @abstractmethod
    async def play(
        self,
        audio_data: bytes,
        interrupt_event: Optional[asyncio.Event] = None,
    ) -> bool:
        """
        Play audio data.

        Args:
            audio_data: Audio data to play (WAV format)
            interrupt_event: Event to monitor for interruption

        Returns:
            True if playback completed, False if interrupted
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop current playback immediately."""
        ...

    @abstractmethod
    def is_playing(self) -> bool:
        """Check if audio is currently playing."""
        ...
```

### 4.2 Input Port

#### ISpeakText (application/ports/input/speak_text.py)

```python
"""Speak text input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.speech_dto import (
    SpeakTextRequest,
    SpeakTextResponse,
)


class ISpeakText(ABC):
    """
    Input port for text-to-speech use case.

    Presentation layer uses this interface to request speech.
    """

    @abstractmethod
    async def execute(self, request: SpeakTextRequest) -> SpeakTextResponse:
        """
        Execute speak text use case.

        Args:
            request: Speech request parameters

        Returns:
            Speech result
        """
        ...
```

### 4.3 DTOs

#### Speech DTOs (application/dto/speech_dto.py)

```python
"""Speech-related DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.value_objects.speech_request import SpeechPriority


@dataclass
class SpeakTextRequest:
    """Input DTO for speak text use case."""
    text: str
    priority: SpeechPriority = SpeechPriority.NORMAL
    style: Optional[str] = None  # None = use emotion-based style
    source: str = "unknown"


@dataclass
class SpeakTextResponse:
    """Output DTO for speak text use case."""
    success: bool
    queued: bool = False
    message: str = ""
    error: Optional[str] = None
```

### 4.4 Use Cases

#### SpeakTextUseCase (application/use_cases/speak_text.py)

```python
"""Speak text use case implementation."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from loguru import logger

from ailoveshen.application.dto.speech_dto import (
    SpeakTextRequest,
    SpeakTextResponse,
)
from ailoveshen.application.ports.input.speak_text import ISpeakText
from ailoveshen.application.ports.output.audio_player import IAudioPlayer
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.speech_synthesizer import ISpeechSynthesizer
from ailoveshen.domain.events import DomainEvent, SpeechCompletedEvent, SpeechStartedEvent
from ailoveshen.domain.services.emotion_style_service import EmotionStyleService
from ailoveshen.domain.value_objects.emotion import EmotionState
from ailoveshen.domain.value_objects.speech_request import SpeechRequest


class SpeakTextUseCase(ISpeakText):
    """
    Use case for synthesizing and playing speech.

    Coordinates:
    - Style selection based on emotion
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
        Initialize use case with dependencies.

        Args:
            synthesizer: Speech synthesizer adapter
            audio_player: Audio player adapter
            event_publisher: Event publisher for domain events
            emotion_style_service: Domain service for emotion→style mapping
            get_current_emotion: Callable to get current emotion state
        """
        self._synthesizer = synthesizer
        self._audio_player = audio_player
        self._event_publisher = event_publisher
        self._emotion_style_service = emotion_style_service
        self._get_current_emotion = get_current_emotion

        self._interrupt_event = asyncio.Event()

    async def execute(self, request: SpeakTextRequest) -> SpeakTextResponse:
        """Execute the speak text use case."""
        try:
            # Determine style
            if request.style is None:
                emotion = self._get_current_emotion()
                style = self._emotion_style_service.get_style_for_emotion(emotion)
            else:
                style = request.style

            # Create domain value object
            speech_request = SpeechRequest(
                text=request.text,
                style=style,
                priority=request.priority,
                source=request.source,
            )

            # Check for interrupt
            if speech_request.is_interrupt():
                self._audio_player.stop()
                self._interrupt_event.set()

            # Synthesize
            logger.debug(f"Synthesizing: {request.text[:50]}...")
            audio_data = await self._synthesizer.synthesize(
                text=speech_request.text,
                style=speech_request.style,
                speaker_id=speech_request.speaker_id,
                language=speech_request.language,
            )

            # Publish started event
            await self._event_publisher.publish(
                SpeechStartedEvent(
                    text=speech_request.text,
                    source=speech_request.source,
                )
            )

            # Play audio
            completed = await self._audio_player.play(
                audio_data,
                interrupt_event=self._interrupt_event,
            )

            # Clear interrupt flag
            self._interrupt_event.clear()

            # Publish completed/interrupted event
            await self._event_publisher.publish(
                SpeechCompletedEvent(
                    text=speech_request.text,
                    source=speech_request.source,
                    completed=completed,
                )
            )

            return SpeakTextResponse(
                success=True,
                message="Speech completed" if completed else "Speech interrupted",
            )

        except Exception as e:
            logger.error(f"Speech failed: {e}")
            return SpeakTextResponse(
                success=False,
                error=str(e),
            )
```

## 5. Infrastructure Layer

### 5.1 TTS Adapter

#### StyleBertVits2Client (infrastructure/adapters/tts/style_bert_vits2_client.py)

```python
"""Style-Bert-VITS2 HTTP client adapter."""

from __future__ import annotations

from typing import Optional

import httpx
from loguru import logger

from ailoveshen.application.ports.output.speech_synthesizer import ISpeechSynthesizer
from ailoveshen.core.exceptions import SynthesisError


class StyleBertVits2Client(ISpeechSynthesizer):
    """
    Infrastructure adapter for Style-Bert-VITS2 TTS server.

    Implements ISpeechSynthesizer output port.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5000,
        timeout_seconds: float = 30.0,
        model_name: str = "default",
    ) -> None:
        self._base_url = f"http://{host}:{port}"
        self._timeout = timeout_seconds
        self._model_name = model_name
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """Initialize HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )
        logger.info(f"TTS client connected to {self._base_url}")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("TTS client disconnected")

    async def synthesize(
        self,
        text: str,
        style: str = "Neutral",
        speaker_id: int = 0,
        language: str = "JP",
    ) -> bytes:
        """Synthesize speech using Style-Bert-VITS2 server."""
        if not self._client:
            raise SynthesisError("TTS client not connected")

        try:
            response = await self._client.get(
                "/voice",
                params={
                    "text": text,
                    "model_name": self._model_name,
                    "speaker_id": speaker_id,
                    "style": style,
                    "language": language,
                    "sdp_ratio": 0.2,
                    "noise": 0.6,
                    "noisew": 0.8,
                    "length": 1.0,
                },
            )
            response.raise_for_status()
            return response.content

        except httpx.HTTPStatusError as e:
            raise SynthesisError(
                f"TTS synthesis failed: {e.response.status_code}"
            )
        except httpx.RequestError as e:
            raise SynthesisError(f"TTS connection error: {e}")

    async def get_available_styles(self) -> list[str]:
        """Get available styles from server."""
        if not self._client:
            return ["Neutral"]

        try:
            response = await self._client.get("/models/info")
            response.raise_for_status()
            data = response.json()

            if self._model_name in data:
                return list(data[self._model_name].get("style2id", {}).keys())
            return ["Neutral"]

        except Exception as e:
            logger.warning(f"Failed to get styles: {e}")
            return ["Neutral"]
```

### 5.2 Audio Adapter

#### SounddevicePlayer (infrastructure/adapters/audio/sounddevice_player.py)

```python
"""Sounddevice-based audio player adapter."""

from __future__ import annotations

import asyncio
import io
from typing import Optional

import numpy as np
import sounddevice as sd
from scipy.io import wavfile
from loguru import logger

from ailoveshen.application.ports.output.audio_player import IAudioPlayer


class SounddevicePlayer(IAudioPlayer):
    """
    Infrastructure adapter for audio playback using sounddevice.

    Implements IAudioPlayer output port.
    """

    def __init__(self, sample_rate: int = 44100) -> None:
        self._sample_rate = sample_rate
        self._playing = False
        self._stop_event = asyncio.Event()

    async def play(
        self,
        audio_data: bytes,
        interrupt_event: Optional[asyncio.Event] = None,
    ) -> bool:
        """Play audio data with interrupt support."""
        try:
            # Parse WAV data
            sample_rate, audio_array = wavfile.read(io.BytesIO(audio_data))

            # Normalize to float32
            if audio_array.dtype == np.int16:
                audio_array = audio_array.astype(np.float32) / 32768.0
            elif audio_array.dtype == np.int32:
                audio_array = audio_array.astype(np.float32) / 2147483648.0

            self._playing = True
            self._stop_event.clear()

            duration = len(audio_array) / sample_rate
            sd.play(audio_array, sample_rate)

            # Wait with interrupt checking
            elapsed = 0.0
            check_interval = 0.1

            while elapsed < duration:
                if interrupt_event and interrupt_event.is_set():
                    sd.stop()
                    logger.info("Audio playback interrupted")
                    return False

                if self._stop_event.is_set():
                    sd.stop()
                    return False

                await asyncio.sleep(check_interval)
                elapsed += check_interval

            sd.wait()
            return True

        except asyncio.CancelledError:
            sd.stop()
            raise
        except Exception as e:
            logger.error(f"Audio playback error: {e}")
            return False
        finally:
            self._playing = False

    def stop(self) -> None:
        """Stop current playback."""
        self._stop_event.set()
        sd.stop()

    def is_playing(self) -> bool:
        """Check if currently playing."""
        return self._playing
```

## 6. Presentation Layer

### 6.1 TTS Service

#### TTSService (presentation/services/tts_service.py)

```python
"""TTS service for presentation layer."""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from ailoveshen.application.dto.speech_dto import SpeakTextRequest
from ailoveshen.application.ports.input.speak_text import ISpeakText
from ailoveshen.domain.value_objects.speech_request import SpeechPriority


class TTSService:
    """
    Presentation layer service for TTS.

    Provides a simple interface for other components to request speech.
    Manages the speech queue and processing loop.
    """

    def __init__(
        self,
        speak_text_use_case: ISpeakText,
        max_queue_size: int = 10,
    ) -> None:
        """
        Initialize TTS service.

        Args:
            speak_text_use_case: Use case for speaking text
            max_queue_size: Maximum queue size
        """
        self._use_case = speak_text_use_case
        self._queue: asyncio.Queue[SpeakTextRequest] = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the TTS service processing loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("TTS service started")

    async def stop(self) -> None:
        """Stop the TTS service."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("TTS service stopped")

    async def speak(
        self,
        text: str,
        priority: SpeechPriority = SpeechPriority.NORMAL,
        style: Optional[str] = None,
        source: str = "unknown",
    ) -> None:
        """
        Queue text for speech.

        Args:
            text: Text to speak
            priority: Speech priority
            style: Override style (None = use emotion-based)
            source: Source identifier
        """
        if not text.strip():
            return

        request = SpeakTextRequest(
            text=text,
            priority=priority,
            style=style,
            source=source,
        )

        # Handle interrupt priority - process immediately
        if priority >= SpeechPriority.INTERRUPT:
            await self._use_case.execute(request)
            return

        # Queue for normal processing
        try:
            self._queue.put_nowait(request)
            logger.debug(f"Queued speech: {text[:30]}...")
        except asyncio.QueueFull:
            logger.warning("Speech queue full, dropping oldest item")
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(request)
            except asyncio.QueueEmpty:
                pass

    async def _process_loop(self) -> None:
        """Main processing loop."""
        while self._running:
            try:
                request = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=1.0,
                )
                await self._use_case.execute(request)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"TTS processing error: {e}")
                await asyncio.sleep(1)
```

## 7. Composition Root (TTS部分)

```python
# src/ailoveshen/main.py (TTS部分の抜粋)

from ailoveshen.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.domain.services.emotion_style_service import EmotionStyleService
from ailoveshen.domain.value_objects.emotion import EmotionState
from ailoveshen.infrastructure.adapters.audio.sounddevice_player import SounddevicePlayer
from ailoveshen.infrastructure.adapters.tts.style_bert_vits2_client import StyleBertVits2Client
from ailoveshen.presentation.services.tts_service import TTSService


def create_tts_service(
    config: TTSConfig,
    event_publisher: IEventPublisher,
    emotion_state_holder: EmotionStateHolder,
) -> TTSService:
    """Create TTS service with all dependencies."""

    # Infrastructure adapters
    synthesizer = StyleBertVits2Client(
        host=config.server.host,
        port=config.server.port,
        timeout_seconds=config.server.timeout_seconds,
        model_name=config.voice.model,
    )

    audio_player = SounddevicePlayer(
        sample_rate=config.audio.sample_rate,
    )

    # Domain service
    emotion_style_service = EmotionStyleService(
        style_map=config.emotion_style_map,
    )

    # Use case
    speak_text_use_case = SpeakTextUseCase(
        synthesizer=synthesizer,
        audio_player=audio_player,
        event_publisher=event_publisher,
        emotion_style_service=emotion_style_service,
        get_current_emotion=emotion_state_holder.get_current,
    )

    # Presentation service
    return TTSService(
        speak_text_use_case=speak_text_use_case,
        max_queue_size=config.queue_size,
    )
```

## 8. データフロー図（Clean Architecture版）

```
┌────────────────────────────────────────────────────────────────────────┐
│                         PRESENTATION LAYER                              │
│  ┌──────────────┐                                                       │
│  │  TTSService  │  ← 他のコンポーネントからのspeak()呼び出し              │
│  └──────┬───────┘                                                       │
└─────────┼──────────────────────────────────────────────────────────────┘
          │ SpeakTextRequest
          ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         APPLICATION LAYER                               │
│  ┌────────────────────┐                                                 │
│  │  ISpeakText        │ ← Input Port (Interface)                        │
│  └─────────┬──────────┘                                                 │
│            │                                                            │
│  ┌─────────▼──────────┐                                                 │
│  │ SpeakTextUseCase   │                                                 │
│  │  - synthesizer     │→ ISpeechSynthesizer (Output Port)               │
│  │  - audio_player    │→ IAudioPlayer (Output Port)                     │
│  │  - event_publisher │→ IEventPublisher (Output Port)                  │
│  └─────────┬──────────┘                                                 │
│            │                                                            │
└────────────┼────────────────────────────────────────────────────────────┘
             │
┌────────────┼────────────────────────────────────────────────────────────┐
│            │                  DOMAIN LAYER                              │
│  ┌─────────▼──────────┐  ┌─────────────────────┐                        │
│  │ EmotionStyleService│  │   SpeechRequest     │                        │
│  │   (Domain Service) │  │   (Value Object)    │                        │
│  └────────────────────┘  └─────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                       INFRASTRUCTURE LAYER                               │
│  ┌───────────────────────┐    ┌────────────────────┐                     │
│  │ StyleBertVits2Client  │    │  SounddevicePlayer │                     │
│  │ (implements           │    │  (implements       │                     │
│  │  ISpeechSynthesizer)  │    │   IAudioPlayer)    │                     │
│  └───────────┬───────────┘    └─────────┬──────────┘                     │
│              │                          │                                │
│              ▼                          ▼                                │
│    [Style-Bert-VITS2 Server]    [Audio Hardware]                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## 9. 依存関係の方向

```
Presentation → Application ← Infrastructure
                   ↓
                Domain

- Presentation層はApplication層のInput Portに依存
- Infrastructure層はApplication層のOutput Portを実装
- Application層はDomain層のみに依存
- Domain層は何にも依存しない
```

## 10. 設定

```yaml
# config/default.yaml (TTS section)
tts:
  enabled: true

  server:
    host: "localhost"
    port: 5000
    timeout_seconds: 30

  voice:
    model: "default"
    speaker_id: 0
    language: "JP"

  queue_size: 10

  audio:
    sample_rate: 44100
    channels: 1

  # EmotionStyleService configuration
  emotion_style_map:
    neutral: "Neutral"
    happy: "Happy"
    sad: "Sad"
    angry: "Angry"
    surprised: "Surprised"
    scared: "Sad"
    disgusted: "Angry"
    excited: "Happy"
    calm: "Neutral"
```

## 11. テスト計画

### 11.1 Unit Tests

```python
# tests/unit/domain/test_speech_request.py
import pytest
from ailoveshen.domain.value_objects.speech_request import (
    SpeechRequest,
    SpeechPriority,
)


def test_speech_request_validation():
    """Test that empty text raises error."""
    with pytest.raises(ValueError):
        SpeechRequest(text="")


def test_speech_request_is_interrupt():
    """Test interrupt detection."""
    normal = SpeechRequest(text="test", priority=SpeechPriority.NORMAL)
    interrupt = SpeechRequest(text="test", priority=SpeechPriority.INTERRUPT)

    assert not normal.is_interrupt()
    assert interrupt.is_interrupt()


def test_speech_request_with_style():
    """Test immutable style modification."""
    original = SpeechRequest(text="test", style="Neutral")
    modified = original.with_style("Happy")

    assert original.style == "Neutral"
    assert modified.style == "Happy"
    assert modified.text == original.text
```

### 11.2 Integration Tests

```python
# tests/integration/test_tts_use_case.py
import pytest
from unittest.mock import AsyncMock, Mock

from ailoveshen.application.dto.speech_dto import SpeakTextRequest
from ailoveshen.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.domain.services.emotion_style_service import EmotionStyleService
from ailoveshen.domain.value_objects.emotion import EmotionState


@pytest.fixture
def mock_synthesizer():
    synthesizer = AsyncMock()
    synthesizer.synthesize.return_value = b"fake_audio_data"
    return synthesizer


@pytest.fixture
def mock_audio_player():
    player = AsyncMock()
    player.play.return_value = True
    return player


@pytest.mark.asyncio
async def test_speak_text_use_case(mock_synthesizer, mock_audio_player):
    """Test complete speak text flow."""
    use_case = SpeakTextUseCase(
        synthesizer=mock_synthesizer,
        audio_player=mock_audio_player,
        event_publisher=AsyncMock(),
        emotion_style_service=EmotionStyleService(),
        get_current_emotion=lambda: EmotionState(),
    )

    request = SpeakTextRequest(text="Hello")
    response = await use_case.execute(request)

    assert response.success
    mock_synthesizer.synthesize.assert_called_once()
    mock_audio_player.play.assert_called_once()
```
