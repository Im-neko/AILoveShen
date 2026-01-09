# Phase 7: OBS Integration 詳細設計書 (Issue #6)

## 1. 概要

OBS Studioとの連携を実装し、配信の制御や画面切り替えなどを自動化します。

### 要件（Issue #6より）
- OBS WebSocket接続
- シーン・ソース制御
- 配信制御

## 2. クリーンアーキテクチャに基づくコンポーネント構成

```
src/ailoveshen/
├── domain/
│   └── value_objects/
│       ├── scene.py                # Scene, Source
│       └── subtitle.py             # SubtitleText
│
├── application/
│   ├── ports/
│   │   ├── input/
│   │   │   ├── control_scene.py        # IControlScene
│   │   │   ├── control_stream.py       # IControlStream
│   │   │   └── display_subtitle.py     # IDisplaySubtitle
│   │   └── output/
│   │       └── obs_connection.py       # IOBSConnection
│   ├── use_cases/
│   │   ├── control_scene.py        # ControlSceneUseCase
│   │   ├── control_stream.py       # ControlStreamUseCase
│   │   └── display_subtitle.py     # DisplaySubtitleUseCase
│   └── dto/
│       └── obs_dto.py              # Request/Response DTOs
│
├── infrastructure/
│   └── adapters/
│       └── obs/
│           └── obs_websocket_adapter.py # OBSWebSocketAdapter
│
└── presentation/
    └── services/
        └── obs_service.py          # OBSService (coordinates)
```

## 3. Domain Layer

### 3.1 Value Objects

#### Scene (domain/value_objects/scene.py)

```python
"""Scene-related value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SceneType(str, Enum):
    """Standard scene types."""
    MAIN = "main"
    STARTING = "starting"
    ENDING = "ending"
    BRB = "brb"  # Be Right Back


@dataclass(frozen=True)
class Scene:
    """
    Immutable value object representing an OBS scene.
    """
    name: str
    scene_type: SceneType | None = None

    def is_main(self) -> bool:
        """Check if this is the main scene."""
        return self.scene_type == SceneType.MAIN


@dataclass(frozen=True)
class Source:
    """
    Immutable value object representing an OBS source.
    """
    name: str
    scene_name: str
    visible: bool = True
```

#### SubtitleText (domain/value_objects/subtitle.py)

```python
"""Subtitle-related value objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SubtitleText:
    """
    Immutable value object for subtitle display.
    """
    text: str
    duration_seconds: Optional[float] = None

    def is_auto_hide(self) -> bool:
        """Check if subtitle should auto-hide."""
        return self.duration_seconds is not None

    def is_empty(self) -> bool:
        """Check if subtitle is empty (for hiding)."""
        return not self.text.strip()
```

## 4. Application Layer

### 4.1 Output Ports

#### IOBSConnection (application/ports/output/obs_connection.py)

```python
"""OBS connection output port."""

from __future__ import annotations

from abc import ABC, abstractmethod


class IOBSConnection(ABC):
    """
    Output port for OBS WebSocket connection.

    Abstracts OBS Studio control.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Connect to OBS."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from OBS."""
        ...

    @abstractmethod
    async def set_scene(self, scene_name: str) -> None:
        """Switch to a scene."""
        ...

    @abstractmethod
    async def get_current_scene(self) -> str:
        """Get current scene name."""
        ...

    @abstractmethod
    async def set_source_visibility(
        self,
        scene_name: str,
        source_name: str,
        visible: bool,
    ) -> None:
        """Set source visibility."""
        ...

    @abstractmethod
    async def set_text_source(
        self,
        source_name: str,
        text: str,
    ) -> None:
        """Update text source content."""
        ...

    @abstractmethod
    async def start_streaming(self) -> None:
        """Start streaming."""
        ...

    @abstractmethod
    async def stop_streaming(self) -> None:
        """Stop streaming."""
        ...

    @abstractmethod
    async def is_streaming(self) -> bool:
        """Check if currently streaming."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected."""
        ...
```

### 4.2 Input Ports & Use Cases

#### ControlSceneUseCase (application/use_cases/control_scene.py)

