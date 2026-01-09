# Phase 1: Core Infrastructure 詳細設計書（クリーンアーキテクチャ版）

## 1. クリーンアーキテクチャ概要

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Presentation Layer                             │
│                         (CLI, API, Event Handlers)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                              Application Layer                              │
│                     (Use Cases, Application Services)                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                Domain Layer                                 │
│              (Entities, Value Objects, Domain Services, Events)             │
├─────────────────────────────────────────────────────────────────────────────┤
│                             Infrastructure Layer                            │
│        (External Services, Persistence, Messaging, Configuration)           │
└─────────────────────────────────────────────────────────────────────────────┘

依存関係の方向: Presentation → Application → Domain ← Infrastructure
                                    ↑_______________|
                              (依存性逆転の原則)
```

## 2. ディレクトリ構造

```
AILoveShen/
├── pyproject.toml
├── config/
│   ├── default.yaml
│   ├── development.yaml
│   └── production.yaml
│
├── src/
│   └── ailoveshen/
│       ├── __init__.py
│       │
│       │  ╔═══════════════════════════════════════════════════════════════╗
│       │  ║                      DOMAIN LAYER                             ║
│       │  ║  ビジネスの核心。外部依存なし。フレームワーク非依存。         ║
│       │  ╚═══════════════════════════════════════════════════════════════╝
│       │
│       ├── domain/
│       │   ├── __init__.py
│       │   │
│       │   ├── entities/              # エンティティ（識別子を持つオブジェクト）
│       │   │   ├── __init__.py
│       │   │   ├── chat_message.py    # ChatMessage, ChatUser
│       │   │   ├── game_state.py      # GameState, GameEvent
│       │   │   ├── memory.py          # Memory (Short/Long term)
│       │   │   └── stream_session.py  # StreamSession
│       │   │
│       │   ├── value_objects/         # 値オブジェクト（不変、識別子なし）
│       │   │   ├── __init__.py
│       │   │   ├── position.py        # Position, Rotation
│       │   │   ├── emotion.py         # EmotionState, EmotionType
│       │   │   ├── speech.py          # SpeechRequest, SpeechPriority
│       │   │   └── filter_result.py   # FilterResult
│       │   │
│       │   ├── events/                # ドメインイベント
│       │   │   ├── __init__.py
│       │   │   ├── chat_events.py     # MessageReceived, ResponseGenerated
│       │   │   ├── game_events.py     # StateChanged, ActionExecuted
│       │   │   ├── stream_events.py   # StreamStarted, StreamEnded
│       │   │   └── emotion_events.py  # EmotionChanged
│       │   │
│       │   ├── services/              # ドメインサービス（複数エンティティにまたがるロジック）
│       │   │   ├── __init__.py
│       │   │   ├── emotion_service.py # 感情状態の計算ロジック
│       │   │   └── priority_service.py # 発話優先度の計算ロジック
│       │   │
│       │   └── repositories/          # リポジトリインターフェース（抽象のみ）
│       │       ├── __init__.py
│       │       ├── memory_repository.py
│       │       └── session_repository.py
│       │
│       │  ╔═══════════════════════════════════════════════════════════════╗
│       │  ║                    APPLICATION LAYER                          ║
│       │  ║  ユースケースの実装。ドメイン層のみに依存。                   ║
│       │  ╚═══════════════════════════════════════════════════════════════╝
│       │
│       ├── application/
│       │   ├── __init__.py
│       │   │
│       │   ├── ports/                 # ポート（インターフェース定義）
│       │   │   ├── __init__.py
│       │   │   │
│       │   │   ├── input/             # 入力ポート（ユースケースのインターフェース）
│       │   │   │   ├── __init__.py
│       │   │   │   ├── generate_commentary.py
│       │   │   │   ├── respond_to_chat.py
│       │   │   │   ├── filter_comment.py
│       │   │   │   ├── execute_game_action.py
│       │   │   │   └── manage_stream.py
│       │   │   │
│       │   │   └── output/            # 出力ポート（外部サービスのインターフェース）
│       │   │       ├── __init__.py
│       │   │       ├── text_generator.py      # ITextGenerator
│       │   │       ├── comment_filter.py      # ICommentFilter
│       │   │       ├── speech_synthesizer.py  # ISpeechSynthesizer
│       │   │       ├── chat_gateway.py        # IChatGateway
│       │   │       ├── game_gateway.py        # IGameGateway
│       │   │       ├── stream_controller.py   # IStreamController
│       │   │       └── event_publisher.py     # IEventPublisher
│       │   │
│       │   ├── use_cases/             # ユースケース実装
│       │   │   ├── __init__.py
│       │   │   ├── generate_commentary.py     # GenerateCommentaryUseCase
│       │   │   ├── respond_to_chat.py         # RespondToChatUseCase
│       │   │   ├── filter_comment.py          # FilterCommentUseCase
│       │   │   ├── execute_game_action.py     # ExecuteGameActionUseCase
│       │   │   ├── manage_emotion.py          # ManageEmotionUseCase
│       │   │   ├── store_memory.py            # StoreMemoryUseCase
│       │   │   ├── recall_memory.py           # RecallMemoryUseCase
│       │   │   └── manage_stream.py           # ManageStreamUseCase
│       │   │
│       │   ├── services/              # アプリケーションサービス
│       │   │   ├── __init__.py
│       │   │   ├── orchestrator.py    # メイン/サブループ調整
│       │   │   └── speech_queue.py    # 発話キュー管理
│       │   │
│       │   └── dto/                   # データ転送オブジェクト
│       │       ├── __init__.py
│       │       ├── commentary_request.py
│       │       ├── chat_response_request.py
│       │       └── filter_request.py
│       │
│       │  ╔═══════════════════════════════════════════════════════════════╗
│       │  ║                   INFRASTRUCTURE LAYER                        ║
│       │  ║  外部サービスとの連携。出力ポートの実装。                     ║
│       │  ╚═══════════════════════════════════════════════════════════════╝
│       │
│       ├── infrastructure/
│       │   ├── __init__.py
│       │   │
│       │   ├── adapters/              # 外部サービスアダプター
│       │   │   ├── __init__.py
│       │   │   │
│       │   │   ├── twitch/            # Twitch IRC アダプター
│       │   │   │   ├── __init__.py
│       │   │   │   ├── chat_gateway.py        # IChatGateway実装
│       │   │   │   ├── auth.py
│       │   │   │   └── mapper.py              # Twitch→ドメインモデル変換
│       │   │   │
│       │   │   ├── gemini/            # Gemini API アダプター
│       │   │   │   ├── __init__.py
│       │   │   │   ├── text_generator.py      # ITextGenerator実装
│       │   │   │   ├── comment_filter.py      # ICommentFilter実装
│       │   │   │   └── prompts.py
│       │   │   │
│       │   │   ├── tts/               # Style-Bert-VITS2 アダプター
│       │   │   │   ├── __init__.py
│       │   │   │   ├── speech_synthesizer.py  # ISpeechSynthesizer実装
│       │   │   │   └── audio_player.py
│       │   │   │
│       │   │   ├── nitrogen/          # NitroGen アダプター
│       │   │   │   ├── __init__.py
│       │   │   │   ├── game_gateway.py        # IGameGateway実装
│       │   │   │   └── mapper.py
│       │   │   │
│       │   │   ├── obs/               # OBS WebSocket アダプター
│       │   │   │   ├── __init__.py
│       │   │   │   ├── stream_controller.py   # IStreamController実装
│       │   │   │   └── scene_manager.py
│       │   │   │
│       │   │   └── mcp/               # MCP サーバーアダプター
│       │   │       ├── __init__.py
│       │   │       └── server.py
│       │   │
│       │   ├── persistence/           # 永続化
│       │   │   ├── __init__.py
│       │   │   ├── sqlite/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── memory_repository.py   # IMemoryRepository実装
│       │   │   │   └── session_repository.py
│       │   │   └── in_memory/
│       │   │       ├── __init__.py
│       │   │       └── memory_repository.py   # テスト用インメモリ実装
│       │   │
│       │   ├── messaging/             # メッセージング
│       │   │   ├── __init__.py
│       │   │   ├── event_bus.py               # IEventPublisher実装
│       │   │   └── async_event_bus.py
│       │   │
│       │   └── config/                # 設定
│       │       ├── __init__.py
│       │       ├── settings.py        # Pydantic Settings
│       │       └── loader.py          # YAML loader
│       │
│       │  ╔═══════════════════════════════════════════════════════════════╗
│       │  ║                    PRESENTATION LAYER                         ║
│       │  ║  ユーザー/外部システムとのインターフェース                    ║
│       │  ╚═══════════════════════════════════════════════════════════════╝
│       │
│       ├── presentation/
│       │   ├── __init__.py
│       │   │
│       │   ├── cli/                   # CLIエントリーポイント
│       │   │   ├── __init__.py
│       │   │   └── main.py
│       │   │
│       │   └── event_handlers/        # イベントハンドラー
│       │       ├── __init__.py
│       │       ├── chat_handler.py    # チャットメッセージ受信時の処理
│       │       ├── game_handler.py    # ゲームイベント受信時の処理
│       │       └── speech_handler.py  # 発話完了時の処理
│       │
│       │  ╔═══════════════════════════════════════════════════════════════╗
│       │  ║                     COMPOSITION ROOT                          ║
│       │  ║  依存関係の組み立て。DIコンテナ。                             ║
│       │  ╚═══════════════════════════════════════════════════════════════╝
│       │
│       └── main.py                    # Composition Root & Entry Point
│
├── tests/
│   ├── unit/
│   │   ├── domain/                    # ドメイン層のテスト（依存なし）
│   │   ├── application/               # ユースケースのテスト（モック使用）
│   │   └── infrastructure/            # アダプターのテスト
│   └── integration/
│       └── ...
│
└── data/
    ├── memory.db
    └── logs/
