# Phase 4: Twitch Integration 詳細設計書 (Issue #1, #7)

## 1. 概要

Twitchチャット連携とGemini Flashによるコメントフィルタリングを実装します。

### 要件
- **Issue #1**: OAuth認証、IRC接続、コメント取得
- **Issue #7**: Gemini Flashフィルター、動的閾値調整

## 2. クリーンアーキテクチャに基づくコンポーネント構成

```
src/ailoveshen/
├── domain/
│   ├── entities/
│   │   └── chat_message.py         # ChatMessage aggregate (Phase 1から)
│   ├── value_objects/
│   │   ├── filter_result.py        # FilterResult
│   │   └── volume_metrics.py       # VolumeMetrics
│   └── services/
│       └── filtering_policy_service.py  # FilteringPolicyService
│
├── application/
│   ├── ports/
│   │   ├── input/
│   │   │   ├── filter_comment.py       # IFilterComment
│   │   │   └── process_chat_message.py # IProcessChatMessage
│   │   └── output/
│   │       ├── chat_provider.py        # IChatProvider
│   │       ├── comment_analyzer.py     # ICommentAnalyzer
│   │       └── message_repository.py   # IMessageRepository
│   ├── use_cases/
│   │   ├── filter_comment.py       # FilterCommentUseCase
│   │   └── process_chat_message.py # ProcessChatMessageUseCase
│   └── dto/
│       └── chat_dto.py             # Request/Response DTOs
│
├── infrastructure/
│   └── adapters/
│       ├── twitch/
│       │   ├── twitch_chat_adapter.py  # TwitchChatAdapter
│       │   └── twitch_auth.py          # TwitchAuth
│       └── gemini/
│           └── gemini_comment_analyzer.py  # GeminiCommentAnalyzer
│
└── presentation/
    └── services/
        └── twitch_service.py       # TwitchService (coordinates)
```

## 3. Domain Layer

### 3.1 Value Objects

#### FilterResult (domain/value_objects/filter_result.py)

```python
"""Filter result value object."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FilterDecision(str, Enum):
    """Filter decision types."""
    RESPOND = "respond"
    SKIP = "skip"
    PRIORITY = "priority"  # High priority (broadcaster, etc.)


@dataclass(frozen=True)
class FilterResult:
    """
    Immutable value object representing comment filter result.
    """
    decision: FilterDecision
    score: float  # 0.0 - 1.0
    reason: str
    threshold_used: float

    @classmethod
    def respond(cls, score: float, reason: str, threshold: float) -> FilterResult:
        """Create a respond result."""
        return cls(
            decision=FilterDecision.RESPOND,
            score=score,
            reason=reason,
            threshold_used=threshold,
        )

    @classmethod
    def skip(cls, score: float, reason: str, threshold: float) -> FilterResult:
        """Create a skip result."""
        return cls(
            decision=FilterDecision.SKIP,
            score=score,
            reason=reason,
            threshold_used=threshold,
        )

    @classmethod
    def priority(cls, reason: str) -> FilterResult:
        """Create a priority (always respond) result."""
        return cls(
            decision=FilterDecision.PRIORITY,
            score=1.0,
            reason=reason,
            threshold_used=0.0,
        )

    def should_respond(self) -> bool:
        """Check if we should respond based on this result."""
        return self.decision in (FilterDecision.RESPOND, FilterDecision.PRIORITY)
```

#### VolumeMetrics (domain/value_objects/volume_metrics.py)

```python
"""Volume metrics value object."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VolumeLevel(str, Enum):
    """Comment volume levels."""
    LOW = "low"       # Few comments
    NORMAL = "normal" # Normal activity
    HIGH = "high"     # Many comments


@dataclass(frozen=True)
class VolumeMetrics:
    """
    Immutable value object representing comment volume metrics.
    """
    messages_per_minute: float
    window_seconds: int
    level: VolumeLevel

    @classmethod
    def from_message_count(
        cls,
        count: int,
        window_seconds: int,
        low_threshold: float,
        high_threshold: float,
    ) -> VolumeMetrics:
        """Create from message count in a time window."""
        mpm = count * (60 / window_seconds)

        if mpm <= low_threshold:
            level = VolumeLevel.LOW
        elif mpm >= high_threshold:
            level = VolumeLevel.HIGH
        else:
            level = VolumeLevel.NORMAL

        return cls(
            messages_per_minute=mpm,
            window_seconds=window_seconds,
            level=level,
        )

    def get_japanese_description(self) -> str:
        """Get volume level in Japanese."""
        mapping = {
            VolumeLevel.LOW: "少ない",
            VolumeLevel.NORMAL: "普通",
            VolumeLevel.HIGH: "多い",
        }
        return mapping[self.level]
```

