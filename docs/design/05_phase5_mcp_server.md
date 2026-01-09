# Phase 5: MCP Server 詳細設計書 (Issue #3)

## 1. 概要

Model Context Protocol (MCP) サーバーを実装し、記憶管理・表情操作・感情状態管理を提供します。

### 要件（Issue #3より）
- 記憶管理（長期・短期メモリ）
- 表情操作
- 感情状態管理

## 2. クリーンアーキテクチャに基づくコンポーネント構成

```
src/ailoveshen/
├── domain/
│   ├── entities/
│   │   └── memory.py               # MemoryEntry aggregate
│   ├── value_objects/
│   │   ├── memory_importance.py    # MemoryImportance
│   │   └── emotion.py              # EmotionState (Phase 1から)
│   ├── services/
│   │   └── emotion_decay_service.py # EmotionDecayService
│   └── repositories/
│       └── memory_repository.py    # IMemoryRepository interface
│
├── application/
│   ├── ports/
│   │   ├── input/
│   │   │   ├── store_memory.py         # IStoreMemory
│   │   │   ├── recall_memory.py        # IRecallMemory
│   │   │   └── manage_emotion.py       # IManageEmotion
│   │   └── output/
│   │       ├── memory_persistence.py   # IMemoryPersistence
│   │       └── emotion_state_holder.py # IEmotionStateHolder
│   ├── use_cases/
│   │   ├── store_memory.py         # StoreMemoryUseCase
│   │   ├── recall_memory.py        # RecallMemoryUseCase
│   │   └── manage_emotion.py       # ManageEmotionUseCase
│   └── dto/
│       └── mcp_dto.py              # Request/Response DTOs
│
├── infrastructure/
│   ├── adapters/
│   │   └── persistence/
│   │       ├── sqlite_memory_repository.py # SQLiteMemoryRepository
│   │       └── in_memory_store.py          # InMemoryStore
│   └── mcp/
│       └── mcp_server.py           # MCPServer (MCP protocol adapter)
│
└── presentation/
    └── services/
        └── memory_service.py       # MemoryService (coordinates)
```

## 3. Domain Layer

### 3.1 Entities

#### MemoryEntry (domain/entities/memory.py)

```python
"""Memory entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4


class MemoryType(str, Enum):
    """Types of memories."""
    CONVERSATION = "conversation"    # Chat interactions
    GAME_EVENT = "game_event"        # Important game moments
    VIEWER_INFO = "viewer_info"      # Viewer relationships
    LEARNED_FACT = "learned_fact"    # Learned information


class MemoryImportance(str, Enum):
    """Memory importance levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class MemoryEntry:
    """
    Memory entity - aggregate root for memory management.

    Has identity (id) and lifecycle.
    """
    id: UUID = field(default_factory=uuid4)
    memory_type: MemoryType = MemoryType.CONVERSATION
    importance: MemoryImportance = MemoryImportance.MEDIUM
    summary: str = ""
    details: str = ""
    context: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed: datetime = field(default_factory=datetime.now)
    access_count: int = 0
    viewer_id: Optional[str] = None
    viewer_name: Optional[str] = None

    def access(self) -> None:
        """Record an access to this memory."""
        self.last_accessed = datetime.now()
        self.access_count += 1

    def is_recent(self, hours: int = 24) -> bool:
        """Check if memory was accessed recently."""
        from datetime import timedelta
        return datetime.now() - self.last_accessed < timedelta(hours=hours)

    def is_important(self) -> bool:
        """Check if this is an important memory."""
        return self.importance in (MemoryImportance.HIGH, MemoryImportance.CRITICAL)

    def matches_query(self, query: str) -> bool:
        """Simple text matching for search."""
        query_lower = query.lower()
        return (
            query_lower in self.summary.lower() or
            query_lower in self.details.lower()
        )
```

### 3.2 Repository Interface

#### IMemoryRepository (domain/repositories/memory_repository.py)

