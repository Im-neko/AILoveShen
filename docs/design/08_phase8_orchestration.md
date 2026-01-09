# Phase 8: Orchestration 詳細設計書

## 1. 概要

全コンポーネントを統合し、メインループ（ゲーム実況）とサブループ（コメント対応）を実装します。クリーンアーキテクチャの原則に従い、各コンポーネント間の依存関係を抽象化します。

## 2. クリーンアーキテクチャ構成

```
src/ailoveshen/orchestrator/
├── domain/
│   ├── __init__.py
│   ├── entities.py           # StreamSession Entity
│   ├── value_objects.py      # StreamStatus, LoopStatus, SessionStatistics
│   └── services.py           # PriorityCalculationService, InterruptionPolicyService
├── application/
│   ├── __init__.py
│   ├── ports/
│   │   ├── __init__.py
│   │   ├── input_ports.py    # IStartStreaming, IStopStreaming, etc.
│   │   └── output_ports.py   # ITTSService, ILLMService, etc. (外部サービスへの依存)
│   ├── use_cases/
│   │   ├── __init__.py
│   │   ├── start_streaming.py
│   │   ├── stop_streaming.py
│   │   ├── main_loop.py      # RunMainLoopUseCase
│   │   └── sub_loop.py       # RunSubLoopUseCase
│   └── dto.py                # StreamingRequest, SessionStatus, etc.
├── infrastructure/
│   ├── __init__.py
│   └── adapters/
│       ├── __init__.py
│       ├── tts_adapter.py       # Phase 2 TTSService への Adapter
│       ├── llm_adapter.py       # Phase 3 LLMService への Adapter
│       ├── twitch_adapter.py    # Phase 4 TwitchService への Adapter
│       ├── game_adapter.py      # Phase 6 GameService への Adapter
│       └── obs_adapter.py       # Phase 7 OBSService への Adapter
├── presentation/
│   ├── __init__.py
│   └── orchestrator_service.py  # OrchestratorService
└── main.py                      # Composition Root
```

## 3. アーキテクチャ図

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                               Presentation Layer                                     │
│  ┌───────────────────────────────────────────────────────────────────────────────┐  │
│  │                           OrchestratorService                                  │  │
│  │   - start_session()                                                            │  │
│  │   - stop_session()                                                             │  │
│  │   - get_status()                                                               │  │
│  └───────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────┬────────────────────────────────────────────┘
                                         │ uses
┌────────────────────────────────────────▼────────────────────────────────────────────┐
│                               Application Layer                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │                              Input Ports                                     │    │
│  │   <<interface>>        <<interface>>         <<interface>>                   │    │
│  │   IStartStreaming      IStopStreaming        IGetSessionStatus               │    │
│  │   IRunMainLoop         IRunSubLoop                                           │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │                              Use Cases                                       │    │
│  │   StartStreamingUseCase   StopStreamingUseCase   GetSessionStatusUseCase     │    │
│  │   RunMainLoopUseCase      RunSubLoopUseCase                                  │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │                              Output Ports                                    │    │
│  │   <<interface>>        <<interface>>         <<interface>>                   │    │
│  │   ITTSService          ILLMService           ITwitchService                  │    │
│  │   IGameService         IOBSService           IEventPublisher                 │    │
│  │   ISessionRepository                                                         │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────┬────────────────────────────────────────────┘
                                         │ implements
┌────────────────────────────────────────▼────────────────────────────────────────────┐
│                             Infrastructure Layer                                     │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │                              Adapters                                        │    │
│  │   TTSServiceAdapter    LLMServiceAdapter     TwitchServiceAdapter            │    │
│  │   GameServiceAdapter   OBSServiceAdapter     InMemorySessionRepository       │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
│                                         │                                            │
│                                         │ uses (Phase 2-7 services)                  │
│                                         ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │   TTSService   LLMService   TwitchService   GameService   OBSService        │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼────────────────────────────────────────────┐
│                                Domain Layer                                          │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────────────────┐    │
│  │      Entities      │  │   Value Objects   │  │       Domain Services         │    │
│  │   StreamSession    │  │   StreamStatus    │  │   PriorityCalculationService  │    │
│  │                    │  │   LoopStatus      │  │   InterruptionPolicyService   │    │
│  │                    │  │SessionStatistics  │  │                               │    │
│  └───────────────────┘  └───────────────────┘  └───────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

## 4. Domain Layer

### 4.1 Value Objects (value_objects.py)

```python
"""Orchestration domain value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class StreamStatus(str, Enum):
    """Stream status values."""
    OFFLINE = "offline"
    STARTING = "starting"
    LIVE = "live"
    ENDING = "ending"


class LoopStatus(str, Enum):
    """Loop execution status values."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class SpeechSource(str, Enum):
    """Speech source types."""
    COMMENTARY = "commentary"
    RESPONSE = "response"
    GAME_EVENT = "game_event"
    SYSTEM = "system"


class SpeechPriority(str, Enum):
    """Speech priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    INTERRUPT = "interrupt"


@dataclass(frozen=True)
class SessionStatistics:
    """Session statistics value object."""
    commentary_count: int
    response_count: int
    messages_received: int
    messages_filtered: int

    def increment_commentary(self) -> SessionStatistics:
        """Return new instance with incremented commentary count."""
        return SessionStatistics(
            commentary_count=self.commentary_count + 1,
            response_count=self.response_count,
            messages_received=self.messages_received,
            messages_filtered=self.messages_filtered,
        )

    def increment_response(self) -> SessionStatistics:
        """Return new instance with incremented response count."""
        return SessionStatistics(
            commentary_count=self.commentary_count,
            response_count=self.response_count + 1,
            messages_received=self.messages_received,
            messages_filtered=self.messages_filtered,
        )

    def increment_messages(self, filtered: bool = False) -> SessionStatistics:
        """Return new instance with incremented message counts."""
        return SessionStatistics(
            commentary_count=self.commentary_count,
            response_count=self.response_count,
            messages_received=self.messages_received + 1,
            messages_filtered=self.messages_filtered + (1 if filtered else 0),
        )

    @classmethod
    def initial(cls) -> SessionStatistics:
        """Create initial statistics."""
        return cls(
            commentary_count=0,
            response_count=0,
            messages_received=0,
            messages_filtered=0,
        )


@dataclass(frozen=True)
class SpeechRequest:
    """Speech request value object."""
    text: str
    priority: SpeechPriority
    source: SpeechSource
    emotion: Optional[str] = None


@dataclass(frozen=True)
class LoopConfiguration:
    """Loop configuration value object."""
    interval_seconds: float
    interrupt_enabled: bool = False
    max_queue_size: int = 10
```

