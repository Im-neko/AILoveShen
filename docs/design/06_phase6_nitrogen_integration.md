# Phase 6: NitroGen Integration 詳細設計書 (Issue #4)

## 1. 概要

NitroGenとの統合を行い、LLMがMinecraftを操作できる双方向連携を実装します。

### 要件（Issue #4より）
- Game State Manager（ゲーム状態の取得・管理）
- Action Executor（LLMの意図をゲーム操作に変換）
- LLM ↔ NitroGen 双方向連携

## 2. クリーンアーキテクチャに基づくコンポーネント構成

```
src/ailoveshen/
├── domain/
│   ├── entities/
│   │   └── game_state.py           # GameState aggregate (Phase 1から)
│   ├── value_objects/
│   │   ├── game_action.py          # GameAction
│   │   ├── game_event.py           # GameEvent
│   │   └── position.py             # Position, Rotation
│   └── services/
│       └── event_detection_service.py # EventDetectionService
│
├── application/
│   ├── ports/
│   │   ├── input/
│   │   │   ├── get_game_state.py       # IGetGameState
│   │   │   └── execute_action.py       # IExecuteAction
│   │   └── output/
│   │       └── game_environment.py     # IGameEnvironment
│   ├── use_cases/
│   │   ├── get_game_state.py       # GetGameStateUseCase
│   │   └── execute_action.py       # ExecuteActionUseCase
│   └── dto/
│       └── game_dto.py             # Request/Response DTOs
│
├── infrastructure/
│   └── adapters/
│       └── nitrogen/
│           └── nitrogen_adapter.py # NitroGenAdapter
│
└── presentation/
    └── services/
        └── game_service.py         # GameService (coordinates)
```

## 3. Domain Layer

### 3.1 Value Objects

#### GameAction (domain/value_objects/game_action.py)

```python
"""Game action value object."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ActionType(str, Enum):
    """Types of game actions."""
    MOVE = "move"
    ATTACK = "attack"
    MINE = "mine"
    PLACE = "place"
    USE_ITEM = "use_item"
    CRAFT = "craft"
    INTERACT = "interact"


@dataclass(frozen=True)
class GameAction:
    """
    Immutable value object representing a game action.
    """
    action_type: ActionType
    parameters: dict = field(default_factory=dict)
    description: str = ""

    def is_movement(self) -> bool:
        """Check if this is a movement action."""
        return self.action_type == ActionType.MOVE

    def is_combat(self) -> bool:
        """Check if this is a combat action."""
        return self.action_type == ActionType.ATTACK

    @classmethod
    def move_to(cls, direction: str) -> GameAction:
        """Create a movement action."""
        return cls(
            action_type=ActionType.MOVE,
            parameters={"direction": direction},
            description=f"Move {direction}",
        )

    @classmethod
    def attack(cls, target: str) -> GameAction:
        """Create an attack action."""
        return cls(
            action_type=ActionType.ATTACK,
            parameters={"target": target},
            description=f"Attack {target}",
        )
```

#### GameEvent (domain/value_objects/game_event.py)

```python
"""Game event value object."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class GameEventType(str, Enum):
    """Types of game events."""
    DAMAGE_TAKEN = "damage_taken"
    DAMAGE_DEALT = "damage_dealt"
    DEATH = "death"
    ITEM_OBTAINED = "item_obtained"
    LOCATION_CHANGED = "location_changed"
    MOB_NEARBY = "mob_nearby"
    ACHIEVEMENT = "achievement"


@dataclass(frozen=True)
class GameEvent:
    """
    Immutable value object representing a game event.
    """
    event_type: GameEventType
    description: str
    data: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def is_danger(self) -> bool:
        """Check if this is a dangerous event."""
        return self.event_type in (
            GameEventType.DAMAGE_TAKEN,
            GameEventType.MOB_NEARBY,
            GameEventType.DEATH,
        )

    def is_positive(self) -> bool:
        """Check if this is a positive event."""
        return self.event_type in (
            GameEventType.ITEM_OBTAINED,
            GameEventType.ACHIEVEMENT,
        )
```

### 3.2 Domain Services

#### EventDetectionService (domain/services/event_detection_service.py)