```python
"""Memory repository interface (Domain layer)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from ailoveshen.domain.entities.memory import MemoryEntry, MemoryImportance, MemoryType


class IMemoryRepository(ABC):
    """
    Repository interface for memory persistence.

    Defined in Domain layer, implemented in Infrastructure layer.
    """

    @abstractmethod
    async def save(self, memory: MemoryEntry) -> None:
        """Save a memory entry."""
        ...

    @abstractmethod
    async def get(self, memory_id: UUID) -> Optional[MemoryEntry]:
        """Get a memory by ID."""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        memory_types: Optional[list[MemoryType]] = None,
        min_importance: Optional[MemoryImportance] = None,
        viewer_name: Optional[str] = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Search memories."""
        ...

    @abstractmethod
    async def get_by_viewer(
        self,
        viewer_name: str,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Get memories about a specific viewer."""
        ...

    @abstractmethod
    async def get_recent(
        self,
        limit: int = 10,
        memory_type: Optional[MemoryType] = None,
    ) -> list[MemoryEntry]:
        """Get recent memories."""
        ...

    @abstractmethod
    async def delete(self, memory_id: UUID) -> bool:
        """Delete a memory."""
        ...
```

### 3.3 Domain Services

#### EmotionDecayService (domain/services/emotion_decay_service.py)

```python
"""Emotion decay domain service."""

from __future__ import annotations

from ailoveshen.domain.value_objects.emotion import EmotionState, EmotionType


class EmotionDecayService:
    """
    Domain service for emotion decay logic.

    Encapsulates the business rules for how emotions decay over time.
    """

    def __init__(
        self,
        decay_rate: float = 0.1,
        neutral_threshold: float = 0.3,
    ) -> None:
        self._decay_rate = decay_rate
        self._neutral_threshold = neutral_threshold

    def apply_decay(self, current_state: EmotionState) -> EmotionState:
        """
        Apply decay to emotion state.

        Returns new EmotionState after decay.
        """
        # Already neutral with low intensity - no change
        if (current_state.primary == EmotionType.NEUTRAL and
            current_state.intensity <= 0.5):
            return current_state

        # Calculate new intensity
        new_intensity = max(0.0, current_state.intensity - self._decay_rate)

        # If intensity drops below threshold, return to neutral
        if new_intensity < self._neutral_threshold:
            return EmotionState(
                primary=EmotionType.NEUTRAL,
                intensity=0.5,
            )

        # Return decayed state
        return EmotionState(
            primary=current_state.primary,
            intensity=new_intensity,
            secondary=current_state.secondary,
            secondary_intensity=max(0.0, current_state.secondary_intensity - self._decay_rate),
        )

    def apply_trigger(
        self,
        current_state: EmotionState,
        new_emotion: EmotionType,
        intensity_modifier: float,
    ) -> EmotionState:
        """
        Apply emotion trigger to current state.

        Business rules:
        - Same emotion: increases intensity
        - Different emotion: replaces, old becomes secondary
        """
        # Calculate new intensity
        new_intensity = min(1.0, 0.5 * intensity_modifier)

        # Same emotion - intensify
        if new_emotion == current_state.primary:
            new_intensity = min(1.0, current_state.intensity + 0.2 * intensity_modifier)
            return EmotionState(
                primary=new_emotion,
                intensity=new_intensity,
            )

        # Different emotion - replace, keep old as secondary
        return EmotionState(
            primary=new_emotion,
            intensity=new_intensity,
            secondary=current_state.primary,
            secondary_intensity=current_state.intensity * 0.5,
        )
```

## 4. Application Layer

### 4.1 Output Ports

#### IMemoryPersistence (application/ports/output/memory_persistence.py)

```python
"""Memory persistence output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from ailoveshen.domain.entities.memory import MemoryEntry


class IMemoryPersistence(ABC):
    """
    Output port for memory persistence.

    Wraps the domain repository with application-level concerns.
    """

    @abstractmethod
    async def store(self, memory: MemoryEntry) -> None:
        """Store a memory."""
        ...

    @abstractmethod
    async def retrieve(self, memory_id: UUID) -> Optional[MemoryEntry]:
        """Retrieve a memory by ID."""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        viewer_name: Optional[str] = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Search memories."""
        ...
```

#### IEmotionStateHolder (application/ports/output/emotion_state_holder.py)

```python
"""Emotion state holder output port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.domain.value_objects.emotion import EmotionState


class IEmotionStateHolder(ABC):
    """
    Output port for managing emotion state.

    Holds and provides access to current emotion state.
    """

    @abstractmethod
    def get_current(self) -> EmotionState:
        """Get current emotion state."""
        ...

    @abstractmethod
    def update(self, state: EmotionState) -> None:
        """Update emotion state."""
        ...
```

### 4.2 Input Ports

#### IStoreMemory (application/ports/input/store_memory.py)