### 4.2 Entities (entities.py)

```python
"""Orchestration domain entities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .value_objects import (
    LoopStatus,
    SessionStatistics,
    SpeechSource,
    StreamStatus,
)


@dataclass
class StreamSession:
    """
    Stream session entity.

    Represents the state of an active streaming session.
    Has identity (session_id) and mutable state.
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Stream state
    stream_status: StreamStatus = StreamStatus.OFFLINE
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    # Loop states
    main_loop_status: LoopStatus = LoopStatus.IDLE
    sub_loop_status: LoopStatus = LoopStatus.IDLE

    # Statistics
    statistics: SessionStatistics = field(default_factory=SessionStatistics.initial)

    # Activity tracking
    last_commentary_at: Optional[datetime] = None
    last_response_at: Optional[datetime] = None

    # Speaking state
    is_speaking: bool = False
    current_speech_source: Optional[SpeechSource] = None

    def start(self) -> None:
        """Start the streaming session."""
        self.stream_status = StreamStatus.STARTING
        self.started_at = datetime.now()

    def go_live(self) -> None:
        """Transition to live status."""
        self.stream_status = StreamStatus.LIVE

    def begin_ending(self) -> None:
        """Begin ending the stream."""
        self.stream_status = StreamStatus.ENDING

    def end(self) -> None:
        """End the streaming session."""
        self.stream_status = StreamStatus.OFFLINE
        self.ended_at = datetime.now()
        self.main_loop_status = LoopStatus.IDLE
        self.sub_loop_status = LoopStatus.IDLE

    def set_main_loop_status(self, status: LoopStatus) -> None:
        """Set main loop status."""
        self.main_loop_status = status

    def set_sub_loop_status(self, status: LoopStatus) -> None:
        """Set sub loop status."""
        self.sub_loop_status = status

    def begin_speaking(self, source: SpeechSource) -> None:
        """Mark speech as started."""
        self.is_speaking = True
        self.current_speech_source = source

    def end_speaking(self) -> None:
        """Mark speech as ended."""
        self.is_speaking = False
        self.current_speech_source = None

    def record_commentary(self) -> None:
        """Record a commentary generation."""
        self.statistics = self.statistics.increment_commentary()
        self.last_commentary_at = datetime.now()

    def record_response(self) -> None:
        """Record a response generation."""
        self.statistics = self.statistics.increment_response()
        self.last_response_at = datetime.now()

    def record_message(self, filtered: bool) -> None:
        """Record a received message."""
        self.statistics = self.statistics.increment_messages(filtered)

    @property
    def is_live(self) -> bool:
        """Check if session is currently live."""
        return self.stream_status == StreamStatus.LIVE

    @property
    def can_generate_commentary(self) -> bool:
        """Check if commentary can be generated."""
        return (
            self.is_live
            and self.main_loop_status == LoopStatus.RUNNING
            and self.current_speech_source != SpeechSource.RESPONSE
        )

    @property
    def duration_seconds(self) -> Optional[float]:
        """Get session duration in seconds."""
        if not self.started_at:
            return None
        end = self.ended_at or datetime.now()
        return (end - self.started_at).total_seconds()
```

### 4.3 Domain Services (services.py)

```python
"""Orchestration domain services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from .value_objects import SpeechPriority


class GameStateProvider(Protocol):
    """Protocol for game state access."""

    @property
    def health(self) -> float: ...

    @property
    def max_health(self) -> float: ...

    @property
    def nearby_hostile_distance(self) -> Optional[float]: ...


class MessageProvider(Protocol):
    """Protocol for message access."""

    @property
    def content(self) -> str: ...

    @property
    def is_broadcaster(self) -> bool: ...

    @property
    def is_moderator(self) -> bool: ...


@dataclass(frozen=True)
class PriorityThresholds:
    """Priority calculation thresholds."""
    health_danger_ratio: float = 0.3
    hostile_danger_distance: float = 10.0


class PriorityCalculationService:
    """
    Domain service for calculating speech priority.

    Encapsulates the business logic for determining
    how urgent a speech request should be.
    """

    def __init__(self, thresholds: Optional[PriorityThresholds] = None) -> None:
        self.thresholds = thresholds or PriorityThresholds()

    def calculate_for_game_state(
        self,
        game_state: GameStateProvider,
    ) -> SpeechPriority:
        """Calculate priority based on game state."""
        # High priority for low health
        if game_state.health < game_state.max_health * self.thresholds.health_danger_ratio:
            return SpeechPriority.HIGH

        # High priority for nearby hostiles
        hostile_distance = game_state.nearby_hostile_distance
        if hostile_distance is not None and hostile_distance < self.thresholds.hostile_danger_distance:
            return SpeechPriority.HIGH

        return SpeechPriority.NORMAL


@dataclass(frozen=True)
class InterruptionPolicy:
    """Interruption policy configuration."""
    broadcaster_interrupts: bool = True
    moderator_interrupts: bool = True
    question_interrupts: bool = True
    name_mention_interrupts: bool = True
    ai_name: str = "shen"


class InterruptionPolicyService:
    """
    Domain service for determining interruption behavior.

    Encapsulates the business logic for when a chat response
    should interrupt ongoing speech.
    """

    def __init__(self, policy: Optional[InterruptionPolicy] = None) -> None:
        self.policy = policy or InterruptionPolicy()

    def should_interrupt(self, message: MessageProvider) -> bool:
        """Determine if message should interrupt current speech."""
        # Broadcaster/moderator always interrupts
        if self.policy.broadcaster_interrupts and message.is_broadcaster:
            return True
        if self.policy.moderator_interrupts and message.is_moderator:
            return True

        # Question interrupts
        if self.policy.question_interrupts and "?" in message.content:
            return True

        # Name mention interrupts
        if self.policy.name_mention_interrupts:
            if self.policy.ai_name.lower() in message.content.lower():
                return True

        return False
```

## 5. Application Layer

### 5.1 Output Ports (output_ports.py)