### 3.2 Domain Services

#### FilteringPolicyService (domain/services/filtering_policy_service.py)

```python
"""Filtering policy domain service."""

from __future__ import annotations

from ailoveshen.domain.entities.chat_message import ChatMessage
from ailoveshen.domain.value_objects.filter_result import FilterDecision, FilterResult
from ailoveshen.domain.value_objects.volume_metrics import VolumeLevel, VolumeMetrics


class FilteringPolicyService:
    """
    Domain service for comment filtering policy.

    Encapsulates the business rules for:
    - Dynamic threshold calculation
    - Quick filter checks (privileged users, etc.)
    - Final decision based on score and threshold
    """

    def __init__(
        self,
        base_threshold: float = 0.5,
        min_threshold: float = 0.3,
        max_threshold: float = 0.9,
        low_volume_mpm: float = 5.0,
        high_volume_mpm: float = 30.0,
    ) -> None:
        self._base_threshold = base_threshold
        self._min_threshold = min_threshold
        self._max_threshold = max_threshold
        self._low_volume_mpm = low_volume_mpm
        self._high_volume_mpm = high_volume_mpm

    def calculate_threshold(self, volume: VolumeMetrics) -> float:
        """
        Calculate dynamic threshold based on volume.

        Lower threshold when volume is low (respond more).
        Higher threshold when volume is high (be selective).
        """
        mpm = volume.messages_per_minute

        if mpm <= self._low_volume_mpm:
            return self._min_threshold
        elif mpm >= self._high_volume_mpm:
            return self._max_threshold
        else:
            # Linear interpolation
            ratio = (mpm - self._low_volume_mpm) / (
                self._high_volume_mpm - self._low_volume_mpm
            )
            return self._min_threshold + ratio * (
                self._max_threshold - self._min_threshold
            )

    def check_quick_filter(self, message: ChatMessage) -> FilterResult | None:
        """
        Apply quick filter rules that don't need LLM analysis.

        Returns FilterResult if decision can be made, None otherwise.
        """
        # Always respond to privileged users
        if message.is_from_privileged_user():
            return FilterResult.priority("Privileged user (broadcaster/mod)")

        # Skip very short messages
        if len(message.content.strip()) < 2:
            return FilterResult.skip(0.0, "Message too short", 0.0)

        # No quick decision - needs LLM analysis
        return None

    def make_decision(
        self,
        score: float,
        reason: str,
        threshold: float,
    ) -> FilterResult:
        """
        Make final filter decision based on score and threshold.
        """
        if score >= threshold:
            return FilterResult.respond(score, reason, threshold)
        else:
            return FilterResult.skip(score, reason, threshold)
```

## 4. Application Layer

### 4.1 Output Ports

#### IChatProvider (application/ports/output/chat_provider.py)

```python
"""Chat provider output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ailoveshen.domain.entities.chat_message import ChatMessage


class IChatProvider(ABC):
    """
    Output port for chat platform connection.

    Abstracts Twitch IRC or other chat platforms.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Connect to chat platform."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from chat platform."""
        ...

    @abstractmethod
    async def send_message(self, message: str) -> None:
        """Send a message to chat."""
        ...

    @abstractmethod
    def subscribe(
        self,
        callback: Callable[[ChatMessage], None],
    ) -> None:
        """Subscribe to incoming messages."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected."""
        ...
```

#### ICommentAnalyzer (application/ports/output/comment_analyzer.py)

```python
"""Comment analyzer output port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.domain.value_objects.volume_metrics import VolumeMetrics


class AnalysisResult:
    """Result from comment analysis."""

    def __init__(
        self,
        should_respond: bool,
        score: float,
        reason: str,
    ) -> None:
        self.should_respond = should_respond
        self.score = score
        self.reason = reason


class ICommentAnalyzer(ABC):
    """
    Output port for comment analysis.

    Abstracts LLM-based comment analysis.
    """

    @abstractmethod
    async def analyze(
        self,
        username: str,
        message: str,
        volume: VolumeMetrics,
    ) -> AnalysisResult:
        """
        Analyze a comment to determine if it needs response.

        Args:
            username: User who sent the message
            message: Message content
            volume: Current volume metrics

        Returns:
            Analysis result with score and reason
        """
        ...

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the analyzer."""
        ...
```