```python
"""Event detection domain service."""

from __future__ import annotations

from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.value_objects.game_event import GameEvent, GameEventType


class EventDetectionService:
    """
    Domain service for detecting game events from state changes.

    Encapsulates business logic for what constitutes an "event".
    """

    def __init__(
        self,
        health_change_threshold: float = 5.0,
        danger_distance: float = 10.0,
    ) -> None:
        self._health_threshold = health_change_threshold
        self._danger_distance = danger_distance

    def detect_events(
        self,
        old_state: GameState,
        new_state: GameState,
    ) -> list[GameEvent]:
        """Detect events from state transition."""
        events = []

        # Health change detection
        events.extend(self._detect_health_events(old_state, new_state))

        # Death detection
        if new_state.health <= 0 and old_state.health > 0:
            events.append(GameEvent(
                event_type=GameEventType.DEATH,
                description="死んでしまった...",
            ))

        # Mob detection
        events.extend(self._detect_mob_events(old_state, new_state))

        return events

    def _detect_health_events(
        self,
        old_state: GameState,
        new_state: GameState,
    ) -> list[GameEvent]:
        """Detect health-related events."""
        events = []
        health_diff = new_state.health - old_state.health

        if abs(health_diff) >= self._health_threshold:
            if health_diff < 0:
                events.append(GameEvent(
                    event_type=GameEventType.DAMAGE_TAKEN,
                    description=f"{abs(health_diff):.1f}ダメージを受けた！",
                    data={"damage": abs(health_diff), "new_health": new_state.health},
                ))
            else:
                events.append(GameEvent(
                    event_type=GameEventType.DAMAGE_DEALT,
                    description=f"体力が{health_diff:.1f}回復した",
                    data={"healed": health_diff},
                ))

        return events

    def _detect_mob_events(
        self,
        old_state: GameState,
        new_state: GameState,
    ) -> list[GameEvent]:
        """Detect mob-related events."""
        events = []
        hostile_mobs = {"zombie", "skeleton", "creeper", "spider"}

        old_entity_ids = {e.id for e in old_state.nearby_entities}
        for entity in new_state.nearby_entities:
            if entity.id not in old_entity_ids:
                if entity.type in hostile_mobs:
                    events.append(GameEvent(
                        event_type=GameEventType.MOB_NEARBY,
                        description=f"{entity.type}が近くにいる！",
                        data={"entity": entity.type, "distance": entity.distance},
                    ))

        return events
```

## 4. Application Layer

### 4.1 Output Ports

#### IGameEnvironment (application/ports/output/game_environment.py)

```python
"""Game environment output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.value_objects.game_action import GameAction


class IGameEnvironment(ABC):
    """
    Output port for game environment.

    Abstracts NitroGen or other Minecraft control.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Connect to game environment."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from game environment."""
        ...

    @abstractmethod
    async def get_observation(self) -> dict[str, Any]:
        """Get current game observation."""
        ...

    @abstractmethod
    async def execute_action(self, action: GameAction) -> bool:
        """Execute action in game."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected."""
        ...
```

### 4.2 Input Ports

#### IGetGameState (application/ports/input/get_game_state.py)

```python
"""Get game state input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.game_dto import (
    GetGameStateRequest,
    GetGameStateResponse,
)


class IGetGameState(ABC):
    """Input port for getting game state."""

    @abstractmethod
    async def execute(
        self,
        request: GetGameStateRequest,
    ) -> GetGameStateResponse:
        """Get current game state."""
        ...
```

#### IExecuteAction (application/ports/input/execute_action.py)

```python
"""Execute action input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.game_dto import (
    ExecuteActionRequest,
    ExecuteActionResponse,
)


class IExecuteAction(ABC):
    """Input port for executing game actions."""

    @abstractmethod
    async def execute(
        self,
        request: ExecuteActionRequest,
    ) -> ExecuteActionResponse:
        """Execute game action."""
        ...
```

### 4.3 Use Cases

#### GetGameStateUseCase (application/use_cases/get_game_state.py)

```python
"""Get game state use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.game_dto import GetGameStateRequest, GetGameStateResponse
from ailoveshen.application.ports.input.get_game_state import IGetGameState
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_environment import IGameEnvironment
from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.events import GameStateUpdatedEvent
from ailoveshen.domain.services.event_detection_service import EventDetectionService


class GetGameStateUseCase(IGetGameState):
    """Use case for getting game state."""

    def __init__(
        self,
        game_environment: IGameEnvironment,
        event_detection: EventDetectionService,
        event_publisher: IEventPublisher,
    ) -> None:
        self._environment = game_environment
        self._event_detection = event_detection
        self._event_publisher = event_publisher
        self._previous_state: GameState | None = None

    async def execute(
        self,
        request: GetGameStateRequest,
    ) -> GetGameStateResponse:
        """Get current game state and detect events."""
        try:
            observation = await self._environment.get_observation()
            state = self._parse_observation(observation)

            # Detect events
            events = []
            if self._previous_state:
                events = self._event_detection.detect_events(
                    self._previous_state,
                    state,
                )
                for event in events:
                    await self._event_publisher.publish(
                        GameEventOccurredEvent(
                            event_type=event.event_type.value,
                            description=event.description,
                        )
                    )

            self._previous_state = state

            await self._event_publisher.publish(
                GameStateUpdatedEvent(state=state)
            )

            return GetGameStateResponse(
                success=True,
                state=state,
                events=events,
            )

        except Exception as e:
            logger.error(f"Failed to get game state: {e}")
            return GetGameStateResponse(
                success=False,
                state=None,
                events=[],
                error=str(e),
            )

    def _parse_observation(self, obs: dict) -> GameState:
        """Parse observation dict to GameState entity."""
        # Implementation depends on NitroGen observation format
        return GameState(
            health=obs.get("health", 20),
            max_health=20,
            hunger=obs.get("hunger", 20),
            position=Position(
                x=obs.get("x", 0),
                y=obs.get("y", 64),
                z=obs.get("z", 0),
            ),
        )
```