```python
"""Orchestration output port interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Optional

from ..domain.entities import StreamSession
from ..domain.value_objects import SpeechPriority, SpeechSource


class ITTSService(ABC):
    """Output port for TTS service operations."""

    @abstractmethod
    async def speak(
        self,
        text: str,
        priority: SpeechPriority,
        source: SpeechSource,
        emotion: Optional[str] = None,
    ) -> None:
        """Queue text for speech synthesis."""
        pass

    @abstractmethod
    async def stop_current(self) -> None:
        """Stop current speech."""
        pass

    @abstractmethod
    async def clear_queue(self) -> None:
        """Clear speech queue."""
        pass


class ILLMService(ABC):
    """Output port for LLM service operations."""

    @abstractmethod
    async def generate_commentary(
        self,
        game_state: dict[str, Any],
        emotion: Optional[str] = None,
    ) -> Optional[str]:
        """Generate game commentary."""
        pass

    @abstractmethod
    async def generate_response(
        self,
        message_content: str,
        user_name: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """Generate chat response."""
        pass

    @abstractmethod
    def update_emotion(self, emotion: str) -> None:
        """Update current emotion state."""
        pass


class IGameService(ABC):
    """Output port for game service operations."""

    @abstractmethod
    async def get_state(self) -> dict[str, Any]:
        """Get current game state."""
        pass

    @abstractmethod
    async def subscribe_events(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Subscribe to game events."""
        pass

    @abstractmethod
    async def unsubscribe_events(self) -> None:
        """Unsubscribe from game events."""
        pass


class ITwitchService(ABC):
    """Output port for Twitch service operations."""

    @abstractmethod
    async def subscribe_messages(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Subscribe to chat messages."""
        pass

    @abstractmethod
    async def unsubscribe_messages(self) -> None:
        """Unsubscribe from chat messages."""
        pass

    @abstractmethod
    async def should_respond(
        self,
        message: dict[str, Any],
    ) -> tuple[bool, float, str]:
        """Check if should respond to message."""
        pass


class IOBSService(ABC):
    """Output port for OBS service operations."""

    @abstractmethod
    async def start_stream(self) -> None:
        """Start OBS stream."""
        pass

    @abstractmethod
    async def stop_stream(self) -> None:
        """Stop OBS stream."""
        pass

    @abstractmethod
    async def switch_scene(self, scene_name: str) -> None:
        """Switch to specified scene."""
        pass


class ISessionRepository(ABC):
    """Output port for session persistence."""

    @abstractmethod
    async def save(self, session: StreamSession) -> None:
        """Save session state."""
        pass

    @abstractmethod
    async def get_current(self) -> Optional[StreamSession]:
        """Get current session."""
        pass

    @abstractmethod
    async def get_by_id(self, session_id: str) -> Optional[StreamSession]:
        """Get session by ID."""
        pass


class IEventPublisher(ABC):
    """Output port for publishing domain events."""

    @abstractmethod
    async def publish_session_started(self, session_id: str) -> None:
        """Publish session started event."""
        pass

    @abstractmethod
    async def publish_session_ended(self, session_id: str, statistics: dict) -> None:
        """Publish session ended event."""
        pass

    @abstractmethod
    async def publish_speech_started(self, source: str) -> None:
        """Publish speech started event."""
        pass

    @abstractmethod
    async def publish_speech_ended(self) -> None:
        """Publish speech ended event."""
        pass

    @abstractmethod
    async def publish_emotion_trigger(self, trigger: dict) -> None:
        """Publish emotion trigger event."""
        pass
```

### 5.2 Input Ports (input_ports.py)

```python
"""Orchestration input port interfaces (use case interfaces)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .dto import SessionStatusDTO, StreamingRequestDTO


class IStartStreaming(ABC):
    """Input port for starting a streaming session."""

    @abstractmethod
    async def execute(self, request: StreamingRequestDTO) -> str:
        """
        Start streaming session.

        Returns:
            Session ID
        """
        pass


class IStopStreaming(ABC):
    """Input port for stopping a streaming session."""

    @abstractmethod
    async def execute(self, session_id: Optional[str] = None) -> None:
        """Stop streaming session."""
        pass


class IGetSessionStatus(ABC):
    """Input port for getting session status."""

    @abstractmethod
    async def execute(self, session_id: Optional[str] = None) -> SessionStatusDTO:
        """Get session status."""
        pass


class IRunMainLoop(ABC):
    """Input port for running the main commentary loop."""

    @abstractmethod
    async def start(self) -> None:
        """Start the main loop."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the main loop."""
        pass

    @abstractmethod
    def pause(self) -> None:
        """Pause commentary generation."""
        pass

    @abstractmethod
    def resume(self) -> None:
        """Resume commentary generation."""
        pass


class IRunSubLoop(ABC):
    """Input port for running the sub (chat response) loop."""

    @abstractmethod
    async def start(self) -> None:
        """Start the sub loop."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the sub loop."""
        pass
```

### 5.3 DTOs (dto.py)

```python
"""Orchestration data transfer objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class StreamingRequestDTO:
    """Request to start streaming."""
    start_obs: bool = True
    start_main_loop: bool = True
    start_sub_loop: bool = True
    initial_scene: Optional[str] = None


@dataclass(frozen=True)
class SessionStatusDTO:
    """Session status response."""
    session_id: Optional[str]
    stream_status: str
    main_loop_status: str
    sub_loop_status: str
    is_speaking: bool
    current_speech_source: Optional[str]
    commentary_count: int
    response_count: int
    messages_received: int
    messages_filtered: int
    started_at: Optional[datetime]
    duration_seconds: Optional[float]


@dataclass(frozen=True)
class MainLoopConfigDTO:
    """Main loop configuration."""
    interval_seconds: float = 5.0


@dataclass(frozen=True)
class SubLoopConfigDTO:
    """Sub loop configuration."""
    interrupt_enabled: bool = True
    max_queue_size: int = 10


@dataclass(frozen=True)
class GameEventDTO:
    """Game event data."""
    event_type: str
    description: Optional[str]
    data: dict


@dataclass(frozen=True)
class ChatMessageDTO:
    """Chat message data."""
    message_id: str
    user_name: str
    content: str
    is_broadcaster: bool
    is_moderator: bool
    is_subscriber: bool
```

### 5.4 Use Cases

#### 5.4.1 StartStreamingUseCase (start_streaming.py)