### 4.2 Input Ports

#### IFilterComment (application/ports/input/filter_comment.py)

```python
"""Filter comment input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.chat_dto import (
    FilterCommentRequest,
    FilterCommentResponse,
)


class IFilterComment(ABC):
    """Input port for comment filtering use case."""

    @abstractmethod
    async def execute(
        self,
        request: FilterCommentRequest,
    ) -> FilterCommentResponse:
        """Execute comment filtering."""
        ...
```

#### IProcessChatMessage (application/ports/input/process_chat_message.py)

```python
"""Process chat message input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.chat_dto import (
    ProcessChatMessageRequest,
    ProcessChatMessageResponse,
)


class IProcessChatMessage(ABC):
    """Input port for processing incoming chat messages."""

    @abstractmethod
    async def execute(
        self,
        request: ProcessChatMessageRequest,
    ) -> ProcessChatMessageResponse:
        """Execute chat message processing."""
        ...
```

### 4.3 DTOs

#### Chat DTOs (application/dto/chat_dto.py)

```python
"""Chat-related DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.entities.chat_message import ChatMessage
from ailoveshen.domain.value_objects.filter_result import FilterDecision


@dataclass
class FilterCommentRequest:
    """Input DTO for comment filtering."""
    message: ChatMessage


@dataclass
class FilterCommentResponse:
    """Output DTO for comment filtering."""
    should_respond: bool
    score: float
    reason: str
    decision: FilterDecision
    threshold_used: float


@dataclass
class ProcessChatMessageRequest:
    """Input DTO for chat message processing."""
    message_id: str
    user_id: str
    user_name: str
    display_name: str
    content: str
    is_broadcaster: bool = False
    is_moderator: bool = False
    is_subscriber: bool = False


@dataclass
class ProcessChatMessageResponse:
    """Output DTO for chat message processing."""
    message: ChatMessage
    filter_result: Optional[FilterCommentResponse] = None
    should_generate_response: bool = False
```

### 4.4 Use Cases

#### FilterCommentUseCase (application/use_cases/filter_comment.py)

```python
"""Filter comment use case."""

from __future__ import annotations

import time
from collections import deque

from loguru import logger

from ailoveshen.application.dto.chat_dto import (
    FilterCommentRequest,
    FilterCommentResponse,
)
from ailoveshen.application.ports.input.filter_comment import IFilterComment
from ailoveshen.application.ports.output.comment_analyzer import ICommentAnalyzer
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.domain.events import CommentFilteredEvent
from ailoveshen.domain.services.filtering_policy_service import FilteringPolicyService
from ailoveshen.domain.value_objects.volume_metrics import VolumeMetrics


class FilterCommentUseCase(IFilterComment):
    """
    Use case for filtering comments.

    Coordinates:
    - Volume tracking
    - Quick filter checks
    - LLM analysis for complex decisions
    - Dynamic threshold application
    """

    def __init__(
        self,
        comment_analyzer: ICommentAnalyzer,
        event_publisher: IEventPublisher,
        filtering_policy: FilteringPolicyService,
        window_seconds: int = 60,
        low_volume_mpm: float = 5.0,
        high_volume_mpm: float = 30.0,
    ) -> None:
        self._analyzer = comment_analyzer
        self._event_publisher = event_publisher
        self._policy = filtering_policy
        self._window_seconds = window_seconds
        self._low_volume_mpm = low_volume_mpm
        self._high_volume_mpm = high_volume_mpm

        # Volume tracking
        self._message_times: deque[float] = deque(maxlen=1000)

    async def execute(
        self,
        request: FilterCommentRequest,
    ) -> FilterCommentResponse:
        """Execute comment filtering."""
        message = request.message

        # Track message time for volume calculation
        self._message_times.append(time.time())

        # Calculate current volume
        volume = self._calculate_volume()

        # Quick filter check
        quick_result = self._policy.check_quick_filter(message)
        if quick_result is not None:
            await self._publish_event(message.id, quick_result)
            return FilterCommentResponse(
                should_respond=quick_result.should_respond(),
                score=quick_result.score,
                reason=quick_result.reason,
                decision=quick_result.decision,
                threshold_used=quick_result.threshold_used,
            )

        # Calculate dynamic threshold
        threshold = self._policy.calculate_threshold(volume)

        try:
            # Analyze with LLM
            analysis = await self._analyzer.analyze(
                username=message.user.display_name,
                message=message.content,
                volume=volume,
            )

            # Make decision based on threshold
            result = self._policy.make_decision(
                score=analysis.score,
                reason=analysis.reason,
                threshold=threshold,
            )

            await self._publish_event(message.id, result)

            return FilterCommentResponse(
                should_respond=result.should_respond(),
                score=result.score,
                reason=result.reason,
                decision=result.decision,
                threshold_used=result.threshold_used,
            )

        except Exception as e:
            logger.error(f"Filter analysis error: {e}")
            # Default to respond on error
            return FilterCommentResponse(
                should_respond=True,
                score=0.5,
                reason=f"Analysis error: {e}",
                decision=FilterDecision.RESPOND,
                threshold_used=threshold,
            )

    def _calculate_volume(self) -> VolumeMetrics:
        """Calculate current volume metrics."""
        now = time.time()
        window_start = now - self._window_seconds
        recent_count = sum(1 for t in self._message_times if t >= window_start)

        return VolumeMetrics.from_message_count(
            count=recent_count,
            window_seconds=self._window_seconds,
            low_threshold=self._low_volume_mpm,
            high_threshold=self._high_volume_mpm,
        )

    async def _publish_event(self, message_id: str, result) -> None:
        """Publish filter event."""
        await self._event_publisher.publish(
            CommentFilteredEvent(
                message_id=message_id,
                should_respond=result.should_respond(),
                score=result.score,
                reason=result.reason,
                threshold=result.threshold_used,
            )
        )
```