```

## 3. 依存関係のルール

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  依存関係の方向                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  presentation/ ──────────────────────────────────┐                         │
│       │                                          │                         │
│       ▼                                          │                         │
│  application/ ───────────────────────┐           │                         │
│       │                              │           │                         │
│       │ (uses)                       │           │                         │
│       ▼                              ▼           ▼                         │
│  domain/ ◄──────────────────── infrastructure/                             │
│                                      │                                     │
│        (implements interfaces)       │                                     │
│                                      │                                     │
│  application/ports/output/ ◄─────────┘                                     │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  禁止される依存:                                                            │
│  ✗ domain/ → application/                                                  │
│  ✗ domain/ → infrastructure/                                               │
│  ✗ domain/ → presentation/                                                 │
│  ✗ application/ → infrastructure/ (具体クラス)                             │
│  ✗ application/ → presentation/                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 4. ドメイン層

### 4.1 エンティティ (domain/entities/)

```python
# domain/entities/chat_message.py
"""Chat message entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


@dataclass
class ChatUser:
    """Chat user value object."""
    id: str
    name: str
    display_name: str
    is_broadcaster: bool = False
    is_moderator: bool = False
    is_subscriber: bool = False


@dataclass
class ChatMessage:
    """
    Chat message entity.

    エンティティは識別子（id）を持ち、ライフサイクルを通じて追跡される。
    """
    id: str
    user: ChatUser
    content: str
    timestamp: datetime = field(default_factory=datetime.now)

    # フィルタリング結果（ユースケースで設定）
    filter_result: Optional["FilterResult"] = None

    @classmethod
    def create(cls, user: ChatUser, content: str) -> "ChatMessage":
        """ファクトリメソッド"""
        return cls(
            id=str(uuid.uuid4()),
            user=user,
            content=content,
        )

    def is_from_privileged_user(self) -> bool:
        """特権ユーザー（配信者/モデレーター）からのメッセージか"""
        return self.user.is_broadcaster or self.user.is_moderator

    def should_always_respond(self) -> bool:
        """常に応答すべきメッセージか（ビジネスルール）"""
        return self.is_from_privileged_user()
```

```python
# domain/entities/game_state.py
"""Game state entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ailoveshen.domain.value_objects.position import Position, Rotation