```python
"""Store memory input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.mcp_dto import (
    StoreMemoryRequest,
    StoreMemoryResponse,
)


class IStoreMemory(ABC):
    """Input port for storing memories."""

    @abstractmethod
    async def execute(
        self,
        request: StoreMemoryRequest,
    ) -> StoreMemoryResponse:
        """Execute store memory use case."""
        ...
```

#### IRecallMemory (application/ports/input/recall_memory.py)

```python
"""Recall memory input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.mcp_dto import (
    RecallMemoryRequest,
    RecallMemoryResponse,
)


class IRecallMemory(ABC):
    """Input port for recalling memories."""

    @abstractmethod
    async def execute(
        self,
        request: RecallMemoryRequest,
    ) -> RecallMemoryResponse:
        """Execute recall memory use case."""
        ...
```

#### IManageEmotion (application/ports/input/manage_emotion.py)

```python
"""Manage emotion input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.mcp_dto import (
    GetEmotionRequest,
    GetEmotionResponse,
    SetEmotionRequest,
    SetEmotionResponse,
)


class IManageEmotion(ABC):
    """Input port for managing emotions."""

    @abstractmethod
    async def get_emotion(
        self,
        request: GetEmotionRequest,
    ) -> GetEmotionResponse:
        """Get current emotion."""
        ...

    @abstractmethod
    async def set_emotion(
        self,
        request: SetEmotionRequest,
    ) -> SetEmotionResponse:
        """Set emotion."""
        ...
```

### 4.3 DTOs

#### MCP DTOs (application/dto/mcp_dto.py)

```python
"""MCP-related DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.entities.memory import MemoryEntry, MemoryImportance
from ailoveshen.domain.value_objects.emotion import EmotionType


@dataclass
class StoreMemoryRequest:
    """Input DTO for storing memory."""
    summary: str
    details: str
    importance: MemoryImportance = MemoryImportance.MEDIUM
    viewer_name: Optional[str] = None


@dataclass
class StoreMemoryResponse:
    """Output DTO for storing memory."""
    success: bool
    memory_id: str
    message: str


@dataclass
class RecallMemoryRequest:
    """Input DTO for recalling memories."""
    query: str
    viewer_name: Optional[str] = None
    limit: int = 5


@dataclass
class RecallMemoryResponse:
    """Output DTO for recalling memories."""
    success: bool
    memories: list[MemoryEntry]
    message: str


@dataclass
class GetEmotionRequest:
    """Input DTO for getting emotion."""
    pass


@dataclass
class GetEmotionResponse:
    """Output DTO for getting emotion."""
    emotion: EmotionType
    intensity: float


@dataclass
class SetEmotionRequest:
    """Input DTO for setting emotion."""
    emotion: EmotionType
    intensity: float = 0.5
    reason: str = ""


@dataclass
class SetEmotionResponse:
    """Output DTO for setting emotion."""
    success: bool
    new_emotion: EmotionType
    new_intensity: float
```

### 4.4 Use Cases

#### StoreMemoryUseCase (application/use_cases/store_memory.py)

```python
"""Store memory use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.mcp_dto import StoreMemoryRequest, StoreMemoryResponse
from ailoveshen.application.ports.input.store_memory import IStoreMemory
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.domain.entities.memory import MemoryEntry, MemoryType
from ailoveshen.domain.events import MemoryStoredEvent
from ailoveshen.domain.repositories.memory_repository import IMemoryRepository


class StoreMemoryUseCase(IStoreMemory):
    """Use case for storing memories."""

    def __init__(
        self,
        memory_repository: IMemoryRepository,
        event_publisher: IEventPublisher,
    ) -> None:
        self._repository = memory_repository
        self._event_publisher = event_publisher

    async def execute(
        self,
        request: StoreMemoryRequest,
    ) -> StoreMemoryResponse:
        """Store a new memory."""
        try:
            # Create memory entity
            memory = MemoryEntry(
                memory_type=MemoryType.LEARNED_FACT,
                importance=request.importance,
                summary=request.summary,
                details=request.details,
                viewer_name=request.viewer_name,
            )

            # Persist
            await self._repository.save(memory)

            # Publish event
            await self._event_publisher.publish(
                MemoryStoredEvent(
                    memory_id=str(memory.id),
                    summary=memory.summary,
                )
            )

            logger.info(f"Stored memory: {memory.summary}")

            return StoreMemoryResponse(
                success=True,
                memory_id=str(memory.id),
                message=f"Memory stored: {memory.summary}",
            )

        except Exception as e:
            logger.error(f"Failed to store memory: {e}")
            return StoreMemoryResponse(
                success=False,
                memory_id="",
                message=f"Error: {e}",
            )
```