#### ProcessChatMessageUseCase (application/use_cases/process_chat_message.py)

```python
"""Process chat message use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.chat_dto import (
    FilterCommentRequest,
    FilterCommentResponse,
    ProcessChatMessageRequest,
    ProcessChatMessageResponse,
)
from ailoveshen.application.ports.input.filter_comment import IFilterComment
from ailoveshen.application.ports.input.process_chat_message import IProcessChatMessage
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.domain.entities.chat_message import ChatMessage, ChatUser
from ailoveshen.domain.events import ChatMessageReceivedEvent


class ProcessChatMessageUseCase(IProcessChatMessage):
    """
    Use case for processing incoming chat messages.

    Coordinates:
    - Message entity creation
    - Event publishing
    - Filter integration
    """

    def __init__(
        self,
        filter_use_case: IFilterComment,
        event_publisher: IEventPublisher,
        auto_filter: bool = True,
    ) -> None:
        self._filter = filter_use_case
        self._event_publisher = event_publisher
        self._auto_filter = auto_filter

    async def execute(
        self,
        request: ProcessChatMessageRequest,
    ) -> ProcessChatMessageResponse:
        """Execute chat message processing."""
        # Create domain entity
        message = ChatMessage(
            id=request.message_id,
            user=ChatUser(
                id=request.user_id,
                name=request.user_name,
                display_name=request.display_name,
                is_broadcaster=request.is_broadcaster,
                is_moderator=request.is_moderator,
                is_subscriber=request.is_subscriber,
            ),
            content=request.content,
        )

        # Publish received event
        await self._event_publisher.publish(
            ChatMessageReceivedEvent(
                message_id=message.id,
                user_name=message.user.name,
                content=message.content,
            )
        )

        # Auto-filter if enabled
        filter_result: FilterCommentResponse | None = None
        should_respond = False

        if self._auto_filter:
            filter_result = await self._filter.execute(
                FilterCommentRequest(message=message)
            )
            should_respond = filter_result.should_respond

        return ProcessChatMessageResponse(
            message=message,
            filter_result=filter_result,
            should_generate_response=should_respond,
        )
```

## 5. Infrastructure Layer

### 5.1 Twitch Chat Adapter

#### TwitchChatAdapter (infrastructure/adapters/twitch/twitch_chat_adapter.py)