@dataclass
class GameState:
    """
    Game state entity.

    ゲームの現在状態を表すエンティティ。
    """
    timestamp: datetime = field(default_factory=datetime.now)

    # Player state
    health: float = 20.0
    max_health: float = 20.0
    hunger: float = 20.0

    # Position
    position: Position = field(default_factory=Position)
    rotation: Rotation = field(default_factory=Rotation)

    # Environment
    biome: str = "plains"
    weather: str = "clear"
    time_of_day: int = 0

    # Nearby entities
    nearby_hostile_count: int = 0
    nearest_hostile_distance: Optional[float] = None

    def is_in_danger(self) -> bool:
        """危険な状況か（ビジネスルール）"""
        if self.health < self.max_health * 0.3:
            return True
        if self.nearest_hostile_distance and self.nearest_hostile_distance < 10:
            return True
        return False

    def get_health_percentage(self) -> float:
        """体力の割合を取得"""
        return self.health / self.max_health if self.max_health > 0 else 0
```

### 4.2 値オブジェクト (domain/value_objects/)

```python
# domain/value_objects/emotion.py
"""Emotion value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EmotionType(str, Enum):
    """感情タイプ"""
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    SCARED = "scared"
    EXCITED = "excited"


@dataclass(frozen=True)  # 不変
class EmotionState:
    """
    感情状態の値オブジェクト。

    値オブジェクトは不変（frozen=True）で、同値性で比較される。
    """
    primary: EmotionType = EmotionType.NEUTRAL
    intensity: float = 0.5  # 0.0 - 1.0

    def __post_init__(self):
        # バリデーション
        if not 0.0 <= self.intensity <= 1.0:
            raise ValueError("Intensity must be between 0.0 and 1.0")

    def with_intensity(self, new_intensity: float) -> "EmotionState":
        """新しい強度で新しい EmotionState を作成"""
        return EmotionState(
            primary=self.primary,
            intensity=max(0.0, min(1.0, new_intensity)),
        )

    def decay(self, rate: float) -> "EmotionState":
        """感情の減衰（新しいインスタンスを返す）"""
        new_intensity = max(0.0, self.intensity - rate)
        if new_intensity < 0.3:
            return EmotionState(EmotionType.NEUTRAL, 0.5)
        return self.with_intensity(new_intensity)
```

```python
# domain/value_objects/speech.py
"""Speech value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class SpeechPriority(IntEnum):
    """発話優先度"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    INTERRUPT = 3


@dataclass(frozen=True)
class SpeechRequest:
    """
    発話リクエストの値オブジェクト。
    """
    text: str
    priority: SpeechPriority = SpeechPriority.NORMAL
    style: str = "Neutral"
    source: str = "unknown"  # "commentary" | "response" | "game_event"

    def should_interrupt(self) -> bool:
        """現在の発話を中断すべきか"""
        return self.priority >= SpeechPriority.INTERRUPT
```

### 4.3 ドメインイベント (domain/events/)

```python
# domain/events/chat_events.py
"""Chat domain events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ailoveshen.domain.entities.chat_message import ChatMessage