```python
"""Control scene use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.obs_dto import (
    SwitchSceneRequest,
    SwitchSceneResponse,
)
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.obs_connection import IOBSConnection
from ailoveshen.domain.events import SceneChangedEvent
from ailoveshen.domain.value_objects.scene import SceneType


class ControlSceneUseCase:
    """Use case for scene control."""

    def __init__(
        self,
        obs: IOBSConnection,
        event_publisher: IEventPublisher,
        scene_names: dict[SceneType, str],
    ) -> None:
        self._obs = obs
        self._event_publisher = event_publisher
        self._scenes = scene_names

    async def switch_scene(
        self,
        request: SwitchSceneRequest,
    ) -> SwitchSceneResponse:
        """Switch to a scene."""
        try:
            scene_name = request.scene_name
            if request.scene_type:
                scene_name = self._scenes.get(request.scene_type, scene_name)

            await self._obs.set_scene(scene_name)

            await self._event_publisher.publish(
                SceneChangedEvent(scene_name=scene_name)
            )

            logger.info(f"Switched to scene: {scene_name}")

            return SwitchSceneResponse(
                success=True,
                scene_name=scene_name,
            )

        except Exception as e:
            logger.error(f"Scene switch failed: {e}")
            return SwitchSceneResponse(
                success=False,
                scene_name="",
                error=str(e),
            )

    async def go_to_main(self) -> SwitchSceneResponse:
        """Quick switch to main scene."""
        return await self.switch_scene(
            SwitchSceneRequest(scene_type=SceneType.MAIN)
        )

    async def go_to_starting(self) -> SwitchSceneResponse:
        """Quick switch to starting scene."""
        return await self.switch_scene(
            SwitchSceneRequest(scene_type=SceneType.STARTING)
        )

    async def go_to_ending(self) -> SwitchSceneResponse:
        """Quick switch to ending scene."""
        return await self.switch_scene(
            SwitchSceneRequest(scene_type=SceneType.ENDING)
        )
```

#### DisplaySubtitleUseCase (application/use_cases/display_subtitle.py)

```python
"""Display subtitle use case."""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from ailoveshen.application.dto.obs_dto import (
    DisplaySubtitleRequest,
    DisplaySubtitleResponse,
)
from ailoveshen.application.ports.output.obs_connection import IOBSConnection
from ailoveshen.domain.value_objects.subtitle import SubtitleText


class DisplaySubtitleUseCase:
    """Use case for subtitle display."""

    def __init__(
        self,
        obs: IOBSConnection,
        default_source_name: str = "Subtitles",
    ) -> None:
        self._obs = obs
        self._source_name = default_source_name
        self._hide_task: Optional[asyncio.Task] = None

    async def display(
        self,
        request: DisplaySubtitleRequest,
    ) -> DisplaySubtitleResponse:
        """Display subtitle text."""
        try:
            # Cancel pending hide
            if self._hide_task and not self._hide_task.done():
                self._hide_task.cancel()

            # Create subtitle value object
            subtitle = SubtitleText(
                text=request.text,
                duration_seconds=request.duration_seconds,
            )

            # Update text
            await self._obs.set_text_source(
                self._source_name,
                subtitle.text,
            )

            logger.debug(f"Subtitle: {request.text[:50]}...")

            # Schedule auto-hide
            if subtitle.is_auto_hide():
                self._hide_task = asyncio.create_task(
                    self._auto_hide(subtitle.duration_seconds)
                )

            return DisplaySubtitleResponse(success=True)

        except Exception as e:
            logger.error(f"Subtitle display failed: {e}")
            return DisplaySubtitleResponse(
                success=False,
                error=str(e),
            )

    async def hide(self) -> DisplaySubtitleResponse:
        """Hide subtitle."""
        return await self.display(
            DisplaySubtitleRequest(text="")
        )

    async def typewriter(
        self,
        text: str,
        char_delay: float = 0.05,
    ) -> DisplaySubtitleResponse:
        """Display with typewriter effect."""
        try:
            current = ""
            for char in text:
                current += char
                await self._obs.set_text_source(self._source_name, current)
                await asyncio.sleep(char_delay)

            return DisplaySubtitleResponse(success=True)

        except Exception as e:
            return DisplaySubtitleResponse(success=False, error=str(e))

    async def _auto_hide(self, duration: float) -> None:
        """Auto-hide after duration."""
        try:
            await asyncio.sleep(duration)
            await self.hide()
        except asyncio.CancelledError:
            pass
```

#### ControlStreamUseCase (application/use_cases/control_stream.py)