```python
"""Twitch chat adapter."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from loguru import logger
from twitchio import Message
from twitchio.ext import commands

from ailoveshen.application.ports.output.chat_provider import IChatProvider
from ailoveshen.core.exceptions import ChatConnectionError
from ailoveshen.domain.entities.chat_message import ChatMessage, ChatUser
from ailoveshen.infrastructure.adapters.twitch.twitch_auth import TwitchAuth


class TwitchChatAdapter(IChatProvider):
    """
    Infrastructure adapter for Twitch chat.

    Implements IChatProvider output port using TwitchIO.
    """

    def __init__(
        self,
        channel: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._channel = channel
        self._auth = TwitchAuth(client_id, client_secret)
        self._bot: Optional[_TwitchBot] = None
        self._callbacks: list[Callable[[ChatMessage], None]] = []
        self._connected = False

    async def connect(self) -> None:
        """Connect to Twitch chat."""
        try:
            token = await self._auth.get_access_token()

            self._bot = _TwitchBot(
                token=token,
                channel=self._channel,
                on_message=self._handle_message,
            )

            asyncio.create_task(self._bot.start())

            # Wait for connection
            for _ in range(30):
                if self._bot.is_ready:
                    break
                await asyncio.sleep(1)
            else:
                raise ChatConnectionError("Connection timeout")

            self._connected = True
            logger.info(f"Connected to Twitch channel: {self._channel}")

        except Exception as e:
            raise ChatConnectionError(f"Failed to connect: {e}")

    async def disconnect(self) -> None:
        """Disconnect from Twitch chat."""
        if self._bot:
            await self._bot.close()
            self._bot = None

        self._connected = False
        logger.info("Disconnected from Twitch")

    async def send_message(self, message: str) -> None:
        """Send a message to chat."""
        if not self._bot or not self._connected:
            logger.warning("Cannot send: not connected")
            return

        channel = self._bot.get_channel(self._channel)
        if channel:
            await channel.send(message)

    def subscribe(
        self,
        callback: Callable[[ChatMessage], None],
    ) -> None:
        """Subscribe to incoming messages."""
        self._callbacks.append(callback)

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    async def _handle_message(self, twitchio_msg: Message) -> None:
        """Convert TwitchIO message to domain entity and notify callbacks."""
        message = ChatMessage(
            id=twitchio_msg.id or str(hash(twitchio_msg.content)),
            user=ChatUser(
                id=str(twitchio_msg.author.id) if twitchio_msg.author else "unknown",
                name=twitchio_msg.author.name if twitchio_msg.author else "unknown",
                display_name=twitchio_msg.author.display_name if twitchio_msg.author else "Unknown",
                is_broadcaster=twitchio_msg.author.is_broadcaster if twitchio_msg.author else False,
                is_moderator=twitchio_msg.author.is_mod if twitchio_msg.author else False,
                is_subscriber=twitchio_msg.author.is_subscriber if twitchio_msg.author else False,
            ),
            content=twitchio_msg.content,
        )

        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(message)
                else:
                    callback(message)
            except Exception as e:
                logger.error(f"Message callback error: {e}")


class _TwitchBot(commands.Bot):
    """Internal TwitchIO bot wrapper."""

    def __init__(
        self,
        token: str,
        channel: str,
        on_message: Callable,
    ) -> None:
        super().__init__(
            token=token,
            prefix="!",
            initial_channels=[channel],
        )
        self._on_message = on_message
        self.is_ready = False

    async def event_ready(self) -> None:
        logger.info(f"TwitchIO ready as {self.nick}")
        self.is_ready = True

    async def event_message(self, message: Message) -> None:
        if message.echo:
            return
        await self._on_message(message)
```

#### TwitchAuth (infrastructure/adapters/twitch/twitch_auth.py)

```python
"""Twitch OAuth authentication."""

from __future__ import annotations

import time
from typing import Optional

import httpx
from loguru import logger

from ailoveshen.core.exceptions import AuthenticationError


class TwitchAuth:
    """
    Twitch OAuth token management.

    Uses Client Credentials flow.
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token: Optional[str] = None
        self._expires_at: float = 0

    async def get_access_token(self) -> str:
        """Get valid access token, refreshing if needed."""
        if self._is_valid():
            return self._access_token  # type: ignore

        await self._refresh()
        return self._access_token  # type: ignore

    def _is_valid(self) -> bool:
        """Check if current token is valid."""
        if not self._access_token:
            return False
        return time.time() < self._expires_at - 60

    async def _refresh(self) -> None:
        """Get new access token."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://id.twitch.tv/oauth2/token",
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "grant_type": "client_credentials",
                    },
                )
                response.raise_for_status()

                data = response.json()
                self._access_token = data["access_token"]
                self._expires_at = time.time() + data["expires_in"]

                logger.info("Twitch access token refreshed")

            except httpx.HTTPStatusError as e:
                raise AuthenticationError(f"Token refresh failed: {e.response.status_code}")
            except Exception as e:
                raise AuthenticationError(f"Token refresh error: {e}")
```

