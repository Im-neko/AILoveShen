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
# src/ailoveshen/factories/mcp.py

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

## 8. Live2D統合 (VTube Studio Plugin API)

### 8.1 概要

LLMからMCP経由でLive2Dアバターの表情・エモートを制御する機能を追加します。

- **Live2D環境**: VTube Studio (WebSocket Plugin API)
- **操作機能**: 表情(Expression)切り替え + エモート/モーション再生
- **統合方式**: `set_emotion` → `EmotionChangedEvent` → `Live2DService`が自動購読

### 8.2 連携フロー

```
LLMがset_emotion("happy")を呼び出し
  → ManageEmotionUseCase.set_emotion()
    → EmotionChangedEvent発行 (EventBus)
      → Live2DService._on_emotion_changed() (subscriber)
        → SyncEmotionExpressionUseCase.sync_expression()
          → VTubeStudioClient.activate_expression("happy.exp3.json")
            → VTube Studioが表情を変更
```

### 8.3 モジュール構成

```
src/ailoveshen/live2d/
├── __init__.py
├── domain/
│   ├── __init__.py
│   ├── value_objects.py          # Expression, Hotkey, ModelInfo
│   ├── events.py                 # ExpressionChangedEvent, MotionTriggeredEvent
│   └── services/
│       ├── __init__.py
│       └── emotion_expression_service.py  # EmotionType → Expression mapping
├── application/
│   ├── __init__.py
│   ├── ports/
│   │   ├── __init__.py
│   │   ├── input/
│   │   │   ├── __init__.py
│   │   │   ├── manage_expression.py      # IManageExpression
│   │   │   └── trigger_motion.py         # ITriggerMotion
│   │   └── output/
│   │       ├── __init__.py
│   │       └── live2d_controller.py      # ILive2DController
│   ├── use_cases/
│   │   ├── __init__.py
│   │   ├── change_expression.py          # ChangeExpressionUseCase
│   │   ├── trigger_motion.py             # TriggerMotionUseCase
│   │   └── sync_emotion_expression.py    # SyncEmotionExpressionUseCase
│   └── dto/
│       ├── __init__.py
│       └── live2d_dto.py
├── infrastructure/
│   ├── __init__.py
│   └── adapters/
│       ├── __init__.py
│       └── vtube_studio/
│           ├── __init__.py
│           ├── client.py                 # VTubeStudioClient (WebSocket)
│           └── auth.py                   # Token management
├── presentation/
│   ├── __init__.py
│   └── services/
│       ├── __init__.py
│       └── live2d_service.py             # Event subscription, coordination
└── factory.py                            # Composition Root
```

### 8.4 Domain Layer

#### 8.4.1 Value Objects (domain/value_objects.py)

```python
"""Live2D domain value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class HotkeyType(str, Enum):
    """Types of VTube Studio hotkeys."""
    EXPRESSION = "expression"
    MOTION = "motion"
    ITEM = "item"
    MODEL_MOVEMENT = "model_movement"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Expression:
    """Live2D expression value object."""
    file_name: str              # e.g., "happy_expression.exp3.json"
    name: str                   # Display name
    is_active: bool = False

    def __post_init__(self) -> None:
        if not self.file_name.endswith(".exp3.json"):
            raise ValueError(
                f"Expression file must end with .exp3.json, got: {self.file_name}"
            )


@dataclass(frozen=True)
class Hotkey:
    """VTube Studio hotkey value object."""
    hotkey_id: str
    name: str
    hotkey_type: HotkeyType
    description: str = ""

    @classmethod
    def from_api_response(cls, data: dict) -> Hotkey:
        """Create Hotkey from VTube Studio API response."""
        hotkey_type_str = data.get("type", "Unknown")
        try:
            hotkey_type = HotkeyType(hotkey_type_str.lower())
        except ValueError:
            hotkey_type = HotkeyType.UNKNOWN
        return cls(
            hotkey_id=data.get("hotkeyID", ""),
            name=data.get("name", ""),
            hotkey_type=hotkey_type,
            description=data.get("description", ""),
        )


@dataclass(frozen=True)
class ModelInfo:
    """Currently loaded Live2D model information."""
    model_id: str
    model_name: str
    is_loaded: bool = True

    @classmethod
    def none_loaded(cls) -> ModelInfo:
        return cls(model_id="", model_name="", is_loaded=False)


@dataclass(frozen=True)
class MotionTriggerResult:
    """Result of triggering a motion/hotkey."""
    success: bool
    hotkey_id: str
    message: str = ""


@dataclass(frozen=True)
class ExpressionChangeResult:
    """Result of changing an expression."""
    success: bool
    expression_file: str
    is_active: bool
    message: str = ""
```