#### ExecuteActionUseCase (application/use_cases/execute_action.py)

```python
"""Execute action use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.game_dto import ExecuteActionRequest, ExecuteActionResponse
from ailoveshen.application.ports.input.execute_action import IExecuteAction
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_environment import IGameEnvironment
from ailoveshen.domain.events import GameActionExecutedEvent
from ailoveshen.domain.value_objects.game_action import ActionType, GameAction


class ExecuteActionUseCase(IExecuteAction):
    """Use case for executing game actions."""

    # Natural language to action mapping
    ACTION_KEYWORDS = {
        "move": ActionType.MOVE,
        "walk": ActionType.MOVE,
        "go": ActionType.MOVE,
        "attack": ActionType.ATTACK,
        "hit": ActionType.ATTACK,
        "mine": ActionType.MINE,
        "dig": ActionType.MINE,
        "place": ActionType.PLACE,
        "build": ActionType.PLACE,
        "use": ActionType.USE_ITEM,
        "eat": ActionType.USE_ITEM,
    }

    def __init__(
        self,
        game_environment: IGameEnvironment,
        event_publisher: IEventPublisher,
    ) -> None:
        self._environment = game_environment
        self._event_publisher = event_publisher

    async def execute(
        self,
        request: ExecuteActionRequest,
    ) -> ExecuteActionResponse:
        """Execute game action from intention."""
        try:
            # Parse intention to action
            action = self._parse_intention(request.intention)

            logger.info(f"Executing: {action.description}")

            # Execute
            success = await self._environment.execute_action(action)

            # Publish event
            await self._event_publisher.publish(
                GameActionExecutedEvent(
                    action_type=action.action_type.value,
                    description=action.description,
                    success=success,
                )
            )

            return ExecuteActionResponse(
                success=success,
                action=action,
                message=f"Executed: {action.description}",
            )

        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return ExecuteActionResponse(
                success=False,
                action=None,
                message=f"Error: {e}",
            )

    def _parse_intention(self, intention: str) -> GameAction:
        """Parse natural language intention to GameAction."""
        intention_lower = intention.lower()

        action_type = ActionType.MOVE  # Default
        for keyword, atype in self.ACTION_KEYWORDS.items():
            if keyword in intention_lower:
                action_type = atype
                break

        params = self._extract_parameters(intention_lower)

        return GameAction(
            action_type=action_type,
            parameters=params,
            description=intention,
        )

    def _extract_parameters(self, intention: str) -> dict:
        """Extract action parameters."""
        params = {}

        # Direction
        for direction in ["forward", "back", "left", "right"]:
            if direction in intention:
                params["direction"] = direction
                break

        # Targets
        for target in ["diamond", "iron", "coal", "wood", "zombie", "creeper"]:
            if target in intention:
                params["target"] = target
                break

        return params
```

## 5. Infrastructure Layer

### 5.1 NitroGen Adapter

#### NitroGenAdapter (infrastructure/adapters/nitrogen/nitrogen_adapter.py)