### 5.2 Gemini Comment Analyzer

#### GeminiCommentAnalyzer (infrastructure/adapters/gemini/gemini_comment_analyzer.py)

```python
"""Gemini-based comment analyzer adapter."""

from __future__ import annotations

import asyncio
import json
from string import Template
from typing import Optional

import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from loguru import logger

from ailoveshen.application.ports.output.comment_analyzer import (
    AnalysisResult,
    ICommentAnalyzer,
)
from ailoveshen.core.exceptions import AnalysisError
from ailoveshen.domain.value_objects.volume_metrics import VolumeMetrics


FILTER_PROMPT_TEMPLATE = Template("""
以下のコメントについて、AI配信者が反応すべきかどうかを判断してください。

## コメント
ユーザー: $username
内容: $message

## 現在のコメント流量
$volume_level

## 判断基準
反応すべき:
- 質問している
- 挨拶や応援
- 配信内容に関連した面白いコメント
- 初見や常連からのコメント

反応不要:
- スパムや連投
- 意味不明な内容
- 不適切な内容
- 他の視聴者同士の会話

## 出力形式
以下のJSON形式で出力してください：
{"respond": true/false, "score": 0.0-1.0, "reason": "理由"}
""")


class GeminiCommentAnalyzer(ICommentAnalyzer):
    """
    Infrastructure adapter for Gemini Flash comment analysis.

    Implements ICommentAnalyzer output port.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.0-flash",
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._model: Optional[genai.GenerativeModel] = None

    async def initialize(self) -> None:
        """Initialize Gemini model."""
        genai.configure(api_key=self._api_key)
        self._model = genai.GenerativeModel(
            model_name=self._model_name,
            generation_config=GenerationConfig(
                temperature=0.1,  # Low temperature for consistent filtering
                max_output_tokens=100,
            ),
        )
        logger.info(f"Comment analyzer initialized: {self._model_name}")

    async def analyze(
        self,
        username: str,
        message: str,
        volume: VolumeMetrics,
    ) -> AnalysisResult:
        """Analyze comment using Gemini Flash."""
        if self._model is None:
            raise AnalysisError("Analyzer not initialized")

        prompt = FILTER_PROMPT_TEMPLATE.substitute(
            username=username,
            message=message,
            volume_level=volume.get_japanese_description(),
        )

        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._model.generate_content(prompt),
            )

            if not response.text:
                return AnalysisResult(
                    should_respond=True,
                    score=0.5,
                    reason="Empty response",
                )

            return self._parse_response(response.text)

        except Exception as e:
            raise AnalysisError(f"Analysis failed: {e}")

    def _parse_response(self, response: str) -> AnalysisResult:
        """Parse Gemini's JSON response."""
        try:
            start = response.find("{")
            end = response.rfind("}") + 1

            if start >= 0 and end > start:
                json_str = response[start:end]
                data = json.loads(json_str)

                return AnalysisResult(
                    should_respond=bool(data.get("respond", True)),
                    score=float(data.get("score", 0.5)),
                    reason=str(data.get("reason", "")),
                )

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse response: {e}")

        return AnalysisResult(
            should_respond=True,
            score=0.5,
            reason="Parse error - defaulting to respond",
        )
```

## 6. Presentation Layer

### 6.1 Twitch Service

#### TwitchService (presentation/services/twitch_service.py)