#### RecallMemoryUseCase (application/use_cases/recall_memory.py)

```python
"""Recall memory use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.mcp_dto import RecallMemoryRequest, RecallMemoryResponse
from ailoveshen.application.ports.input.recall_memory import IRecallMemory
from ailoveshen.domain.repositories.memory_repository import IMemoryRepository


class RecallMemoryUseCase(IRecallMemory):
    """Use case for recalling memories."""

    def __init__(
        self,
        memory_repository: IMemoryRepository,
    ) -> None:
        self._repository = memory_repository

    async def execute(
        self,
        request: RecallMemoryRequest,
    ) -> RecallMemoryResponse:
        """Search and recall memories."""
        try:
            memories = await self._repository.search(
                query=request.query,
                viewer_name=request.viewer_name,
                limit=request.limit,
            )

            # Update access count for retrieved memories
            for memory in memories:
                memory.access()
                await self._repository.save(memory)

            return RecallMemoryResponse(
                success=True,
                memories=memories,
                message=f"Found {len(memories)} memories",
            )

        except Exception as e:
            logger.error(f"Failed to recall memories: {e}")
            return RecallMemoryResponse(
                success=False,
                memories=[],
                message=f"Error: {e}",
            )
```

#### ManageEmotionUseCase (application/use_cases/manage_emotion.py)

```python
"""Manage emotion use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.mcp_dto import (
    GetEmotionRequest,
    GetEmotionResponse,
    SetEmotionRequest,
    SetEmotionResponse,
)
from ailoveshen.application.ports.input.manage_emotion import IManageEmotion
from ailoveshen.application.ports.output.emotion_state_holder import IEmotionStateHolder
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.domain.events import EmotionChangedEvent
from ailoveshen.domain.services.emotion_decay_service import EmotionDecayService
from ailoveshen.domain.value_objects.emotion import EmotionState


class ManageEmotionUseCase(IManageEmotion):
    """Use case for managing emotions."""

    def __init__(
        self,
        emotion_holder: IEmotionStateHolder,
        decay_service: EmotionDecayService,
        event_publisher: IEventPublisher,
    ) -> None:
        self._holder = emotion_holder
        self._decay_service = decay_service
        self._event_publisher = event_publisher

    async def get_emotion(
        self,
        request: GetEmotionRequest,
    ) -> GetEmotionResponse:
        """Get current emotion state."""
        state = self._holder.get_current()
        return GetEmotionResponse(
            emotion=state.primary,
            intensity=state.intensity,
        )

    async def set_emotion(
        self,
        request: SetEmotionRequest,
    ) -> SetEmotionResponse:
        """Set emotion state."""
        current = self._holder.get_current()

        # Apply trigger through domain service
        new_state = self._decay_service.apply_trigger(
            current_state=current,
            new_emotion=request.emotion,
            intensity_modifier=request.intensity * 2,
        )

        # Update holder
        self._holder.update(new_state)

        # Publish event
        await self._event_publisher.publish(
            EmotionChangedEvent(
                old_emotion=current.primary.value,
                new_emotion=new_state.primary.value,
                intensity=new_state.intensity,
                reason=request.reason,
            )
        )

        logger.info(f"Emotion changed: {current.primary} -> {new_state.primary}")

        return SetEmotionResponse(
            success=True,
            new_emotion=new_state.primary,
            new_intensity=new_state.intensity,
        )
```

## 5. Infrastructure Layer

### 5.1 SQLite Memory Repository

#### SQLiteMemoryRepository (infrastructure/adapters/persistence/sqlite_memory_repository.py)