#### 8.4.2 Domain Events (domain/events.py)

```python
"""Live2D domain events."""

from __future__ import annotations

from dataclasses import dataclass, field

from ailoveshen.domain.events import DomainEvent
from ailoveshen.domain.value_objects import EmotionType


@dataclass(frozen=True)
class ExpressionChangedEvent(DomainEvent):
    """Raised when a Live2D expression is activated/deactivated."""
    expression_file: str = field(default="")
    is_active: bool = field(default=True)
    triggered_by: str = field(default="manual")  # "manual", "emotion_sync", "mcp"


@dataclass(frozen=True)
class MotionTriggeredEvent(DomainEvent):
    """Raised when a motion/emote is triggered."""
    hotkey_id: str = field(default="")
    hotkey_name: str = field(default="")
    triggered_by: str = field(default="manual")


@dataclass(frozen=True)
class Live2DConnectedEvent(DomainEvent):
    """Raised when connection to VTube Studio is established."""
    model_name: str = field(default="")
    model_id: str = field(default="")


@dataclass(frozen=True)
class Live2DDisconnectedEvent(DomainEvent):
    """Raised when connection to VTube Studio is lost."""
    reason: str = field(default="")


@dataclass(frozen=True)
class EmotionExpressionSyncedEvent(DomainEvent):
    """Raised when emotion state triggers expression change."""
    emotion: EmotionType = field(default=EmotionType.NEUTRAL)
    expression_file: str = field(default="")
    intensity: float = field(default=0.5)
```

#### 8.4.3 Domain Service (domain/services/emotion_expression_service.py)

```python
"""Emotion to Expression mapping service."""

from __future__ import annotations

from typing import Dict, Optional

from ailoveshen.domain.value_objects import EmotionState, EmotionType


class EmotionExpressionService:
    """
    Domain service for mapping emotions to Live2D expressions.

    Similar to EmotionStyleService in TTS module.
    """

    DEFAULT_EXPRESSION_MAP: Dict[EmotionType, str] = {
        EmotionType.NEUTRAL: "neutral.exp3.json",
        EmotionType.HAPPY: "happy.exp3.json",
        EmotionType.SAD: "sad.exp3.json",
        EmotionType.ANGRY: "angry.exp3.json",
        EmotionType.SURPRISED: "surprised.exp3.json",
        EmotionType.SCARED: "scared.exp3.json",
        EmotionType.EXCITED: "excited.exp3.json",
    }

    def __init__(
        self,
        expression_map: Optional[Dict[EmotionType, str]] = None,
        default_expression: str = "neutral.exp3.json",
        intensity_threshold: float = 0.3,
    ) -> None:
        self._expression_map = expression_map or self.DEFAULT_EXPRESSION_MAP.copy()
        self._default_expression = default_expression
        self._intensity_threshold = intensity_threshold

    def get_expression_for_emotion(self, emotion: EmotionState) -> str:
        """Get the expression file for an emotion state."""
        if emotion.intensity < self._intensity_threshold:
            return self._default_expression
        return self._expression_map.get(emotion.primary, self._default_expression)

    def should_change_expression(
        self,
        current_expression: str,
        new_emotion: EmotionState,
    ) -> bool:
        """Determine if expression should change based on new emotion."""
        target_expression = self.get_expression_for_emotion(new_emotion)
        return target_expression != current_expression

    def update_mapping(self, emotion: EmotionType, expression_file: str) -> None:
        """Update the expression mapping for an emotion type."""
        self._expression_map[emotion] = expression_file
```

### 8.5 Application Layer

#### 8.5.1 Output Port (application/ports/output/live2d_controller.py)

```python
"""Live2D controller output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ailoveshen.live2d.domain.value_objects import (
    Expression,
    ExpressionChangeResult,
    Hotkey,
    ModelInfo,
    MotionTriggerResult,
)


class ILive2DController(ABC):
    """Output port for Live2D control via VTube Studio."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection and authenticate with VTube Studio."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to VTube Studio."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected and authenticated."""
        ...

    @abstractmethod
    async def get_current_model(self) -> ModelInfo:
        """Get information about the currently loaded model."""
        ...

    @abstractmethod
    async def get_available_expressions(self) -> List[Expression]:
        """Get all expressions available for the current model."""
        ...

    @abstractmethod
    async def get_available_hotkeys(self) -> List[Hotkey]:
        """Get all hotkeys configured for the current model."""
        ...

    @abstractmethod
    async def activate_expression(
        self,
        expression_file: str,
        active: bool = True,
    ) -> ExpressionChangeResult:
        """Activate or deactivate an expression."""
        ...

    @abstractmethod
    async def trigger_hotkey(
        self,
        hotkey_id: Optional[str] = None,
        hotkey_name: Optional[str] = None,
    ) -> MotionTriggerResult:
        """Trigger a hotkey by ID or name."""
        ...

    @abstractmethod
    async def deactivate_all_expressions(self) -> None:
        """Deactivate all currently active expressions."""
        ...
```