```python
"""Twitch service for presentation layer."""

from __future__ import annotations

from typing import Callable, Optional

from loguru import logger

from ailoveshen.application.dto.chat_dto import ProcessChatMessageRequest
from ailoveshen.application.ports.input.process_chat_message import IProcessChatMessage
from ailoveshen.application.ports.output.chat_provider import IChatProvider
from ailoveshen.domain.entities.chat_message import ChatMessage


class TwitchService:
    """
    Presentation layer service for Twitch integration.

    Coordinates chat provider and message processing.
    """

    def __init__(
        self,
        chat_provider: IChatProvider,
        process_message_use_case: IProcessChatMessage,
    ) -> None:
        self._chat = chat_provider
        self._process = process_message_use_case
        self._response_handlers: list[Callable] = []

    async def start(self) -> None:
        """Start Twitch service."""
        await self._chat.connect()
        self._chat.subscribe(self._on_message)
        logger.info("Twitch service started")

    async def stop(self) -> None:
        """Stop Twitch service."""
        await self._chat.disconnect()
        logger.info("Twitch service stopped")

    def on_should_respond(self, handler: Callable) -> None:
        """Register handler for messages that need response."""
        self._response_handlers.append(handler)

    async def send_message(self, text: str) -> None:
        """Send message to chat."""
        await self._chat.send_message(text)

    async def _on_message(self, message: ChatMessage) -> None:
        """Handle incoming message."""
        request = ProcessChatMessageRequest(
            message_id=message.id,
            user_id=message.user.id,
            user_name=message.user.name,
            display_name=message.user.display_name,
            content=message.content,
            is_broadcaster=message.user.is_broadcaster,
            is_moderator=message.user.is_moderator,
            is_subscriber=message.user.is_subscriber,
        )

        result = await self._process.execute(request)

        if result.should_generate_response:
            for handler in self._response_handlers:
                try:
                    await handler(result.message)
                except Exception as e:
                    logger.error(f"Response handler error: {e}")

    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._chat.is_connected()
```

## 7. Composition Root (Twitch部分)

```python
# src/ailoveshen/main.py (Twitch部分の抜粋)

from ailoveshen.application.use_cases.filter_comment import FilterCommentUseCase
from ailoveshen.application.use_cases.process_chat_message import ProcessChatMessageUseCase
from ailoveshen.domain.services.filtering_policy_service import FilteringPolicyService
from ailoveshen.infrastructure.adapters.gemini.gemini_comment_analyzer import GeminiCommentAnalyzer
from ailoveshen.infrastructure.adapters.twitch.twitch_chat_adapter import TwitchChatAdapter
from ailoveshen.presentation.services.twitch_service import TwitchService


def create_twitch_service(
    config: TwitchConfig,
    filter_config: FilterConfig,
    gemini_config: GeminiConfig,
    event_publisher: IEventPublisher,
) -> TwitchService:
    """Create Twitch service with all dependencies."""

    # Infrastructure adapters
    chat_adapter = TwitchChatAdapter(
        channel=config.channel,
        client_id=config.client_id,
        client_secret=config.client_secret,
    )

    comment_analyzer = GeminiCommentAnalyzer(
        api_key=gemini_config.api_key,
        model_name=gemini_config.filter_model,
    )

    # Domain service
    filtering_policy = FilteringPolicyService(
        base_threshold=filter_config.base_threshold,
        min_threshold=filter_config.min_threshold,
        max_threshold=filter_config.max_threshold,
        low_volume_mpm=filter_config.low_volume_threshold,
        high_volume_mpm=filter_config.high_volume_threshold,
    )

    # Use cases
    filter_use_case = FilterCommentUseCase(
        comment_analyzer=comment_analyzer,
        event_publisher=event_publisher,
        filtering_policy=filtering_policy,
        window_seconds=filter_config.window_seconds,
    )

    process_use_case = ProcessChatMessageUseCase(
        filter_use_case=filter_use_case,
        event_publisher=event_publisher,
        auto_filter=True,
    )

    # Presentation service
    return TwitchService(
        chat_provider=chat_adapter,
        process_message_use_case=process_use_case,
    )
```

## 8. データフロー図（Clean Architecture版）

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PRESENTATION LAYER                               │
│  ┌───────────────┐                                                       │
│  │ TwitchService │  ← on_message callback from chat provider             │
│  └───────┬───────┘                                                       │
└──────────┼───────────────────────────────────────────────────────────────┘
           │ ProcessChatMessageRequest
           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         APPLICATION LAYER                                │