```python
"""Start streaming use case."""

from __future__ import annotations

from loguru import logger

from ..domain.entities import StreamSession
from ..domain.value_objects import LoopStatus
from .dto import SessionStatusDTO, StreamingRequestDTO
from .ports.input_ports import IStartStreaming
from .ports.output_ports import (
    IEventPublisher,
    IOBSService,
    ISessionRepository,
)


class StartStreamingUseCase(IStartStreaming):
    """Use case for starting a streaming session."""

    def __init__(
        self,
        session_repository: ISessionRepository,
        obs_service: IOBSService | None,
        event_publisher: IEventPublisher,
        main_loop: "IRunMainLoop | None",
        sub_loop: "IRunSubLoop | None",
    ) -> None:
        self._session_repository = session_repository
        self._obs_service = obs_service
        self._event_publisher = event_publisher
        self._main_loop = main_loop
        self._sub_loop = sub_loop

    async def execute(self, request: StreamingRequestDTO) -> str:
        """Start a new streaming session."""
        # Check for existing session
        current = await self._session_repository.get_current()
        if current and current.is_live:
            raise RuntimeError("Session already in progress")

        # Create new session
        session = StreamSession()
        session.start()

        logger.info(f"Starting streaming session: {session.session_id}")

        # Start OBS stream
        if request.start_obs and self._obs_service:
            if request.initial_scene:
                await self._obs_service.switch_scene(request.initial_scene)
            await self._obs_service.start_stream()

        # Start loops
        if request.start_main_loop and self._main_loop:
            await self._main_loop.start()
            session.set_main_loop_status(LoopStatus.RUNNING)

        if request.start_sub_loop and self._sub_loop:
            await self._sub_loop.start()
            session.set_sub_loop_status(LoopStatus.RUNNING)

        # Go live
        session.go_live()

        # Persist session
        await self._session_repository.save(session)

        # Publish event
        await self._event_publisher.publish_session_started(session.session_id)

        logger.info(f"Session {session.session_id} is now live!")

        return session.session_id
```

#### 5.4.2 StopStreamingUseCase (stop_streaming.py)

```python
"""Stop streaming use case."""

from __future__ import annotations

from typing import Optional

from loguru import logger

from .ports.input_ports import IStopStreaming
from .ports.output_ports import (
    IEventPublisher,
    IOBSService,
    ISessionRepository,
    ITTSService,
)


class StopStreamingUseCase(IStopStreaming):
    """Use case for stopping a streaming session."""

    def __init__(
        self,
        session_repository: ISessionRepository,
        obs_service: IOBSService | None,
        tts_service: ITTSService | None,
        event_publisher: IEventPublisher,
        main_loop: "IRunMainLoop | None",
        sub_loop: "IRunSubLoop | None",
    ) -> None:
        self._session_repository = session_repository
        self._obs_service = obs_service
        self._tts_service = tts_service
        self._event_publisher = event_publisher
        self._main_loop = main_loop
        self._sub_loop = sub_loop

    async def execute(self, session_id: Optional[str] = None) -> None:
        """Stop streaming session."""
        # Get session
        if session_id:
            session = await self._session_repository.get_by_id(session_id)
        else:
            session = await self._session_repository.get_current()

        if not session:
            logger.warning("No active session to stop")
            return

        logger.info(f"Stopping session: {session.session_id}")

        session.begin_ending()

        # Stop loops
        if self._main_loop:
            await self._main_loop.stop()

        if self._sub_loop:
            await self._sub_loop.stop()

        # Clear TTS queue
        if self._tts_service:
            await self._tts_service.clear_queue()

        # Stop OBS stream
        if self._obs_service:
            await self._obs_service.stop_stream()

        # End session
        session.end()

        # Persist final state
        await self._session_repository.save(session)

        # Publish event
        await self._event_publisher.publish_session_ended(
            session.session_id,
            {
                "commentary_count": session.statistics.commentary_count,
                "response_count": session.statistics.response_count,
                "messages_received": session.statistics.messages_received,
                "duration_seconds": session.duration_seconds,
            },
        )

        logger.info(f"Session {session.session_id} ended")
```

#### 5.4.3 RunMainLoopUseCase (main_loop.py)

```python
"""Main commentary loop use case."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

from ..domain.entities import StreamSession
from ..domain.services import PriorityCalculationService
from ..domain.value_objects import LoopStatus, SpeechPriority, SpeechSource
from .dto import GameEventDTO, MainLoopConfigDTO
from .ports.input_ports import IRunMainLoop
from .ports.output_ports import (
    IEventPublisher,
    IGameService,
    ILLMService,
    ISessionRepository,
    ITTSService,
)


class GameStateAdapter:
    """Adapter to make game state dict work with PriorityCalculationService."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    @property
    def health(self) -> float:
        return self._state.get("health", 100.0)

    @property
    def max_health(self) -> float:
        return self._state.get("max_health", 100.0)

    @property
    def nearby_hostile_distance(self) -> Optional[float]:
        entities = self._state.get("nearby_entities", [])
        hostile_types = {"creeper", "zombie", "skeleton", "spider", "enderman"}
        for entity in entities:
            if entity.get("type") in hostile_types:
                return entity.get("distance")
        return None


class RunMainLoopUseCase(IRunMainLoop):
    """
    Use case for running the main game commentary loop.

    Responsibilities:
    - Poll game state
    - Generate commentary via LLM
    - Output speech via TTS
    - React to game events
    """

    def __init__(
        self,
        config: MainLoopConfigDTO,
        session_repository: ISessionRepository,
        game_service: IGameService,
        llm_service: ILLMService,
        tts_service: ITTSService,
        event_publisher: IEventPublisher,
        priority_service: Optional[PriorityCalculationService] = None,
    ) -> None:
        self._config = config
        self._session_repository = session_repository
        self._game_service = game_service
        self._llm_service = llm_service
        self._tts_service = tts_service
        self._event_publisher = event_publisher
        self._priority_service = priority_service or PriorityCalculationService()

        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the main loop."""
        if self._running:
            return

        self._running = True
        self._paused = False

        # Subscribe to game events
        await self._game_service.subscribe_events(self._on_game_event)

        # Start loop task
        self._task = asyncio.create_task(self._run())

        logger.info("Main loop started")

    async def stop(self) -> None:
        """Stop the main loop."""
        self._running = False

        # Unsubscribe from events
        await self._game_service.unsubscribe_events()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("Main loop stopped")

    def pause(self) -> None:
        """Pause commentary generation."""
        self._paused = True
        logger.info("Main loop paused")

    def resume(self) -> None:
        """Resume commentary generation."""
        self._paused = False
        logger.info("Main loop resumed")

    async def _run(self) -> None:
        """Main loop execution."""
        interval = self._config.interval_seconds

        while self._running:
            try:
                # Get current session
                session = await self._session_repository.get_current()
                if not session or not session.can_generate_commentary:
                    await asyncio.sleep(0.5)
                    continue

                if self._paused:
                    await asyncio.sleep(1)
                    continue

                # Get game state
                game_state = await self._game_service.get_state()

                # Generate commentary
                commentary = await self._llm_service.generate_commentary(
                    game_state=game_state,
                )

                if commentary:
                    # Calculate priority
                    state_adapter = GameStateAdapter(game_state)
                    priority = self._priority_service.calculate_for_game_state(state_adapter)

                    # Queue speech
                    await self._tts_service.speak(
                        text=commentary,
                        priority=priority,
                        source=SpeechSource.COMMENTARY,
                    )

                    # Update session
                    session.record_commentary()
                    await self._session_repository.save(session)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                session = await self._session_repository.get_current()
                if session:
                    session.set_main_loop_status(LoopStatus.ERROR)
                    await self._session_repository.save(session)
                await asyncio.sleep(5)

    async def _on_game_event(self, event_data: dict[str, Any]) -> None:
        """Handle game events."""
        event = GameEventDTO(
            event_type=event_data.get("type", "unknown"),
            description=event_data.get("description"),
            data=event_data,
        )

        logger.info(f"Game event: {event.event_type}")

        # Generate immediate reaction
        reaction = self._get_immediate_reaction(event)
        if reaction:
            await self._tts_service.speak(
                text=reaction,
                priority=SpeechPriority.HIGH,
                source=SpeechSource.GAME_EVENT,
            )

        # Trigger emotion change
        emotion_trigger = self._event_to_emotion_trigger(event)
        if emotion_trigger:
            await self._event_publisher.publish_emotion_trigger(emotion_trigger)

    def _get_immediate_reaction(self, event: GameEventDTO) -> Optional[str]:
        """Get immediate reaction for game event."""
        reactions = {
            "damage_taken": "いたっ！",
            "death": "うわあああ！死んじゃった...",
            "item_pickup": "お、何かゲット！",
            "entity_killed": "やった！倒した！",
        }
        return reactions.get(event.event_type)

    def _event_to_emotion_trigger(self, event: GameEventDTO) -> Optional[dict]:
        """Map game event to emotion trigger."""
        mapping = {
            "damage_taken": ("scared", 1.2),
            "death": ("sad", 1.5),
            "item_pickup": ("happy", 0.8),
            "entity_killed": ("excited", 1.0),
            "achievement": ("happy", 1.5),
        }

        if event.event_type in mapping:
            emotion, intensity = mapping[event.event_type]
            return {
                "source": "game",
                "event_type": event.event_type,
                "suggested_emotion": emotion,
                "intensity_modifier": intensity,
                "description": event.description,
            }

        return None
```