#### 8.5.2 DTOs (application/dto/live2d_dto.py)

```python
"""Live2D DTOs for application layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ailoveshen.live2d.domain.value_objects import Expression, Hotkey


@dataclass
class ChangeExpressionRequest:
    """Input DTO for changing expression."""
    expression_file: str
    active: bool = True
    deactivate_others: bool = True


@dataclass
class ChangeExpressionResponse:
    """Output DTO for changing expression."""
    success: bool
    expression_file: str
    is_active: bool
    message: str = ""
    error: Optional[str] = None

    @classmethod
    def ok(cls, expression_file: str, is_active: bool) -> ChangeExpressionResponse:
        return cls(
            success=True,
            expression_file=expression_file,
            is_active=is_active,
            message=f"Expression {'activated' if is_active else 'deactivated'}: {expression_file}",
        )

    @classmethod
    def error_response(cls, error: str) -> ChangeExpressionResponse:
        return cls(success=False, expression_file="", is_active=False, error=error, message=f"Failed: {error}")


@dataclass
class TriggerMotionRequest:
    """Input DTO for triggering motion."""
    hotkey_id: Optional[str] = None
    hotkey_name: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.hotkey_id and not self.hotkey_name:
            raise ValueError("Must provide either hotkey_id or hotkey_name")


@dataclass
class TriggerMotionResponse:
    """Output DTO for triggering motion."""
    success: bool
    hotkey_id: str = ""
    message: str = ""
    error: Optional[str] = None

    @classmethod
    def ok(cls, hotkey_id: str) -> TriggerMotionResponse:
        return cls(success=True, hotkey_id=hotkey_id, message=f"Motion triggered: {hotkey_id}")

    @classmethod
    def error_response(cls, error: str) -> TriggerMotionResponse:
        return cls(success=False, error=error, message=f"Failed: {error}")


@dataclass
class GetExpressionsResponse:
    """Output DTO for getting expressions."""
    success: bool
    expressions: List[Expression] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class GetHotkeysResponse:
    """Output DTO for getting hotkeys."""
    success: bool
    hotkeys: List[Hotkey] = field(default_factory=list)
    error: Optional[str] = None
```

### 8.6 Infrastructure Layer

#### 8.6.1 VTube Studio Client (infrastructure/adapters/vtube_studio/client.py)