│  ┌────────────────────────┐    ┌────────────────────────┐                │
│  │IProcessChatMessage     │    │IFilterComment          │ ← Input Ports  │
│  └───────────┬────────────┘    └───────────┬────────────┘                │
│              │                             │                             │
│  ┌───────────▼────────────┐    ┌───────────▼────────────┐                │
│  │ProcessChatMessageUC    │───▶│FilterCommentUseCase    │                │
│  │  - event_publisher     │    │  - comment_analyzer    │→ ICommentAnalyzer
│  └────────────────────────┘    │  - filtering_policy    │                │
│                                └────────────────────────┘                │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                            DOMAIN LAYER                                  │
│  ┌─────────────────────┐  ┌────────────────────┐  ┌──────────────────┐   │
│  │FilteringPolicyService│  │   FilterResult    │  │  VolumeMetrics   │   │
│  │  (Domain Service)   │  │  (Value Object)   │  │ (Value Object)   │   │
│  └─────────────────────┘  └────────────────────┘  └──────────────────┘   │
│  ┌─────────────────────┐                                                 │
│  │   ChatMessage       │                                                 │
│  │    (Entity)         │                                                 │
│  └─────────────────────┘                                                 │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                       INFRASTRUCTURE LAYER                               │
│  ┌───────────────────────┐    ┌────────────────────────┐                 │
│  │  TwitchChatAdapter    │    │GeminiCommentAnalyzer   │                 │
│  │  (implements          │    │(implements             │                 │
│  │   IChatProvider)      │    │ ICommentAnalyzer)      │                 │
│  └───────────┬───────────┘    └───────────┬────────────┘                 │
│              │                            │                              │
│              ▼                            ▼                              │
│    [Twitch IRC/TwitchIO]        [Google Gemini Flash]                    │
└─────────────────────────────────────────────────────────────────────────┘
```

## 9. 動的閾値の動作

| コメント数/分 | 閾値 | 動作 |
|--------------|------|------|
| 0-5 | 0.30 | ほぼ全てに反応 |
| 5-15 | 0.30-0.60 | 多くに反応 |
| 15-30 | 0.60-0.90 | 選択的に反応 |
| 30+ | 0.90 | 重要なものだけ |

## 10. 設定

```yaml
# config/default.yaml
twitch:
  enabled: true
  channel: "${TWITCH_CHANNEL}"
  client_id: "${TWITCH_CLIENT_ID}"
  client_secret: "${TWITCH_CLIENT_SECRET}"

comment_filter:
  enabled: true
  base_threshold: 0.5
  min_threshold: 0.3
  max_threshold: 0.9
  window_seconds: 60
  low_volume_threshold: 5
  high_volume_threshold: 30
```

## 11. テスト計画

### 11.1 Unit Tests

```python
# tests/unit/domain/test_filtering_policy.py
import pytest
from ailoveshen.domain.services.filtering_policy_service import FilteringPolicyService
from ailoveshen.domain.value_objects.volume_metrics import VolumeLevel, VolumeMetrics


def test_threshold_low_volume():
    """Test threshold is low when volume is low."""
    policy = FilteringPolicyService(
        min_threshold=0.3,
        max_threshold=0.9,
    )
    volume = VolumeMetrics(
        messages_per_minute=3.0,
        window_seconds=60,
        level=VolumeLevel.LOW,
    )

    threshold = policy.calculate_threshold(volume)
    assert threshold == 0.3


def test_threshold_high_volume():
    """Test threshold is high when volume is high."""
    policy = FilteringPolicyService(
        min_threshold=0.3,
        max_threshold=0.9,
    )
    volume = VolumeMetrics(
        messages_per_minute=50.0,
        window_seconds=60,
        level=VolumeLevel.HIGH,
    )

    threshold = policy.calculate_threshold(volume)
    assert threshold == 0.9
```

### 11.2 Integration Tests

```python
# tests/integration/test_filter_use_case.py
import pytest
from unittest.mock import AsyncMock

from ailoveshen.application.dto.chat_dto import FilterCommentRequest
from ailoveshen.application.ports.output.comment_analyzer import AnalysisResult
from ailoveshen.application.use_cases.filter_comment import FilterCommentUseCase
from ailoveshen.domain.entities.chat_message import ChatMessage, ChatUser
from ailoveshen.domain.services.filtering_policy_service import FilteringPolicyService


@pytest.fixture
def mock_analyzer():
    analyzer = AsyncMock()
    analyzer.analyze.return_value = AnalysisResult(
        should_respond=True,
        score=0.8,
        reason="Good comment",
    )
    return analyzer


@pytest.mark.asyncio
async def test_filter_passes_high_score(mock_analyzer):
    """Test that high score passes filter."""
    use_case = FilterCommentUseCase(
        comment_analyzer=mock_analyzer,
        event_publisher=AsyncMock(),
        filtering_policy=FilteringPolicyService(),
    )

    message = ChatMessage(
        id="123",
        user=ChatUser(id="u1", name="user", display_name="User"),
        content="Hello!",
    )

    response = await use_case.execute(FilterCommentRequest(message=message))

    assert response.should_respond
    assert response.score == 0.8
```