@dataclass(frozen=True)
class DomainEvent:
    """ドメインイベント基底クラス"""
    occurred_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class ChatMessageReceived(DomainEvent):
    """チャットメッセージ受信イベント"""
    message: ChatMessage


@dataclass(frozen=True)
class ChatResponseGenerated(DomainEvent):
    """チャット応答生成イベント"""
    original_message: ChatMessage
    response_text: str


@dataclass(frozen=True)
class CommentFiltered(DomainEvent):
    """コメントフィルタリング完了イベント"""
    message: ChatMessage
    should_respond: bool
    score: float
    reason: str
```

### 4.4 ドメインサービス (domain/services/)

```python
# domain/services/emotion_service.py
"""Emotion domain service."""

from __future__ import annotations

from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.value_objects.emotion import EmotionState, EmotionType


class EmotionService:
    """
    感情計算のドメインサービス。

    複数のエンティティ/値オブジェクトにまたがるビジネスロジック。
    """

    @staticmethod
    def calculate_from_game_event(
        current: EmotionState,
        event_type: str,
    ) -> EmotionState:
        """ゲームイベントから感情を計算"""
        emotion_map = {
            "damage_taken": (EmotionType.SCARED, 0.8),
            "death": (EmotionType.SAD, 1.0),
            "item_pickup": (EmotionType.HAPPY, 0.6),
            "entity_killed": (EmotionType.EXCITED, 0.7),
            "achievement": (EmotionType.HAPPY, 0.9),
        }

        if event_type in emotion_map:
            emotion_type, intensity = emotion_map[event_type]
            return EmotionState(emotion_type, intensity)

        return current

    @staticmethod
    def calculate_from_game_state(
        current: EmotionState,
        game_state: GameState,
    ) -> EmotionState:
        """ゲーム状態から感情を計算"""
        if game_state.is_in_danger():
            return EmotionState(EmotionType.SCARED, 0.7)

        return current
```

```python
# domain/services/priority_service.py
"""Speech priority domain service."""

from __future__ import annotations

from ailoveshen.domain.entities.chat_message import ChatMessage
from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.value_objects.speech import SpeechPriority


class PriorityService:
    """発話優先度計算のドメインサービス"""

    @staticmethod
    def calculate_commentary_priority(game_state: GameState) -> SpeechPriority:
        """ゲーム状態から実況の優先度を計算"""
        if game_state.is_in_danger():
            return SpeechPriority.HIGH
        return SpeechPriority.NORMAL

    @staticmethod
    def calculate_response_priority(message: ChatMessage) -> SpeechPriority:
        """チャットメッセージから応答の優先度を計算"""
        if message.should_always_respond():
            return SpeechPriority.INTERRUPT

        # 質問には割り込み
        if "?" in message.content:
            return SpeechPriority.INTERRUPT

        return SpeechPriority.NORMAL