```python
"""Control stream use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.obs_dto import (
    StreamControlRequest,
    StreamControlResponse,
)
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.obs_connection import IOBSConnection
from ailoveshen.domain.events import StreamStartedEvent, StreamStoppedEvent


class ControlStreamUseCase:
    """Use case for stream control."""

    def __init__(
        self,
        obs: IOBSConnection,
        event_publisher: IEventPublisher,
    ) -> None:
        self._obs = obs
        self._event_publisher = event_publisher

    async def start(
        self,
        request: StreamControlRequest,
    ) -> StreamControlResponse:
        """Start streaming."""
        try:
            await self._obs.start_streaming()

            await self._event_publisher.publish(
                StreamStartedEvent()
            )

            logger.info("Streaming started")
            return StreamControlResponse(success=True, is_streaming=True)

        except Exception as e:
            logger.error(f"Failed to start stream: {e}")
            return StreamControlResponse(
                success=False,
                is_streaming=False,
                error=str(e),
            )

    async def stop(
        self,
        request: StreamControlRequest,
    ) -> StreamControlResponse:
        """Stop streaming."""
        try:
            await self._obs.stop_streaming()

            await self._event_publisher.publish(
                StreamStoppedEvent()
            )

            logger.info("Streaming stopped")
            return StreamControlResponse(success=True, is_streaming=False)

        except Exception as e:
            logger.error(f"Failed to stop stream: {e}")
            return StreamControlResponse(
                success=False,
                is_streaming=True,
                error=str(e),
            )
```

## 5. Infrastructure Layer

### 5.1 OBS WebSocket Adapter

#### OBSWebSocketAdapter (infrastructure/adapters/obs/obs_websocket_adapter.py)

```python
"""OBS WebSocket adapter."""

from __future__ import annotations

import asyncio
from typing import Optional

import obsws_python as obs
from loguru import logger

from ailoveshen.application.ports.output.obs_connection import IOBSConnection
from ailoveshen.core.exceptions import OBSConnectionError


class OBSWebSocketAdapter(IOBSConnection):
    """
    Infrastructure adapter for OBS WebSocket.

    Implements IOBSConnection output port using obs-websocket-py.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4455,
        password: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._client: Optional[obs.ReqClient] = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to OBS WebSocket."""
        try:
            loop = asyncio.get_event_loop()
            self._client = await loop.run_in_executor(
                None,
                lambda: obs.ReqClient(
                    host=self._host,
                    port=self._port,
                    password=self._password,
                ),
            )
            self._connected = True
            logger.info(f"Connected to OBS at {self._host}:{self._port}")

        except Exception as e:
            raise OBSConnectionError(f"Failed to connect: {e}")

    async def disconnect(self) -> None:
        """Disconnect from OBS."""
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False
        logger.info("Disconnected from OBS")

    async def set_scene(self, scene_name: str) -> None:
        """Switch to a scene."""
        await self._call("SetCurrentProgramScene", scene_name=scene_name)

    async def get_current_scene(self) -> str:
        """Get current scene name."""
        response = await self._call("GetCurrentProgramScene")
        return response.scene_name

    async def set_source_visibility(
        self,
        scene_name: str,
        source_name: str,
        visible: bool,
    ) -> None:
        """Set source visibility."""
        # Get scene item ID
        response = await self._call(
            "GetSceneItemId",
            scene_name=scene_name,
            source_name=source_name,
        )
        item_id = response.scene_item_id

        # Set visibility
        await self._call(
            "SetSceneItemEnabled",
            scene_name=scene_name,
            scene_item_id=item_id,
            scene_item_enabled=visible,
        )

    async def set_text_source(self, source_name: str, text: str) -> None:
        """Update text source content."""
        await self._call(
            "SetInputSettings",
            input_name=source_name,
            input_settings={"text": text},
        )

    async def start_streaming(self) -> None:
        """Start streaming."""
        await self._call("StartStream")

    async def stop_streaming(self) -> None:
        """Stop streaming."""
        await self._call("StopStream")

    async def is_streaming(self) -> bool:
        """Check if currently streaming."""
        response = await self._call("GetStreamStatus")
        return response.output_active

    def is_connected(self) -> bool:
        return self._connected

    async def _call(self, method: str, **kwargs):
        """Call OBS WebSocket method."""
        if not self._client:
            raise OBSConnectionError("Not connected")

        loop = asyncio.get_event_loop()
        # Convert method name format
        func_name = method.lower().replace("get", "get_").replace("set", "set_")
        func = getattr(self._client, func_name)
        return await loop.run_in_executor(None, lambda: func(**kwargs))
```