```python
"""VTube Studio WebSocket client adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

import websockets
from loguru import logger
from websockets.client import WebSocketClientProtocol

from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.live2d.application.ports.output.live2d_controller import ILive2DController
from ailoveshen.live2d.domain.value_objects import (
    Expression,
    ExpressionChangeResult,
    Hotkey,
    ModelInfo,
    MotionTriggerResult,
)
from ailoveshen.live2d.infrastructure.adapters.vtube_studio.auth import VTubeStudioAuth


class VTubeStudioError(AILoveShenError):
    """VTube Studio specific errors."""
    pass


class VTubeStudioClient(ILive2DController):
    """Infrastructure adapter for VTube Studio Plugin API."""

    API_NAME = "VTubeStudioPublicAPI"
    API_VERSION = "1.0"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8001,
        plugin_name: str = "AILoveShen",
        plugin_developer: str = "AILoveShen",
        plugin_icon: Optional[str] = None,
        auth_token_file: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._ws_url = f"ws://{host}:{port}"
        self._plugin_name = plugin_name
        self._plugin_developer = plugin_developer
        self._plugin_icon = plugin_icon
        self._timeout = timeout_seconds

        self._ws: Optional[WebSocketClientProtocol] = None
        self._authenticated = False
        self._lock = asyncio.Lock()

        self._auth = VTubeStudioAuth(
            plugin_name=plugin_name,
            plugin_developer=plugin_developer,
            plugin_icon=plugin_icon,
            token_file=auth_token_file,
        )

    async def connect(self) -> None:
        """Establish connection and authenticate."""
        async with self._lock:
            try:
                self._ws = await websockets.connect(
                    self._ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                )
                logger.info(f"Connected to VTube Studio at {self._ws_url}")
                await self._authenticate()
            except Exception as e:
                self._ws = None
                self._authenticated = False
                raise ConnectionError(f"Failed to connect to VTube Studio: {e}") from e

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        async with self._lock:
            if self._ws:
                await self._ws.close()
                self._ws = None
            self._authenticated = False
            logger.info("Disconnected from VTube Studio")

    def is_connected(self) -> bool:
        """Check if connected and authenticated."""
        return self._ws is not None and self._authenticated

    async def _authenticate(self) -> None:
        """Perform authentication flow."""
        token = self._auth.load_token()
        if token:
            success = await self._try_authenticate_with_token(token)
            if success:
                self._authenticated = True
                return

        token = await self._request_new_token()
        success = await self._try_authenticate_with_token(token)
        if not success:
            raise VTubeStudioError("Authentication failed")

        self._auth.save_token(token)
        self._authenticated = True
        logger.info("Authenticated with VTube Studio")

    async def _request_new_token(self) -> str:
        """Request a new authentication token."""
        request = self._build_request(
            "AuthenticationTokenRequest",
            {
                "pluginName": self._plugin_name,
                "pluginDeveloper": self._plugin_developer,
                **({"pluginIcon": self._plugin_icon} if self._plugin_icon else {}),
            },
        )
        response = await self._send_request(request)
        if "authenticationToken" not in response.get("data", {}):
            raise VTubeStudioError("Failed to get authentication token")
        return response["data"]["authenticationToken"]

    async def _try_authenticate_with_token(self, token: str) -> bool:
        """Attempt authentication with given token."""
        request = self._build_request(
            "AuthenticationRequest",
            {
                "pluginName": self._plugin_name,
                "pluginDeveloper": self._plugin_developer,
                "authenticationToken": token,
            },
        )
        response = await self._send_request(request)
        return response.get("data", {}).get("authenticated", False)

    async def get_current_model(self) -> ModelInfo:
        """Get current model information."""
        request = self._build_request("CurrentModelRequest", {})
        response = await self._send_request(request)
        data = response.get("data", {})
        if not data.get("modelLoaded", False):
            return ModelInfo.none_loaded()
        return ModelInfo(
            model_id=data.get("modelID", ""),
            model_name=data.get("modelName", ""),
            is_loaded=True,
        )

    async def get_available_expressions(self) -> List[Expression]:
        """Get all expressions for current model."""
        request = self._build_request("ExpressionStateRequest", {"details": True})
        response = await self._send_request(request)
        expressions = []
        for exp_data in response.get("data", {}).get("expressions", []):
            expressions.append(Expression(
                file_name=exp_data.get("file", ""),
                name=exp_data.get("name", ""),
                is_active=exp_data.get("active", False),
            ))
        return expressions

    async def get_available_hotkeys(self) -> List[Hotkey]:
        """Get all hotkeys for current model."""
        request = self._build_request("HotkeysInCurrentModelRequest", {})
        response = await self._send_request(request)
        hotkeys = []
        for hk_data in response.get("data", {}).get("availableHotkeys", []):
            hotkeys.append(Hotkey.from_api_response(hk_data))
        return hotkeys

    async def activate_expression(
        self,
        expression_file: str,
        active: bool = True,
    ) -> ExpressionChangeResult:
        """Activate or deactivate an expression."""
        request = self._build_request(
            "ExpressionActivationRequest",
            {"expressionFile": expression_file, "active": active},
        )
        try:
            await self._send_request(request)
            return ExpressionChangeResult(
                success=True,
                expression_file=expression_file,
                is_active=active,
            )
        except VTubeStudioError as e:
            return ExpressionChangeResult(
                success=False,
                expression_file=expression_file,
                is_active=not active,
                message=str(e),
            )

    async def trigger_hotkey(
        self,
        hotkey_id: Optional[str] = None,
        hotkey_name: Optional[str] = None,
    ) -> MotionTriggerResult:
        """Trigger a hotkey."""
        if not hotkey_id and not hotkey_name:
            raise ValueError("Must provide hotkey_id or hotkey_name")
        request = self._build_request(
            "HotkeyTriggerRequest",
            {"hotkeyID": hotkey_id or hotkey_name},
        )
        try:
            await self._send_request(request)
            return MotionTriggerResult(success=True, hotkey_id=hotkey_id or hotkey_name or "")
        except VTubeStudioError as e:
            return MotionTriggerResult(success=False, hotkey_id=hotkey_id or hotkey_name or "", message=str(e))

    async def deactivate_all_expressions(self) -> None:
        """Deactivate all active expressions."""
        expressions = await self.get_available_expressions()
        for exp in expressions:
            if exp.is_active:
                await self.activate_expression(exp.file_name, active=False)

    def _build_request(self, message_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Build a VTube Studio API request."""
        return {
            "apiName": self.API_NAME,
            "apiVersion": self.API_VERSION,
            "requestID": str(uuid4()),
            "messageType": message_type,
            "data": data,
        }

    async def _send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send request and wait for response."""
        if not self._ws:
            raise VTubeStudioError("Not connected")
        try:
            await self._ws.send(json.dumps(request))
            response_text = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout)
            response = json.loads(response_text)
            if "errorID" in response.get("data", {}):
                error_msg = response["data"].get("message", "Unknown error")
                raise VTubeStudioError(f"API Error: {error_msg}")
            return response
        except asyncio.TimeoutError:
            raise VTubeStudioError("Request timed out")
        except websockets.exceptions.ConnectionClosed:
            self._authenticated = False
            raise VTubeStudioError("Connection closed")
```