#### 5.4.4 RunSubLoopUseCase (sub_loop.py)

```python
"""Sub loop (chat response) use case."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Optional

from loguru import logger

from ..domain.services import InterruptionPolicyService
from ..domain.value_objects import LoopStatus, SpeechPriority, SpeechSource
from .dto import ChatMessageDTO, SubLoopConfigDTO
from .ports.input_ports import IRunSubLoop
from .ports.output_ports import (
    IEventPublisher,
    ILLMService,
    ISessionRepository,
    ITTSService,
    ITwitchService,
)


class MessageAdapter:
    """Adapter to make message dict work with InterruptionPolicyService."""

    def __init__(self, message: ChatMessageDTO) -> None:
        self._message = message

    @property
    def content(self) -> str:
        return self._message.content

    @property
    def is_broadcaster(self) -> bool:
        return self._message.is_broadcaster

    @property
    def is_moderator(self) -> bool:
        return self._message.is_moderator


class RunSubLoopUseCase(IRunSubLoop):
    """
    Use case for running the chat response sub loop.

    Responsibilities:
    - Receive chat messages from Twitch
    - Filter messages
    - Generate responses via LLM
    - Queue responses (with interrupt capability)
    """

    def __init__(
        self,
        config: SubLoopConfigDTO,
        session_repository: ISessionRepository,
        twitch_service: ITwitchService,
        llm_service: ILLMService,
        tts_service: ITTSService,
        event_publisher: IEventPublisher,
        interruption_service: Optional[InterruptionPolicyService] = None,
    ) -> None:
        self._config = config
        self._session_repository = session_repository
        self._twitch_service = twitch_service
        self._llm_service = llm_service
        self._tts_service = tts_service
        self._event_publisher = event_publisher
        self._interruption_service = interruption_service or InterruptionPolicyService()

        self._message_queue: deque[ChatMessageDTO] = deque(
            maxlen=config.max_queue_size
        )

        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the sub loop."""
        if self._running:
            return

        self._running = True

        # Subscribe to chat messages
        await self._twitch_service.subscribe_messages(self._on_message)

        # Start processing task
        self._task = asyncio.create_task(self._process_queue())

        logger.info("Sub loop started")

    async def stop(self) -> None:
        """Stop the sub loop."""
        self._running = False

        # Unsubscribe from messages
        await self._twitch_service.unsubscribe_messages()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Clear queue
        self._message_queue.clear()

        logger.info("Sub loop stopped")

    async def _on_message(self, message_data: dict[str, Any]) -> None:
        """Handle incoming chat message."""
        session = await self._session_repository.get_current()
        if not session:
            return

        # Record message
        session.record_message(filtered=False)

        # Check if should respond
        should_respond, score, reason = await self._twitch_service.should_respond(
            message_data
        )

        if should_respond:
            message = ChatMessageDTO(
                message_id=message_data.get("id", ""),
                user_name=message_data.get("user", {}).get("name", ""),
                content=message_data.get("content", ""),
                is_broadcaster=message_data.get("user", {}).get("is_broadcaster", False),
                is_moderator=message_data.get("user", {}).get("is_moderator", False),
                is_subscriber=message_data.get("user", {}).get("is_subscriber", False),
            )
            self._message_queue.append(message)
            session.record_message(filtered=True)
            logger.debug(f"Message queued: {message.content[:30]}...")

        await self._session_repository.save(session)

    async def _process_queue(self) -> None:
        """Process response queue."""
        while self._running:
            try:
                if not self._message_queue:
                    await asyncio.sleep(0.1)
                    continue

                # Get next message
                message = self._message_queue.popleft()

                # Generate response
                response_text = await self._llm_service.generate_response(
                    message_content=message.content,
                    user_name=message.user_name,
                )

                if not response_text:
                    continue

                # Determine priority
                priority = SpeechPriority.NORMAL
                if self._config.interrupt_enabled:
                    adapter = MessageAdapter(message)
                    if self._interruption_service.should_interrupt(adapter):
                        priority = SpeechPriority.INTERRUPT
                        logger.info("Interrupting for chat response")

                # Queue speech
                await self._tts_service.speak(
                    text=response_text,
                    priority=priority,
                    source=SpeechSource.RESPONSE,
                )

                # Update session
                session = await self._session_repository.get_current()
                if session:
                    session.record_response()
                    await self._session_repository.save(session)

                # Publish emotion trigger (positive interaction)
                await self._event_publisher.publish_emotion_trigger({
                    "source": "chat",
                    "event_type": "response",
                    "suggested_emotion": "happy",
                    "intensity_modifier": 0.5,
                })

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sub loop error: {e}")
                session = await self._session_repository.get_current()
                if session:
                    session.set_sub_loop_status(LoopStatus.ERROR)
                    await self._session_repository.save(session)
                await asyncio.sleep(1)
```