```

### 4.5 リポジトリインターフェース (domain/repositories/)

```python
# domain/repositories/memory_repository.py
"""Memory repository interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ailoveshen.domain.entities.memory import LongTermMemory, MemoryQuery


class IMemoryRepository(ABC):
    """
    メモリリポジトリのインターフェース。

    ドメイン層では抽象のみを定義。
    具体実装はインフラ層で行う（依存性逆転）。
    """

    @abstractmethod
    async def save(self, memory: LongTermMemory) -> None:
        """メモリを保存"""
        pass

    @abstractmethod
    async def find_by_id(self, memory_id: str) -> Optional[LongTermMemory]:
        """IDでメモリを検索"""
        pass

    @abstractmethod
    async def search(self, query: MemoryQuery) -> list[LongTermMemory]:
        """クエリでメモリを検索"""
        pass

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """メモリを削除"""
        pass
```

## 5. アプリケーション層

### 5.1 出力ポート (application/ports/output/)

```python
# application/ports/output/text_generator.py
"""Text generator output port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.domain.entities.chat_message import ChatMessage
from ailoveshen.domain.entities.game_state import GameState


class ITextGenerator(ABC):
    """
    テキスト生成の出力ポート。

    ユースケースはこのインターフェースに依存する。
    具体実装（Gemini等）はインフラ層で行う。
    """

    @abstractmethod
    async def generate_commentary(
        self,
        game_state: GameState,
        context: str,
        emotion: str,
    ) -> str:
        """ゲーム実況を生成"""
        pass

    @abstractmethod
    async def generate_response(
        self,
        message: ChatMessage,
        context: str,
        emotion: str,
    ) -> str:
        """チャット応答を生成"""
        pass
```

```python
# application/ports/output/speech_synthesizer.py
"""Speech synthesizer output port."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ISpeechSynthesizer(ABC):
    """音声合成の出力ポート"""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        style: str = "Neutral",
    ) -> bytes:
        """テキストを音声に変換"""
        pass

    @abstractmethod
    async def play(
        self,
        audio_data: bytes,
        allow_interrupt: bool = True,
    ) -> bool:
        """音声を再生。Trueなら完了、Falseなら中断"""
        pass

    @abstractmethod
    def stop(self) -> None:
        """再生を停止"""
        pass
```

```python
# application/ports/output/event_publisher.py
"""Event publisher output port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.domain.events.chat_events import DomainEvent


class IEventPublisher(ABC):
    """イベント発行の出力ポート"""

    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """ドメインイベントを発行"""
        pass
```

### 5.2 入力ポート (application/ports/input/)

```python
# application/ports/input/respond_to_chat.py
"""Respond to chat input port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ailoveshen.domain.entities.chat_message import ChatMessage


@dataclass
class RespondToChatRequest:
    """チャット応答リクエストDTO"""
    message: ChatMessage
    context: str = ""


@dataclass
class RespondToChatResponse:
    """チャット応答レスポンスDTO"""
    response_text: str
    emotion: str
    should_interrupt: bool


class IRespondToChat(ABC):
    """チャット応答ユースケースの入力ポート"""

    @abstractmethod
    async def execute(self, request: RespondToChatRequest) -> RespondToChatResponse:
        pass
```

### 5.3 ユースケース (application/use_cases/)

```python
# application/use_cases/respond_to_chat.py
"""Respond to chat use case."""

from __future__ import annotations

from ailoveshen.application.ports.input.respond_to_chat import (
    IRespondToChat,
    RespondToChatRequest,
    RespondToChatResponse,
)
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.events.chat_events import ChatResponseGenerated
from ailoveshen.domain.services.priority_service import PriorityService
from ailoveshen.domain.value_objects.emotion import EmotionState
from ailoveshen.domain.value_objects.speech import SpeechPriority


class RespondToChatUseCase(IRespondToChat):
    """
    チャット応答ユースケース。

    依存はすべて出力ポート（インターフェース）経由。
    具体実装には依存しない。
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        event_publisher: IEventPublisher,
        # 現在の感情状態を取得する方法（後で注入）
        get_emotion: callable,
    ) -> None:
        self._text_generator = text_generator
        self._event_publisher = event_publisher
        self._get_emotion = get_emotion

    async def execute(self, request: RespondToChatRequest) -> RespondToChatResponse:
        """チャットに応答を生成"""
        message = request.message
        emotion = self._get_emotion()

        # テキスト生成（出力ポート経由）
        response_text = await self._text_generator.generate_response(
            message=message,
            context=request.context,
            emotion=emotion.primary.value,
        )

        # 優先度計算（ドメインサービス）
        priority = PriorityService.calculate_response_priority(message)

        # ドメインイベント発行
        await self._event_publisher.publish(
            ChatResponseGenerated(
                original_message=message,
                response_text=response_text,
            )
        )

        return RespondToChatResponse(
            response_text=response_text,
            emotion=emotion.primary.value,
            should_interrupt=priority >= SpeechPriority.INTERRUPT,
        )
```

```python
# application/use_cases/generate_commentary.py
"""Generate commentary use case."""