```python
"""SQLite-based memory repository."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional
from uuid import UUID

from loguru import logger

from ailoveshen.domain.entities.memory import (
    MemoryEntry,
    MemoryImportance,
    MemoryType,
)
from ailoveshen.domain.repositories.memory_repository import IMemoryRepository


class SQLiteMemoryRepository(IMemoryRepository):
    """
    SQLite implementation of memory repository.

    Implements IMemoryRepository from Domain layer.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details TEXT NOT NULL,
                    context TEXT,
                    created_at TEXT NOT NULL,
                    last_accessed TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    viewer_id TEXT,
                    viewer_name TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON memories(memory_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_viewer ON memories(viewer_name)")
            conn.commit()

    @contextmanager
    def _get_conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    async def save(self, memory: MemoryEntry) -> None:
        """Save memory to database."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO memories
                (id, memory_type, importance, summary, details, context,
                 created_at, last_accessed, access_count, viewer_id, viewer_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(memory.id),
                memory.memory_type.value,
                memory.importance.value,
                memory.summary,
                memory.details,
                json.dumps(memory.context),
                memory.created_at.isoformat(),
                memory.last_accessed.isoformat(),
                memory.access_count,
                memory.viewer_id,
                memory.viewer_name,
            ))
            conn.commit()

    async def get(self, memory_id: UUID) -> Optional[MemoryEntry]:
        """Get memory by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?",
                (str(memory_id),),
            ).fetchone()
            return self._row_to_entry(row) if row else None

    async def search(
        self,
        query: str,
        memory_types: Optional[list[MemoryType]] = None,
        min_importance: Optional[MemoryImportance] = None,
        viewer_name: Optional[str] = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Search memories."""
        conditions = ["(summary LIKE ? OR details LIKE ?)"]
        params: list = [f"%{query}%", f"%{query}%"]

        if viewer_name:
            conditions.append("viewer_name = ?")
            params.append(viewer_name)

        sql = f"""
            SELECT * FROM memories
            WHERE {" AND ".join(conditions)}
            ORDER BY last_accessed DESC
            LIMIT ?
        """
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_entry(row) for row in rows]

    async def get_by_viewer(
        self,
        viewer_name: str,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Get memories about a viewer."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM memories
                WHERE viewer_name = ?
                ORDER BY importance DESC, last_accessed DESC
                LIMIT ?
            """, (viewer_name, limit)).fetchall()
            return [self._row_to_entry(row) for row in rows]

    async def get_recent(
        self,
        limit: int = 10,
        memory_type: Optional[MemoryType] = None,
    ) -> list[MemoryEntry]:
        """Get recent memories."""
        if memory_type:
            sql = "SELECT * FROM memories WHERE memory_type = ? ORDER BY last_accessed DESC LIMIT ?"
            params = (memory_type.value, limit)
        else:
            sql = "SELECT * FROM memories ORDER BY last_accessed DESC LIMIT ?"
            params = (limit,)

        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_entry(row) for row in rows]

    async def delete(self, memory_id: UUID) -> bool:
        """Delete a memory."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM memories WHERE id = ?",
                (str(memory_id),),
            )
            conn.commit()
            return cursor.rowcount > 0

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        """Convert database row to entity."""
        return MemoryEntry(
            id=UUID(row["id"]),
            memory_type=MemoryType(row["memory_type"]),
            importance=MemoryImportance(row["importance"]),
            summary=row["summary"],
            details=row["details"],
            context=json.loads(row["context"]) if row["context"] else {},
            created_at=datetime.fromisoformat(row["created_at"]),
            last_accessed=datetime.fromisoformat(row["last_accessed"]),
            access_count=row["access_count"],
            viewer_id=row["viewer_id"],
            viewer_name=row["viewer_name"],
        )
```

### 5.2 MCP Server Adapter

#### MCPServer (infrastructure/mcp/mcp_server.py)