## 6. Infrastructure Layer

### 6.1 Service Adapters (adapters/)

```python
# tts_adapter.py
"""TTS service adapter."""

from __future__ import annotations

from typing import Optional

from ...application.ports.output_ports import ITTSService
from ...domain.value_objects import SpeechPriority, SpeechSource


class TTSServiceAdapter(ITTSService):
    """Adapter for TTSService from Phase 2."""

    def __init__(self, tts_service: "TTSService") -> None:
        self._tts_service = tts_service

    async def speak(
        self,
        text: str,
        priority: SpeechPriority,
        source: SpeechSource,
        emotion: Optional[str] = None,
    ) -> None:
        """Queue text for speech synthesis."""
        await self._tts_service.speak(
            text=text,
            priority=priority.value,
            source=source.value,
            emotion=emotion,
        )

    async def stop_current(self) -> None:
        """Stop current speech."""
        await self._tts_service.stop_current()

    async def clear_queue(self) -> None:
        """Clear speech queue."""
        await self._tts_service.clear_queue()
```

```python
# llm_adapter.py
"""LLM service adapter."""

from __future__ import annotations

from typing import Any, Optional

from ...application.ports.output_ports import ILLMService


class LLMServiceAdapter(ILLMService):
    """Adapter for LLMService from Phase 3."""

    def __init__(self, llm_service: "LLMService") -> None:
        self._llm_service = llm_service

    async def generate_commentary(
        self,
        game_state: dict[str, Any],
        emotion: Optional[str] = None,
    ) -> Optional[str]:
        """Generate game commentary."""
        return await self._llm_service.generate_commentary(
            game_state=game_state,
            emotion=emotion,
        )

    async def generate_response(
        self,
        message_content: str,
        user_name: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """Generate chat response."""
        return await self._llm_service.generate_response(
            message_content=message_content,
            user_name=user_name,
            context=context,
        )

    def update_emotion(self, emotion: str) -> None:
        """Update current emotion state."""
        self._llm_service.update_emotion(emotion)
```

```python
# twitch_adapter.py
"""Twitch service adapter."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ...application.ports.output_ports import ITwitchService


class TwitchServiceAdapter(ITwitchService):
    """Adapter for TwitchService from Phase 4."""

    def __init__(self, twitch_service: "TwitchService") -> None:
        self._twitch_service = twitch_service

    async def subscribe_messages(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Subscribe to chat messages."""
        await self._twitch_service.subscribe_messages(callback)

    async def unsubscribe_messages(self) -> None:
        """Unsubscribe from chat messages."""
        await self._twitch_service.unsubscribe_messages()

    async def should_respond(
        self,
        message: dict[str, Any],
    ) -> tuple[bool, float, str]:
        """Check if should respond to message."""
        return await self._twitch_service.should_respond(message)
```

```python
# game_adapter.py
"""Game service adapter."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ...application.ports.output_ports import IGameService


class GameServiceAdapter(IGameService):
    """Adapter for GameService from Phase 6."""

    def __init__(self, game_service: "GameService") -> None:
        self._game_service = game_service

    async def get_state(self) -> dict[str, Any]:
        """Get current game state."""
        return await self._game_service.get_state()

    async def subscribe_events(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Subscribe to game events."""
        await self._game_service.subscribe_events(callback)

    async def unsubscribe_events(self) -> None:
        """Unsubscribe from game events."""
        await self._game_service.unsubscribe_events()
```

```python
# obs_adapter.py
"""OBS service adapter."""

from __future__ import annotations

from ...application.ports.output_ports import IOBSService


class OBSServiceAdapter(IOBSService):
    """Adapter for OBSService from Phase 7."""

    def __init__(self, obs_service: "OBSService") -> None:
        self._obs_service = obs_service

    async def start_stream(self) -> None:
        """Start OBS stream."""
        await self._obs_service.start_stream()

    async def stop_stream(self) -> None:
        """Stop OBS stream."""
        await self._obs_service.stop_stream()

    async def switch_scene(self, scene_name: str) -> None:
        """Switch to specified scene."""
        await self._obs_service.switch_scene(scene_name)
```

### 6.2 Session Repository (session_repository.py)

```python
"""In-memory session repository."""

from __future__ import annotations

from typing import Optional

from ..application.ports.output_ports import ISessionRepository
from ..domain.entities import StreamSession


class InMemorySessionRepository(ISessionRepository):
    """In-memory implementation of session repository."""

    def __init__(self) -> None:
        self._sessions: dict[str, StreamSession] = {}
        self._current_session_id: Optional[str] = None

    async def save(self, session: StreamSession) -> None:
        """Save session state."""
        self._sessions[session.session_id] = session
        if session.is_live:
            self._current_session_id = session.session_id
        elif self._current_session_id == session.session_id:
            self._current_session_id = None

    async def get_current(self) -> Optional[StreamSession]:
        """Get current session."""
        if self._current_session_id:
            return self._sessions.get(self._current_session_id)
        return None

    async def get_by_id(self, session_id: str) -> Optional[StreamSession]:
        """Get session by ID."""
        return self._sessions.get(session_id)
```

### 6.3 Event Publisher (event_publisher.py)

```python
"""Event publisher adapter."""

from __future__ import annotations

from ..application.ports.output_ports import IEventPublisher
from ..core.events import Event, EventBus, EventType


class EventBusPublisher(IEventPublisher):
    """Event publisher using EventBus."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    async def publish_session_started(self, session_id: str) -> None:
        """Publish session started event."""
        await self._event_bus.publish(Event(
            type=EventType.SYSTEM_STARTED,
            data={"session_id": session_id, "status": "live"},
            source="orchestrator",
        ))

    async def publish_session_ended(self, session_id: str, statistics: dict) -> None:
        """Publish session ended event."""
        await self._event_bus.publish(Event(
            type=EventType.SYSTEM_STOPPED,
            data={"session_id": session_id, "statistics": statistics},
            source="orchestrator",
        ))

    async def publish_speech_started(self, source: str) -> None:
        """Publish speech started event."""
        await self._event_bus.publish(Event(
            type=EventType.TTS_SPEECH_STARTED,
            data={"source": source},
            source="orchestrator",
        ))

    async def publish_speech_ended(self) -> None:
        """Publish speech ended event."""
        await self._event_bus.publish(Event(
            type=EventType.TTS_SPEECH_COMPLETED,
            data={},
            source="orchestrator",
        ))

    async def publish_emotion_trigger(self, trigger: dict) -> None:
        """Publish emotion trigger event."""
        await self._event_bus.publish(Event(
            type=EventType.EMOTION_CHANGED,
            data=trigger,
            source="orchestrator",
        ))
```