from __future__ import annotations

from dataclasses import dataclass

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.services.priority_service import PriorityService
from ailoveshen.domain.value_objects.speech import SpeechPriority, SpeechRequest


@dataclass
class GenerateCommentaryRequest:
    game_state: GameState
    context: str = ""


@dataclass
class GenerateCommentaryResponse:
    speech_request: SpeechRequest


class GenerateCommentaryUseCase:
    """ゲーム実況生成ユースケース"""

    def __init__(
        self,
        text_generator: ITextGenerator,
        event_publisher: IEventPublisher,
        get_emotion: callable,
    ) -> None:
        self._text_generator = text_generator
        self._event_publisher = event_publisher
        self._get_emotion = get_emotion

    async def execute(
        self,
        request: GenerateCommentaryRequest,
    ) -> GenerateCommentaryResponse:
        """ゲーム実況を生成"""
        game_state = request.game_state
        emotion = self._get_emotion()

        # テキスト生成
        commentary = await self._text_generator.generate_commentary(
            game_state=game_state,
            context=request.context,
            emotion=emotion.primary.value,
        )

        # 優先度計算
        priority = PriorityService.calculate_commentary_priority(game_state)

        # SpeechRequest作成
        speech_request = SpeechRequest(
            text=commentary,
            priority=priority,
            style=self._emotion_to_style(emotion),
            source="commentary",
        )

        return GenerateCommentaryResponse(speech_request=speech_request)

    def _emotion_to_style(self, emotion) -> str:
        """感情をTTSスタイルにマッピング"""
        mapping = {
            "neutral": "Neutral",
            "happy": "Happy",
            "sad": "Sad",
            "angry": "Angry",
            "surprised": "Surprised",
        }
        return mapping.get(emotion.primary.value, "Neutral")
```

```python
# application/use_cases/filter_comment.py
"""Filter comment use case."""

from __future__ import annotations

from dataclasses import dataclass

from ailoveshen.application.ports.output.comment_filter import ICommentFilter
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.domain.entities.chat_message import ChatMessage
from ailoveshen.domain.events.chat_events import CommentFiltered
from ailoveshen.domain.value_objects.filter_result import FilterResult


@dataclass
class FilterCommentRequest:
    message: ChatMessage


@dataclass
class FilterCommentResponse:
    should_respond: bool
    score: float
    reason: str


class FilterCommentUseCase:
    """コメントフィルタリングユースケース"""

    def __init__(
        self,
        comment_filter: ICommentFilter,
        event_publisher: IEventPublisher,
    ) -> None:
        self._comment_filter = comment_filter
        self._event_publisher = event_publisher

    async def execute(self, request: FilterCommentRequest) -> FilterCommentResponse:
        """コメントをフィルタリング"""
        message = request.message

        # 特権ユーザーは常に応答（ドメインルール）
        if message.should_always_respond():
            result = FilterCommentResponse(
                should_respond=True,
                score=1.0,
                reason="Privileged user",
            )
        else:
            # フィルター実行（出力ポート経由）
            filter_result = await self._comment_filter.filter(message)
            result = FilterCommentResponse(
                should_respond=filter_result.should_respond,
                score=filter_result.score,
                reason=filter_result.reason,
            )

        # ドメインイベント発行
        await self._event_publisher.publish(
            CommentFiltered(
                message=message,
                should_respond=result.should_respond,
                score=result.score,
                reason=result.reason,
            )
        )

        return result
```

## 6. インフラストラクチャ層

### 6.1 Geminiアダプター (infrastructure/adapters/gemini/)

```python
# infrastructure/adapters/gemini/text_generator.py
"""Gemini text generator adapter."""

from __future__ import annotations

import google.generativeai as genai

from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities.chat_message import ChatMessage
from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.infrastructure.adapters.gemini.prompts import PromptBuilder


class GeminiTextGenerator(ITextGenerator):
    """
    Gemini APIを使用したテキスト生成アダプター。

    ITextGeneratorインターフェースを実装。
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-pro-preview-05-06",
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model_name)
        self._prompts = prompt_builder or PromptBuilder()

    async def generate_commentary(
        self,
        game_state: GameState,
        context: str,
        emotion: str,
    ) -> str:
        """ゲーム実況を生成"""
        prompt = self._prompts.build_commentary_prompt(
            game_state=self._format_game_state(game_state),
            context=context,
            emotion=emotion,
        )

        response = await self._generate(prompt)
        return response

    async def generate_response(
        self,
        message: ChatMessage,
        context: str,
        emotion: str,
    ) -> str:
        """チャット応答を生成"""
        prompt = self._prompts.build_response_prompt(
            username=message.user.display_name,
            message=message.content,
            context=context,
            emotion=emotion,
        )

        response = await self._generate(prompt)
        return response

    async def _generate(self, prompt: str) -> str:
        """Gemini APIを呼び出し"""
        import asyncio

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._model.generate_content(prompt),
        )
        return response.text.strip() if response.text else ""

    def _format_game_state(self, state: GameState) -> str:
        """ゲーム状態をテキストにフォーマット"""
        return f"""