```python
"""MCP Server adapter."""

from __future__ import annotations

from typing import Any

from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ailoveshen.application.dto.mcp_dto import (
    GetEmotionRequest,
    RecallMemoryRequest,
    SetEmotionRequest,
    StoreMemoryRequest,
)
from ailoveshen.application.ports.input.manage_emotion import IManageEmotion
from ailoveshen.application.ports.input.recall_memory import IRecallMemory
from ailoveshen.application.ports.input.store_memory import IStoreMemory
from ailoveshen.domain.entities.memory import MemoryImportance
from ailoveshen.domain.value_objects.emotion import EmotionType


class MCPServerAdapter:
    """
    MCP Server infrastructure adapter.

    Translates MCP protocol to application use cases.
    """

    def __init__(
        self,
        store_memory: IStoreMemory,
        recall_memory: IRecallMemory,
        manage_emotion: IManageEmotion,
    ) -> None:
        self._store_memory = store_memory
        self._recall_memory = recall_memory
        self._manage_emotion = manage_emotion

        self._server = Server("ailoveshen-mcp")
        self._setup_tools()

    def _setup_tools(self) -> None:
        """Register MCP tools."""

        @self._server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="remember",
                    description="Store information in long-term memory",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "details": {"type": "string"},
                            "importance": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                            "viewer_name": {"type": "string"},
                        },
                        "required": ["summary", "details"],
                    },
                ),
                Tool(
                    name="recall",
                    description="Search and retrieve memories",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "viewer_name": {"type": "string"},
                            "limit": {"type": "integer", "default": 5},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="get_emotion",
                    description="Get current emotional state",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="set_emotion",
                    description="Set emotional state",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "emotion": {"type": "string", "enum": [e.value for e in EmotionType]},
                            "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                            "reason": {"type": "string"},
                        },
                        "required": ["emotion"],
                    },
                ),
            ]

        @self._server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            if name == "remember":
                return await self._handle_remember(arguments)
            elif name == "recall":
                return await self._handle_recall(arguments)
            elif name == "get_emotion":
                return await self._handle_get_emotion(arguments)
            elif name == "set_emotion":
                return await self._handle_set_emotion(arguments)
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    async def _handle_remember(self, args: dict) -> list[TextContent]:
        request = StoreMemoryRequest(
            summary=args["summary"],
            details=args["details"],
            importance=MemoryImportance(args.get("importance", "medium")),
            viewer_name=args.get("viewer_name"),
        )
        response = await self._store_memory.execute(request)
        return [TextContent(type="text", text=response.message)]

    async def _handle_recall(self, args: dict) -> list[TextContent]:
        request = RecallMemoryRequest(
            query=args["query"],
            viewer_name=args.get("viewer_name"),
            limit=args.get("limit", 5),
        )
        response = await self._recall_memory.execute(request)

        if not response.memories:
            return [TextContent(type="text", text="No memories found.")]

        lines = ["Found memories:"]
        for m in response.memories:
            lines.append(f"- [{m.importance.value}] {m.summary}")
        return [TextContent(type="text", text="\n".join(lines))]

    async def _handle_get_emotion(self, args: dict) -> list[TextContent]:
        response = await self._manage_emotion.get_emotion(GetEmotionRequest())
        return [TextContent(
            type="text",
            text=f"Current emotion: {response.emotion.value} (intensity: {response.intensity:.2f})",
        )]

    async def _handle_set_emotion(self, args: dict) -> list[TextContent]:
        request = SetEmotionRequest(
            emotion=EmotionType(args["emotion"]),
            intensity=args.get("intensity", 0.5),
            reason=args.get("reason", ""),
        )
        response = await self._manage_emotion.set_emotion(request)
        return [TextContent(
            type="text",
            text=f"Emotion set to: {response.new_emotion.value} ({response.new_intensity:.2f})",
        )]

    async def run(self) -> None:
        """Run the MCP server."""
        logger.info("MCP Server starting...")
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(),
            )
```

## 6. Composition Root (MCP部分)

```python
# src/ailoveshen/main.py (MCP部分の抜粋)

from ailoveshen.application.use_cases.manage_emotion import ManageEmotionUseCase
from ailoveshen.application.use_cases.recall_memory import RecallMemoryUseCase
from ailoveshen.application.use_cases.store_memory import StoreMemoryUseCase
from ailoveshen.domain.services.emotion_decay_service import EmotionDecayService
from ailoveshen.infrastructure.adapters.persistence.sqlite_memory_repository import SQLiteMemoryRepository
from ailoveshen.infrastructure.mcp.mcp_server import MCPServerAdapter


def create_mcp_server(
    config: MCPConfig,
    event_publisher: IEventPublisher,
    emotion_holder: IEmotionStateHolder,
) -> MCPServerAdapter:
    """Create MCP server with all dependencies."""

    # Infrastructure
    memory_repository = SQLiteMemoryRepository(config.memory.db_path)

    # Domain service
    decay_service = EmotionDecayService(
        decay_rate=config.emotion.decay_rate,
    )

    # Use cases
    store_memory = StoreMemoryUseCase(
        memory_repository=memory_repository,
        event_publisher=event_publisher,
    )

    recall_memory = RecallMemoryUseCase(
        memory_repository=memory_repository,
    )

    manage_emotion = ManageEmotionUseCase(
        emotion_holder=emotion_holder,
        decay_service=decay_service,
        event_publisher=event_publisher,
    )

    # MCP adapter
    return MCPServerAdapter(
        store_memory=store_memory,
        recall_memory=recall_memory,
        manage_emotion=manage_emotion,
    )
```

## 7. 設定

```yaml
mcp:
  enabled: true

  memory:
    db_path: "./data/memory.db"
    short_term_limit: 100

  emotion:
    default_state: "neutral"
    decay_rate: 0.1
```