## 7. Presentation Layer

### 7.1 OrchestratorService (orchestrator_service.py)

```python
"""Orchestrator presentation service."""

from __future__ import annotations

from typing import Optional

from loguru import logger

from ..application.dto import SessionStatusDTO, StreamingRequestDTO
from ..application.ports.input_ports import (
    IGetSessionStatus,
    IRunMainLoop,
    IRunSubLoop,
    IStartStreaming,
    IStopStreaming,
)
from ..application.ports.output_ports import ISessionRepository
from ..domain.value_objects import SpeechSource


class OrchestratorService:
    """
    Presentation layer service for orchestration.

    Coordinates use cases and provides high-level API
    for controlling the AI streamer.
    """

    def __init__(
        self,
        start_streaming: IStartStreaming,
        stop_streaming: IStopStreaming,
        main_loop: Optional[IRunMainLoop],
        sub_loop: Optional[IRunSubLoop],
        session_repository: ISessionRepository,
    ) -> None:
        self._start_streaming = start_streaming
        self._stop_streaming = stop_streaming
        self._main_loop = main_loop
        self._sub_loop = sub_loop
        self._session_repository = session_repository

        self._running = False

    async def start_session(
        self,
        start_obs: bool = True,
        start_main_loop: bool = True,
        start_sub_loop: bool = True,
        initial_scene: Optional[str] = None,
    ) -> str:
        """Start a new streaming session."""
        request = StreamingRequestDTO(
            start_obs=start_obs,
            start_main_loop=start_main_loop,
            start_sub_loop=start_sub_loop,
            initial_scene=initial_scene,
        )

        session_id = await self._start_streaming.execute(request)
        self._running = True

        return session_id

    async def stop_session(self, session_id: Optional[str] = None) -> None:
        """Stop the current streaming session."""
        await self._stop_streaming.execute(session_id)
        self._running = False

    async def get_status(self) -> SessionStatusDTO:
        """Get current session status."""
        session = await self._session_repository.get_current()

        if not session:
            return SessionStatusDTO(
                session_id=None,
                stream_status="offline",
                main_loop_status="idle",
                sub_loop_status="idle",
                is_speaking=False,
                current_speech_source=None,
                commentary_count=0,
                response_count=0,
                messages_received=0,
                messages_filtered=0,
                started_at=None,
                duration_seconds=None,
            )

        return SessionStatusDTO(
            session_id=session.session_id,
            stream_status=session.stream_status.value,
            main_loop_status=session.main_loop_status.value,
            sub_loop_status=session.sub_loop_status.value,
            is_speaking=session.is_speaking,
            current_speech_source=(
                session.current_speech_source.value
                if session.current_speech_source else None
            ),
            commentary_count=session.statistics.commentary_count,
            response_count=session.statistics.response_count,
            messages_received=session.statistics.messages_received,
            messages_filtered=session.statistics.messages_filtered,
            started_at=session.started_at,
            duration_seconds=session.duration_seconds,
        )

    def pause_main_loop(self) -> None:
        """Pause main loop commentary generation."""
        if self._main_loop:
            self._main_loop.pause()

    def resume_main_loop(self) -> None:
        """Resume main loop commentary generation."""
        if self._main_loop:
            self._main_loop.resume()

    @property
    def is_running(self) -> bool:
        """Check if orchestrator is running."""
        return self._running
```

## 8. Composition Root (main.py)