体力: {state.health}/{state.max_health}
位置: {state.position}
バイオーム: {state.biome}
天候: {state.weather}
危険状態: {"はい" if state.is_in_danger() else "いいえ"}
"""
```

### 6.2 メモリリポジトリ実装 (infrastructure/persistence/)

```python
# infrastructure/persistence/sqlite/memory_repository.py
"""SQLite memory repository implementation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from ailoveshen.domain.entities.memory import LongTermMemory, MemoryQuery
from ailoveshen.domain.repositories.memory_repository import IMemoryRepository


class SqliteMemoryRepository(IMemoryRepository):
    """
    SQLiteを使用したメモリリポジトリ実装。

    IMemoryRepositoryインターフェースを実装。
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """データベース初期化"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details TEXT NOT NULL,
                    context TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.commit()

    async def save(self, memory: LongTermMemory) -> None:
        """メモリを保存"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memories
                (id, type, summary, details, context, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.id,
                    memory.type.value,
                    memory.summary,
                    memory.details,
                    json.dumps(memory.context),
                    memory.created_at.isoformat(),
                ),
            )
            conn.commit()

    async def find_by_id(self, memory_id: str) -> Optional[LongTermMemory]:
        """IDでメモリを検索"""
        # 実装...
        pass

    async def search(self, query: MemoryQuery) -> list[LongTermMemory]:
        """クエリでメモリを検索"""
        # 実装...
        pass

    async def delete(self, memory_id: str) -> bool:
        """メモリを削除"""
        # 実装...
        pass
```

## 7. プレゼンテーション層

### 7.1 イベントハンドラー (presentation/event_handlers/)

```python
# presentation/event_handlers/chat_handler.py
"""Chat event handler."""

from __future__ import annotations

from ailoveshen.application.ports.input.filter_comment import (
    FilterCommentRequest,
    IFilterComment,
)
from ailoveshen.application.ports.input.respond_to_chat import (
    IRespondToChat,
    RespondToChatRequest,
)
from ailoveshen.application.services.speech_queue import SpeechQueueService
from ailoveshen.domain.entities.chat_message import ChatMessage
from ailoveshen.domain.value_objects.speech import SpeechPriority, SpeechRequest


class ChatEventHandler:
    """
    チャットイベントハンドラー。

    ユースケースを組み合わせてチャットメッセージを処理。
    """

    def __init__(
        self,
        filter_use_case: IFilterComment,
        respond_use_case: IRespondToChat,
        speech_queue: SpeechQueueService,
    ) -> None:
        self._filter = filter_use_case
        self._respond = respond_use_case
        self._speech_queue = speech_queue

    async def handle(self, message: ChatMessage) -> None:
        """チャットメッセージを処理"""
        # フィルタリング
        filter_result = await self._filter.execute(
            FilterCommentRequest(message=message)
        )

        if not filter_result.should_respond:
            return

        # 応答生成
        response = await self._respond.execute(
            RespondToChatRequest(message=message)
        )

        # 発話キューに追加
        priority = (
            SpeechPriority.INTERRUPT
            if response.should_interrupt
            else SpeechPriority.NORMAL
        )

        await self._speech_queue.enqueue(
            SpeechRequest(
                text=response.response_text,
                priority=priority,
                source="response",
            )
        )
```

## 8. Composition Root (main.py)

```python
# main.py
"""
Composition Root - 依存関係の組み立て。