```python
"""NitroGen environment adapter."""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.game_environment import IGameEnvironment
from ailoveshen.core.exceptions import GameConnectionError
from ailoveshen.domain.value_objects.game_action import GameAction


class NitroGenAdapter(IGameEnvironment):
    """
    Infrastructure adapter for NitroGen/MineDojo.

    Implements IGameEnvironment output port.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8080,
    ) -> None:
        self._host = host
        self._port = port
        self._env = None  # NitroGen environment
        self._connected = False

    async def connect(self) -> None:
        """Connect to NitroGen environment."""
        try:
            # TODO: Actual NitroGen connection
            # from nitrogen import NitroGenEnv
            # self._env = NitroGenEnv(host=self._host, port=self._port)
            # await self._env.connect()

            self._connected = True
            logger.info(f"Connected to NitroGen at {self._host}:{self._port}")

        except Exception as e:
            raise GameConnectionError(f"Failed to connect: {e}")

    async def disconnect(self) -> None:
        """Disconnect from NitroGen."""
        if self._env:
            # await self._env.close()
            pass
        self._connected = False
        logger.info("Disconnected from NitroGen")

    async def get_observation(self) -> dict[str, Any]:
        """Get current observation."""
        if not self._connected:
            raise GameConnectionError("Not connected")

        # TODO: Get actual observation from NitroGen
        # return await self._env.observe()

        # Placeholder
        return {
            "health": 20,
            "hunger": 20,
            "x": 0,
            "y": 64,
            "z": 0,
        }

    async def execute_action(self, action: GameAction) -> bool:
        """Execute action in NitroGen."""
        if not self._connected:
            raise GameConnectionError("Not connected")

        try:
            # TODO: Execute actual action
            # await self._env.step(action.to_nitrogen_format())

            logger.debug(f"Executed: {action.action_type} with {action.parameters}")
            return True

        except Exception as e:
            logger.error(f"Action failed: {e}")
            return False

    def is_connected(self) -> bool:
        return self._connected
```

## 6. Presentation Layer

### 6.1 Game Service

#### GameService (presentation/services/game_service.py)

```python
"""Game service for presentation layer."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from loguru import logger

from ailoveshen.application.dto.game_dto import ExecuteActionRequest, GetGameStateRequest
from ailoveshen.application.ports.input.execute_action import IExecuteAction
from ailoveshen.application.ports.input.get_game_state import IGetGameState
from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.value_objects.game_event import GameEvent


class GameService:
    """
    Presentation layer service for game integration.

    Coordinates state polling and action execution.
    """

    def __init__(
        self,
        get_game_state: IGetGameState,
        execute_action: IExecuteAction,
        polling_interval_ms: int = 100,
    ) -> None:
        self._get_state = get_game_state
        self._execute = execute_action
        self._polling_interval = polling_interval_ms / 1000
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._event_handlers: list[Callable[[GameEvent], None]] = []

    async def start(self) -> None:
        """Start game service with polling."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Game service started")

    async def stop(self) -> None:
        """Stop game service."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Game service stopped")

    async def get_state(self) -> GameState | None:
        """Get current game state."""
        response = await self._get_state.execute(GetGameStateRequest())
        return response.state if response.success else None

    async def execute_intention(self, intention: str) -> bool:
        """Execute action from natural language."""
        response = await self._execute.execute(
            ExecuteActionRequest(intention=intention)
        )
        return response.success

    def on_game_event(self, handler: Callable[[GameEvent], None]) -> None:
        """Register game event handler."""
        self._event_handlers.append(handler)

    async def _poll_loop(self) -> None:
        """State polling loop."""
        while self._running:
            try:
                response = await self._get_state.execute(GetGameStateRequest())

                # Notify event handlers
                for event in response.events:
                    for handler in self._event_handlers:
                        try:
                            await handler(event)
                        except Exception as e:
                            logger.error(f"Event handler error: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling error: {e}")

            await asyncio.sleep(self._polling_interval)
```

## 7. Composition Root (NitroGen部分)

```python
# src/ailoveshen/main.py (NitroGen部分の抜粋)

from ailoveshen.application.use_cases.execute_action import ExecuteActionUseCase
from ailoveshen.application.use_cases.get_game_state import GetGameStateUseCase
from ailoveshen.domain.services.event_detection_service import EventDetectionService
from ailoveshen.infrastructure.adapters.nitrogen.nitrogen_adapter import NitroGenAdapter
from ailoveshen.presentation.services.game_service import GameService


def create_game_service(
    config: NitroGenConfig,
    event_publisher: IEventPublisher,
) -> GameService:
    """Create game service with all dependencies."""

    # Infrastructure
    nitrogen = NitroGenAdapter(
        host=config.host,
        port=config.port,
    )

    # Domain service
    event_detection = EventDetectionService(
        health_change_threshold=config.events.health_change_threshold,
    )

    # Use cases
    get_game_state = GetGameStateUseCase(
        game_environment=nitrogen,
        event_detection=event_detection,
        event_publisher=event_publisher,
    )

    execute_action = ExecuteActionUseCase(
        game_environment=nitrogen,
        event_publisher=event_publisher,
    )

    return GameService(
        get_game_state=get_game_state,
        execute_action=execute_action,
        polling_interval_ms=config.polling_interval_ms,
    )
```

## 8. 設定

```yaml
nitrogen:
  enabled: false
  host: "localhost"
  port: 8080
  polling_interval_ms: 100

  events:
    health_change_threshold: 5
```