### 8.7 追加MCPツール

既存の`MCPServerAdapter`に以下のツールを追加します：

```python
# MCP tools for Live2D control (infrastructure/mcp/mcp_server.py に追加)

Tool(
    name="trigger_emote",
    description="Live2Dのエモート/モーションをホットキー名またはIDで再生",
    inputSchema={
        "type": "object",
        "properties": {
            "hotkey_name": {"type": "string", "description": "トリガーするホットキー名"},
            "hotkey_id": {"type": "string", "description": "トリガーするホットキーID（名前の代替）"},
        },
        "anyOf": [
            {"required": ["hotkey_name"]},
            {"required": ["hotkey_id"]},
        ],
    },
),
Tool(
    name="set_expression",
    description="Live2Dの表情を直接指定して変更",
    inputSchema={
        "type": "object",
        "properties": {
            "expression_file": {"type": "string", "description": "表情ファイル名（例: 'happy.exp3.json'）"},
            "active": {"type": "boolean", "default": True, "description": "有効化/無効化"},
        },
        "required": ["expression_file"],
    },
),
Tool(
    name="get_expressions",
    description="現在のモデルで利用可能なLive2D表情一覧を取得",
    inputSchema={"type": "object", "properties": {}},
),
Tool(
    name="get_hotkeys",
    description="現在のモデルで利用可能なホットキー/エモート一覧を取得",
    inputSchema={"type": "object", "properties": {}},
),
```

**注**: 既存の`set_emotion`ツールは変更不要です。`EmotionChangedEvent`がEventBus経由で発行され、`Live2DService`が自動的に購読してLive2D表情を同期します。

### 8.8 設定 (config/default.yaml)

```yaml
# Live2D (VTube Studio) Integration
live2d:
  enabled: true

  # VTube Studio connection
  vtube_studio:
    host: "localhost"
    port: 8001
    timeout_seconds: 10

  # Plugin identity (shown in VTube Studio)
  plugin:
    name: "AILoveShen"
    developer: "AILoveShen"
    # icon: "base64_encoded_128x128_png"  # Optional

  # Authentication token storage
  auth:
    token_file: "data/.vts_token.json"

  # Emotion to Expression mapping
  emotion_expression_map:
    neutral: "neutral.exp3.json"
    happy: "happy.exp3.json"
    sad: "sad.exp3.json"
    angry: "angry.exp3.json"
    surprised: "surprised.exp3.json"
    scared: "scared.exp3.json"
    excited: "excited.exp3.json"

  # Expression sync settings
  sync:
    auto_sync_emotions: true       # Automatically sync emotion -> expression
    intensity_threshold: 0.3       # Minimum intensity to trigger change
    default_expression: "neutral.exp3.json"
```

### 8.9 依存関係

```toml
# pyproject.toml
[project.optional-dependencies]
live2d = [
    "websockets>=12.0",
]
```

### 8.10 テスト構成

```
tests/unit/live2d/
├── __init__.py
├── domain/
│   ├── __init__.py
│   ├── test_value_objects.py
│   └── test_emotion_expression_service.py
├── application/
│   ├── __init__.py
│   ├── test_dto.py
│   ├── test_change_expression_use_case.py
│   ├── test_trigger_motion_use_case.py
│   └── test_sync_emotion_expression_use_case.py
├── infrastructure/
│   ├── __init__.py
│   ├── test_vtube_studio_client.py
│   └── test_vtube_studio_auth.py
└── presentation/
    ├── __init__.py
    └── test_live2d_service.py
```