## 6. Presentation Layer

### 6.1 OBS Service

#### OBSService (presentation/services/obs_service.py)

```python
"""OBS service for presentation layer."""

from __future__ import annotations

from typing import Optional

from loguru import logger

from ailoveshen.application.dto.obs_dto import (
    DisplaySubtitleRequest,
    StreamControlRequest,
    SwitchSceneRequest,
)
from ailoveshen.application.use_cases.control_scene import ControlSceneUseCase
from ailoveshen.application.use_cases.control_stream import ControlStreamUseCase
from ailoveshen.application.use_cases.display_subtitle import DisplaySubtitleUseCase
from ailoveshen.domain.value_objects.scene import SceneType


class OBSService:
    """
    Presentation layer service for OBS control.

    Provides unified interface for OBS operations.
    """

    def __init__(
        self,
        scene_control: ControlSceneUseCase,
        stream_control: ControlStreamUseCase,
        subtitle_display: DisplaySubtitleUseCase,
    ) -> None:
        self._scene = scene_control
        self._stream = stream_control
        self._subtitle = subtitle_display

    # Scene control
    async def go_to_main(self) -> bool:
        """Switch to main scene."""
        response = await self._scene.go_to_main()
        return response.success

    async def go_to_starting(self) -> bool:
        """Switch to starting scene."""
        response = await self._scene.go_to_starting()
        return response.success

    async def go_to_ending(self) -> bool:
        """Switch to ending scene."""
        response = await self._scene.go_to_ending()
        return response.success

    # Subtitle control
    async def show_subtitle(
        self,
        text: str,
        duration: Optional[float] = None,
    ) -> bool:
        """Show subtitle."""
        response = await self._subtitle.display(
            DisplaySubtitleRequest(text=text, duration_seconds=duration)
        )
        return response.success

    async def hide_subtitle(self) -> bool:
        """Hide subtitle."""
        response = await self._subtitle.hide()
        return response.success

    async def typewriter_subtitle(
        self,
        text: str,
        char_delay: float = 0.05,
    ) -> bool:
        """Show subtitle with typewriter effect."""
        response = await self._subtitle.typewriter(text, char_delay)
        return response.success

    # Stream control
    async def start_stream(self) -> bool:
        """Start streaming."""
        response = await self._stream.start(StreamControlRequest())
        return response.success

    async def stop_stream(self) -> bool:
        """Stop streaming."""
        response = await self._stream.stop(StreamControlRequest())
        return response.success
```

## 7. Composition Root (OBS部分)

```python
# src/ailoveshen/main.py (OBS部分の抜粋)

from ailoveshen.application.use_cases.control_scene import ControlSceneUseCase
from ailoveshen.application.use_cases.control_stream import ControlStreamUseCase
from ailoveshen.application.use_cases.display_subtitle import DisplaySubtitleUseCase
from ailoveshen.domain.value_objects.scene import SceneType
from ailoveshen.infrastructure.adapters.obs.obs_websocket_adapter import OBSWebSocketAdapter
from ailoveshen.presentation.services.obs_service import OBSService


def create_obs_service(
    config: OBSConfig,
    event_publisher: IEventPublisher,
) -> OBSService:
    """Create OBS service with all dependencies."""

    # Infrastructure
    obs_adapter = OBSWebSocketAdapter(
        host=config.host,
        port=config.port,
        password=config.password,
    )

    # Scene name mapping
    scene_names = {
        SceneType.MAIN: config.scenes.main,
        SceneType.STARTING: config.scenes.starting,
        SceneType.ENDING: config.scenes.ending,
    }

    # Use cases
    scene_control = ControlSceneUseCase(
        obs=obs_adapter,
        event_publisher=event_publisher,
        scene_names=scene_names,
    )

    stream_control = ControlStreamUseCase(
        obs=obs_adapter,
        event_publisher=event_publisher,
    )

    subtitle_display = DisplaySubtitleUseCase(
        obs=obs_adapter,
        default_source_name=config.subtitle_source,
    )

    return OBSService(
        scene_control=scene_control,
        stream_control=stream_control,
        subtitle_display=subtitle_display,
    )
```

## 8. 設定

```yaml
obs:
  enabled: false
  host: "localhost"
  port: 4455
  password: "${OBS_PASSWORD}"

  scenes:
    main: "Main Scene"
    starting: "Starting Soon"
    ending: "Ending"

  subtitle_source: "Subtitles"
```