```python
"""Composition root for orchestrator - dependency injection setup."""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from .application.dto import MainLoopConfigDTO, SubLoopConfigDTO
from .application.use_cases.main_loop import RunMainLoopUseCase
from .application.use_cases.start_streaming import StartStreamingUseCase
from .application.use_cases.stop_streaming import StopStreamingUseCase
from .application.use_cases.sub_loop import RunSubLoopUseCase
from .core.config import Settings
from .core.events import EventBus, get_event_bus
from .infrastructure.adapters.game_adapter import GameServiceAdapter
from .infrastructure.adapters.llm_adapter import LLMServiceAdapter
from .infrastructure.adapters.obs_adapter import OBSServiceAdapter
from .infrastructure.adapters.tts_adapter import TTSServiceAdapter
from .infrastructure.adapters.twitch_adapter import TwitchServiceAdapter
from .infrastructure.event_publisher import EventBusPublisher
from .infrastructure.session_repository import InMemorySessionRepository
from .presentation.orchestrator_service import OrchestratorService

# Import services from other phases (would be actual imports in real code)
# from .tts.presentation import TTSService
# from .llm.presentation import LLMService
# from .twitch.presentation import TwitchService
# from .nitrogen.presentation import GameService
# from .obs.presentation import OBSService


@dataclass
class OrchestratorConfig:
    """Orchestrator configuration."""
    main_loop_interval_seconds: float = 5.0
    sub_loop_interrupt_enabled: bool = True
    sub_loop_max_queue_size: int = 10


def create_orchestrator_service(
    settings: Settings,
    event_bus: EventBus,
    tts_service: Optional["TTSService"] = None,
    llm_service: Optional["LLMService"] = None,
    twitch_service: Optional["TwitchService"] = None,
    game_service: Optional["GameService"] = None,
    obs_service: Optional["OBSService"] = None,
) -> OrchestratorService:
    """
    Create orchestrator service with all dependencies.

    This is the composition root where all dependencies are wired together.
    """
    config = OrchestratorConfig(
        main_loop_interval_seconds=settings.orchestrator.main_loop.interval_seconds,
        sub_loop_interrupt_enabled=settings.orchestrator.sub_loop.interrupt_enabled,
        sub_loop_max_queue_size=settings.orchestrator.sub_loop.max_queue_size,
    )

    # Create infrastructure components
    session_repository = InMemorySessionRepository()
    event_publisher = EventBusPublisher(event_bus)

    # Create adapters for external services
    tts_adapter = TTSServiceAdapter(tts_service) if tts_service else None
    llm_adapter = LLMServiceAdapter(llm_service) if llm_service else None
    twitch_adapter = TwitchServiceAdapter(twitch_service) if twitch_service else None
    game_adapter = GameServiceAdapter(game_service) if game_service else None
    obs_adapter = OBSServiceAdapter(obs_service) if obs_service else None

    # Create main loop use case
    main_loop: Optional[RunMainLoopUseCase] = None
    if game_adapter and llm_adapter and tts_adapter:
        main_loop = RunMainLoopUseCase(
            config=MainLoopConfigDTO(
                interval_seconds=config.main_loop_interval_seconds
            ),
            session_repository=session_repository,
            game_service=game_adapter,
            llm_service=llm_adapter,
            tts_service=tts_adapter,
            event_publisher=event_publisher,
        )

    # Create sub loop use case
    sub_loop: Optional[RunSubLoopUseCase] = None
    if twitch_adapter and llm_adapter and tts_adapter:
        sub_loop = RunSubLoopUseCase(
            config=SubLoopConfigDTO(
                interrupt_enabled=config.sub_loop_interrupt_enabled,
                max_queue_size=config.sub_loop_max_queue_size,
            ),
            session_repository=session_repository,
            twitch_service=twitch_adapter,
            llm_service=llm_adapter,
            tts_service=tts_adapter,
            event_publisher=event_publisher,
        )

    # Create start/stop streaming use cases
    start_streaming = StartStreamingUseCase(
        session_repository=session_repository,
        obs_service=obs_adapter,
        event_publisher=event_publisher,
        main_loop=main_loop,
        sub_loop=sub_loop,
    )

    stop_streaming = StopStreamingUseCase(
        session_repository=session_repository,
        obs_service=obs_adapter,
        tts_service=tts_adapter,
        event_publisher=event_publisher,
        main_loop=main_loop,
        sub_loop=sub_loop,
    )

    # Create orchestrator service
    return OrchestratorService(
        start_streaming=start_streaming,
        stop_streaming=stop_streaming,
        main_loop=main_loop,
        sub_loop=sub_loop,
        session_repository=session_repository,
    )


async def run_application(settings: Settings) -> None:
    """
    Run the complete AILoveShen application.

    This function initializes all services and starts the orchestrator.
    """
    logger.info(f"Starting {settings.app.name} v{settings.app.version}")

    # Create event bus
    event_bus = get_event_bus()
    await event_bus.start()

    # Initialize services from other phases
    # (In real implementation, these would be created using their factory functions)
    tts_service = None  # create_tts_service(settings.tts, event_bus)
    llm_service = None  # create_llm_service(settings.gemini, event_bus)
    twitch_service = None  # create_twitch_service(settings.twitch, event_bus)
    game_service = None  # create_game_service(settings.nitrogen, event_bus)
    obs_service = None  # create_obs_service(settings.obs, event_bus)

    # Create orchestrator
    orchestrator = create_orchestrator_service(
        settings=settings,
        event_bus=event_bus,
        tts_service=tts_service,
        llm_service=llm_service,
        twitch_service=twitch_service,
        game_service=game_service,
        obs_service=obs_service,
    )

    # Setup signal handlers
    shutdown_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # Start streaming session
        session_id = await orchestrator.start_session()
        logger.info(f"Session started: {session_id}")

        # Wait for shutdown signal
        await shutdown_event.wait()

    finally:
        # Cleanup
        logger.info("Shutting down...")
        await orchestrator.stop_session()
        await event_bus.stop()
        logger.info("Shutdown complete")


def main() -> int:
    """Main entry point."""
    from .core.config import get_config
    from .core.logging import setup_logging

    try:
        settings = get_config()
        setup_logging(settings.logging)

        asyncio.run(run_application(settings))
        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

## 9. 設定完全版

```yaml
# config/default.yaml
app:
  name: "AILoveShen"
  version: "0.1.0"
  debug: false

twitch:
  enabled: true
  channel: "${TWITCH_CHANNEL}"
  client_id: "${TWITCH_CLIENT_ID}"
  client_secret: "${TWITCH_CLIENT_SECRET}"

gemini:
  api_key: "${GEMINI_API_KEY}"
  main_model: "gemini-2.5-pro-preview-05-06"
  filter_model: "gemini-2.0-flash"

comment_filter:
  enabled: true
  base_threshold: 0.5
  min_threshold: 0.3
  max_threshold: 0.9
  window_seconds: 60
  low_volume_threshold: 5
  high_volume_threshold: 30

mcp:
  enabled: true
  host: "localhost"
  port: 3000
  memory:
    short_term_limit: 100
    long_term_db: "./data/memory.db"
  emotion:
    default_state: "neutral"
    decay_rate: 0.1

nitrogen:
  enabled: false
  host: "localhost"
  port: 8080
  polling_interval_ms: 100

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
    style: "Neutral"
  emotion_style_map:
    neutral: "Neutral"
    happy: "Happy"
    sad: "Sad"
    angry: "Angry"
    surprised: "Surprised"

obs:
  enabled: false
  host: "localhost"
  port: 4455
  password: "${OBS_PASSWORD}"
  scenes:
    main: "Main Scene"
    starting: "Starting Soon"
    ending: "Ending"

orchestrator:
  main_loop:
    enabled: true
    interval_seconds: 5
  sub_loop:
    enabled: true
    interrupt_enabled: true
    max_queue_size: 10

logging:
  level: "INFO"
  format: "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}"
  file:
    enabled: true
    path: "./data/logs/ailoveshen.log"
    rotation: "10 MB"
    retention: "7 days"
  console:
    enabled: true
    colorize: true
```

## 10. クリーンアーキテクチャの利点

1. **依存性の逆転**
   - 外部サービス（TTS, LLM, Twitch等）への依存はOutput Portインターフェースを通じて抽象化
   - ビジネスロジックは具体的な実装に依存しない

2. **テスト容易性**
   - 各Use CaseはOutput Portのモックを注入してテスト可能
   - Domain ServiceはProtocolを使用して依存を最小化

3. **拡張性**
   - 新しい外部サービスはAdapterを追加するだけで統合可能
   - ビジネスロジックの変更は影響範囲が限定される

4. **関心の分離**
   - Domain: ビジネスルール（優先度計算、割り込みポリシー）
   - Application: ユースケースオーケストレーション
   - Infrastructure: 外部サービスとの統合
   - Presentation: APIの提供

## 11. 次のステップ

全フェーズの設計書が完成しました。実装は以下の順序で進めます：

1. Phase 1: Core Infrastructure
2. Phase 2: TTS Pipeline
3. Phase 3: LLM Integration
4. Phase 4: Twitch Integration
5. Phase 5: MCP Server
6. Phase 6: NitroGen Integration
7. Phase 7: OBS Integration
8. Phase 8: Orchestration (統合)

各フェーズは独立してテスト可能な形で実装し、最終的にPhase 8で統合します。