すべての具体実装はここで注入される。
"""

from __future__ import annotations

import asyncio

from ailoveshen.application.services.speech_queue import SpeechQueueService
from ailoveshen.application.use_cases.filter_comment import FilterCommentUseCase
from ailoveshen.application.use_cases.generate_commentary import GenerateCommentaryUseCase
from ailoveshen.application.use_cases.respond_to_chat import RespondToChatUseCase
from ailoveshen.infrastructure.adapters.gemini.comment_filter import GeminiCommentFilter
from ailoveshen.infrastructure.adapters.gemini.text_generator import GeminiTextGenerator
from ailoveshen.infrastructure.adapters.tts.speech_synthesizer import StyleBertVits2Synthesizer
from ailoveshen.infrastructure.adapters.twitch.chat_gateway import TwitchChatGateway
from ailoveshen.infrastructure.config.settings import Settings
from ailoveshen.infrastructure.messaging.event_bus import AsyncEventBus
from ailoveshen.infrastructure.persistence.sqlite.memory_repository import SqliteMemoryRepository
from ailoveshen.presentation.event_handlers.chat_handler import ChatEventHandler


class Application:
    """アプリケーションのComposition Root"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        # 状態
        self._emotion_state = EmotionState()

        # --- インフラストラクチャ層の構築 ---
        self._event_bus = AsyncEventBus()

        self._memory_repo = SqliteMemoryRepository(
            settings.mcp.memory.long_term_db
        )

        self._text_generator = GeminiTextGenerator(
            api_key=settings.gemini.api_key,
            model_name=settings.gemini.main_model,
        )

        self._comment_filter = GeminiCommentFilter(
            api_key=settings.gemini.api_key,
            model_name=settings.gemini.filter_model,
        )

        self._speech_synthesizer = StyleBertVits2Synthesizer(
            host=settings.tts.server.host,
            port=settings.tts.server.port,
        )

        self._chat_gateway = TwitchChatGateway(
            channel=settings.twitch.channel,
            client_id=settings.twitch.client_id,
            client_secret=settings.twitch.client_secret,
        )

        # --- アプリケーション層の構築 ---
        self._speech_queue = SpeechQueueService(
            synthesizer=self._speech_synthesizer,
        )

        self._filter_use_case = FilterCommentUseCase(
            comment_filter=self._comment_filter,
            event_publisher=self._event_bus,
        )

        self._respond_use_case = RespondToChatUseCase(
            text_generator=self._text_generator,
            event_publisher=self._event_bus,
            get_emotion=lambda: self._emotion_state,
        )

        self._commentary_use_case = GenerateCommentaryUseCase(
            text_generator=self._text_generator,
            event_publisher=self._event_bus,
            get_emotion=lambda: self._emotion_state,
        )

        # --- プレゼンテーション層の構築 ---
        self._chat_handler = ChatEventHandler(
            filter_use_case=self._filter_use_case,
            respond_use_case=self._respond_use_case,
            speech_queue=self._speech_queue,
        )

    async def start(self) -> None:
        """アプリケーション開始"""
        # チャットゲートウェイにハンドラーを登録
        await self._chat_gateway.subscribe(self._chat_handler.handle)
        await self._chat_gateway.connect()

        # 発話キュー開始
        await self._speech_queue.start()

    async def stop(self) -> None:
        """アプリケーション停止"""
        await self._speech_queue.stop()
        await self._chat_gateway.disconnect()


def main() -> None:
    """エントリーポイント"""
    from ailoveshen.infrastructure.config.loader import load_settings

    settings = load_settings()
    app = Application(settings)

    loop = asyncio.new_event_loop()

    try:
        loop.run_until_complete(app.start())
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(app.stop())
        loop.close()


if __name__ == "__main__":
    main()
```

## 9. テスト戦略

### ドメイン層のテスト（純粋なユニットテスト）
```python
# tests/unit/domain/test_emotion_service.py
def test_calculate_from_game_event_damage():
    """ダメージを受けたらSCAREDになる"""
    current = EmotionState(EmotionType.NEUTRAL, 0.5)
    result = EmotionService.calculate_from_game_event(current, "damage_taken")
    assert result.primary == EmotionType.SCARED
```

### アプリケーション層のテスト（モック使用）
```python
# tests/unit/application/test_respond_to_chat.py
@pytest.mark.asyncio
async def test_respond_generates_text():
    """応答が生成される"""
    # モック
    mock_generator = Mock(spec=ITextGenerator)
    mock_generator.generate_response.return_value = "こんにちは！"
    mock_publisher = Mock(spec=IEventPublisher)

    # ユースケース
    use_case = RespondToChatUseCase(
        text_generator=mock_generator,
        event_publisher=mock_publisher,
        get_emotion=lambda: EmotionState(),
    )

    # 実行
    result = await use_case.execute(RespondToChatRequest(
        message=ChatMessage.create(ChatUser(...), "hello")
    ))

    assert result.response_text == "こんにちは！"
    mock_generator.generate_response.assert_called_once()
```

## 10. 修正による利点

| 観点 | Before | After |
|------|--------|-------|
| 依存方向 | 上位→下位（直接） | 上位→抽象←下位（逆転） |
| テスト容易性 | モック困難 | インターフェース経由でモック容易 |
| 変更影響 | Gemini変更→全体に影響 | Gemini変更→アダプターのみ |
| ビジネスロジック | 散在 | ドメイン層に集約 |
| 責務 | 不明確 | レイヤーごとに明確 |
