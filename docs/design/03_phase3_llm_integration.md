# Phase 3: LLM Integration 詳細設計書 (Issue #2)

## 1. 概要

Gemini 2.5を使用した会話システムを実装します。

### 要件（Issue #2より）
- Gemini 2.5 APIとの接続
- ゲーム実況生成（メインループ）
- コメント応答生成（サブループ/割り込み）

## 2. クリーンアーキテクチャに基づくコンポーネント構成

```
src/ailoveshen/
├── domain/
│   ├── entities/
│   │   └── conversation.py         # Conversation aggregate
│   ├── value_objects/
│   │   ├── conversation_message.py # ConversationMessage
│   │   ├── character_profile.py    # CharacterProfile
│   │   └── generation_context.py   # GenerationContext
│   └── services/
│       └── conversation_service.py # ConversationService
│
├── application/
│   ├── ports/
│   │   ├── input/
│   │   │   ├── generate_commentary.py  # IGenerateCommentary
│   │   │   └── generate_response.py    # IGenerateResponse
│   │   └── output/
│   │       ├── text_generator.py       # ITextGenerator
│   │       └── prompt_builder.py       # IPromptBuilder
│   ├── use_cases/
│   │   ├── generate_commentary.py  # GenerateCommentaryUseCase
│   │   └── generate_response.py    # GenerateResponseUseCase
│   └── dto/
│       └── llm_dto.py              # Request/Response DTOs
│
├── infrastructure/
│   └── adapters/
│       └── gemini/
│           ├── gemini_text_generator.py  # GeminiTextGenerator
│           └── prompt_templates.py       # Prompt templates
│
└── presentation/
    └── services/
        └── llm_service.py          # LLMService (coordinates use cases)
```

## 3. Domain Layer

### 3.1 Entities

#### Conversation (domain/entities/conversation.py)

```python
"""Conversation aggregate root."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator
from uuid import UUID, uuid4

from ailoveshen.domain.value_objects.conversation_message import (
    ConversationMessage,
    MessageRole,
)


@dataclass
class Conversation:
    """
    Aggregate root for conversation history.

    Manages the conversation context and history.
    """
    id: UUID = field(default_factory=uuid4)
    max_history: int = 20
    created_at: datetime = field(default_factory=datetime.now)
    _messages: deque[ConversationMessage] = field(
        default_factory=lambda: deque(maxlen=20),
        repr=False,
    )

    def add_user_message(
        self,
        content: str,
        user_name: str,
        user_id: str | None = None,
    ) -> ConversationMessage:
        """Add a message from user (chat viewer)."""
        message = ConversationMessage.create_user_message(
            content=content,
            user_name=user_name,
            user_id=user_id,
        )
        self._messages.append(message)
        return message

    def add_assistant_message(
        self,
        content: str,
        message_type: str = "response",
    ) -> ConversationMessage:
        """Add a message from assistant (AI streamer)."""
        message = ConversationMessage.create_assistant_message(
            content=content,
            message_type=message_type,
        )
        self._messages.append(message)
        return message

    def add_system_message(self, content: str) -> ConversationMessage:
        """Add a system message (context/instruction)."""
        message = ConversationMessage.create_system_message(content)
        self._messages.append(message)
        return message

    def get_recent_messages(self, limit: int = 10) -> list[ConversationMessage]:
        """Get recent messages for context."""
        return list(self._messages)[-limit:]

    def get_recent_chat_messages(self, limit: int = 5) -> list[ConversationMessage]:
        """Get recent chat messages only."""
        chat_messages = [
            m for m in self._messages
            if m.role == MessageRole.USER
        ]
        return chat_messages[-limit:]

    def __iter__(self) -> Iterator[ConversationMessage]:
        return iter(self._messages)

    def __len__(self) -> int:
        return len(self._messages)

    def clear(self) -> None:
        """Clear conversation history."""
        self._messages.clear()
```

### 3.2 Value Objects

#### ConversationMessage (domain/value_objects/conversation_message.py)

```python
"""Conversation message value object."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4


class MessageRole(str, Enum):
    """Role of the message sender."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass(frozen=True)
class ConversationMessage:
    """
    Immutable value object representing a message in conversation.
    """
    id: UUID
    role: MessageRole
    content: str
    timestamp: datetime
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create_user_message(
        cls,
        content: str,
        user_name: str,
        user_id: Optional[str] = None,
    ) -> ConversationMessage:
        """Factory method for user messages."""
        return cls(
            id=uuid4(),
            role=MessageRole.USER,
            content=content,
            timestamp=datetime.now(),
            metadata={
                "user_name": user_name,
                "user_id": user_id,
                "type": "chat",
            },
        )

    @classmethod
    def create_assistant_message(
        cls,
        content: str,
        message_type: str = "response",
    ) -> ConversationMessage:
        """Factory method for assistant messages."""
        return cls(
            id=uuid4(),
            role=MessageRole.ASSISTANT,
            content=content,
            timestamp=datetime.now(),
            metadata={"type": message_type},
        )

    @classmethod
    def create_system_message(cls, content: str) -> ConversationMessage:
        """Factory method for system messages."""
        return cls(
            id=uuid4(),
            role=MessageRole.SYSTEM,
            content=content,
            timestamp=datetime.now(),
            metadata={"type": "system"},
        )

    def to_llm_format(self) -> dict:
        """Convert to format expected by LLM APIs."""
        return {
            "role": self.role.value,
            "parts": [self.content],
        }
```

#### CharacterProfile (domain/value_objects/character_profile.py)

```python
"""Character profile value object."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CharacterProfile:
    """
    Immutable value object representing AI streamer character.

    Encapsulates all character-related configuration.
    """
    name: str = "AILoveShen"
    description: str = "明るく元気なAI配信者"
    speech_style: str = "フレンドリーで親しみやすい"
    first_person: str = "私"
    sentence_endings: tuple[str, ...] = ("だよ", "だね", "かな", "！")
    personality_traits: tuple[str, ...] = (
        "好奇心旺盛",
        "ポジティブ",
        "ちょっとおっちょこちょい",
        "視聴者思い",
    )

    def get_endings_description(self) -> str:
        """Get sentence endings as comma-separated string."""
        return "、".join(self.sentence_endings)
```

#### GenerationContext (domain/value_objects/generation_context.py)

```python
"""Generation context value object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.value_objects.emotion import EmotionState


@dataclass(frozen=True)
class GenerationContext:
    """
    Immutable context for text generation.

    Combines all contextual information needed for generation.
    """
    game_state: Optional[GameState] = None
    emotion_state: EmotionState = field(default_factory=EmotionState)
    recent_events_summary: str = "特になし"
    recent_chat_summary: str = ""

    def format_game_state(self) -> str:
        """Format game state for prompt."""
        if not self.game_state:
            return "ゲーム状態: 不明"

        gs = self.game_state
        lines = [
            f"体力: {gs.health}/{gs.max_health}",
            f"空腹度: {gs.hunger}/20",
            f"位置: X={gs.position.x:.0f}, Y={gs.position.y:.0f}, Z={gs.position.z:.0f}",
            f"バイオーム: {gs.biome}",
            f"天候: {gs.weather}",
        ]

        if gs.main_hand_item:
            lines.append(f"手持ち: {gs.main_hand_item}")

        return "\n".join(lines)

    def format_emotion(self) -> str:
        """Format emotion for prompt."""
        return f"{self.emotion_state.primary.value}（強度: {self.emotion_state.intensity:.1f}）"
```

### 3.3 Domain Services

#### ConversationService (domain/services/conversation_service.py)

```python
"""Conversation domain service."""

from __future__ import annotations

from ailoveshen.domain.entities.chat_message import ChatMessage
from ailoveshen.domain.entities.conversation import Conversation
from ailoveshen.domain.value_objects.generation_context import GenerationContext


class ConversationService:
    """
    Domain service for conversation management.

    Provides business logic for managing conversation context.
    """

    def __init__(self, max_history: int = 20) -> None:
        self._conversation = Conversation(max_history=max_history)

    def add_chat_message(self, message: ChatMessage) -> None:
        """Add incoming chat message."""
        formatted_content = f"[{message.user.display_name}]: {message.content}"
        self._conversation.add_user_message(
            content=formatted_content,
            user_name=message.user.name,
            user_id=message.user.id,
        )

    def add_response(self, text: str) -> None:
        """Add AI response."""
        self._conversation.add_assistant_message(
            content=text,
            message_type="response",
        )

    def add_commentary(self, text: str) -> None:
        """Add generated commentary."""
        self._conversation.add_assistant_message(
            content=text,
            message_type="commentary",
        )

    def build_context_summary(self) -> str:
        """Build summary of recent context."""
        recent_chat = self._conversation.get_recent_chat_messages(limit=5)
        if not recent_chat:
            return "（特になし）"

        parts = ["最近のコメント:"]
        for msg in recent_chat:
            parts.append(f"  {msg.content}")
        return "\n".join(parts)

    def get_messages_for_llm(self, limit: int = 10) -> list[dict]:
        """Get messages formatted for LLM API."""
        messages = self._conversation.get_recent_messages(limit)
        return [msg.to_llm_format() for msg in messages]

    def clear(self) -> None:
        """Clear conversation history."""
        self._conversation.clear()
```

## 4. Application Layer

### 4.1 Output Ports

#### ITextGenerator (application/ports/output/text_generator.py)

```python
"""Text generator output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class ITextGenerator(ABC):
    """
    Output port for text generation.

    Abstracts the LLM service.
    """

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
    ) -> str:
        """
        Generate text from prompt.

        Args:
            prompt: User prompt
            system_instruction: System instruction (character context)

        Returns:
            Generated text
        """
        ...

    @abstractmethod
    async def generate_with_history(
        self,
        messages: list[dict],
        system_instruction: Optional[str] = None,
    ) -> str:
        """Generate text with conversation history."""
        ...

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the generator."""
        ...
```

#### IPromptBuilder (application/ports/output/prompt_builder.py)

```python
"""Prompt builder output port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.domain.value_objects.character_profile import CharacterProfile
from ailoveshen.domain.value_objects.generation_context import GenerationContext


class IPromptBuilder(ABC):
    """
    Output port for building prompts.

    Abstracts prompt template handling.
    """

    @abstractmethod
    def build_system_prompt(self, character: CharacterProfile) -> str:
        """Build system prompt from character profile."""
        ...

    @abstractmethod
    def build_commentary_prompt(
        self,
        context: GenerationContext,
    ) -> str:
        """Build game commentary prompt."""
        ...

    @abstractmethod
    def build_chat_response_prompt(
        self,
        user_name: str,
        message: str,
        context: GenerationContext,
        context_summary: str,
    ) -> str:
        """Build chat response prompt."""
        ...
```

### 4.2 Input Ports

#### IGenerateCommentary (application/ports/input/generate_commentary.py)

```python
"""Generate commentary input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateCommentaryResponse,
)


class IGenerateCommentary(ABC):
    """Input port for commentary generation use case."""

    @abstractmethod
    async def execute(
        self,
        request: GenerateCommentaryRequest,
    ) -> GenerateCommentaryResponse:
        """Execute commentary generation."""
        ...
```

#### IGenerateResponse (application/ports/input/generate_response.py)

```python
"""Generate response input port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.llm_dto import (
    GenerateResponseRequest,
    GenerateResponseResponse,
)


class IGenerateResponse(ABC):
    """Input port for chat response generation use case."""

    @abstractmethod
    async def execute(
        self,
        request: GenerateResponseRequest,
    ) -> GenerateResponseResponse:
        """Execute response generation."""
        ...
```

### 4.3 DTOs

#### LLM DTOs (application/dto/llm_dto.py)

```python
"""LLM-related DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.value_objects.emotion import EmotionState


@dataclass
class GenerateCommentaryRequest:
    """Input DTO for commentary generation."""
    game_state: GameState
    emotion_state: EmotionState
    recent_events: list[str] = None

    def __post_init__(self):
        if self.recent_events is None:
            self.recent_events = []


@dataclass
class GenerateCommentaryResponse:
    """Output DTO for commentary generation."""
    text: str
    success: bool
    error: Optional[str] = None


@dataclass
class GenerateResponseRequest:
    """Input DTO for chat response generation."""
    user_name: str
    user_id: str
    message: str
    emotion_state: EmotionState


@dataclass
class GenerateResponseResponse:
    """Output DTO for chat response generation."""
    text: str
    success: bool
    original_message: str
    user_name: str
    error: Optional[str] = None
```

### 4.4 Use Cases

#### GenerateCommentaryUseCase (application/use_cases/generate_commentary.py)

```python
"""Generate commentary use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateCommentaryResponse,
)
from ailoveshen.application.ports.input.generate_commentary import IGenerateCommentary
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.prompt_builder import IPromptBuilder
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.events import CommentaryGeneratedEvent
from ailoveshen.domain.services.conversation_service import ConversationService
from ailoveshen.domain.value_objects.character_profile import CharacterProfile
from ailoveshen.domain.value_objects.generation_context import GenerationContext


class GenerateCommentaryUseCase(IGenerateCommentary):
    """
    Use case for generating game commentary.

    Coordinates:
    - Context building from game state
    - Prompt construction
    - Text generation
    - Domain event publishing
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IPromptBuilder,
        event_publisher: IEventPublisher,
        conversation_service: ConversationService,
        character_profile: CharacterProfile,
    ) -> None:
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._event_publisher = event_publisher
        self._conversation_service = conversation_service
        self._character = character_profile

    async def execute(
        self,
        request: GenerateCommentaryRequest,
    ) -> GenerateCommentaryResponse:
        """Execute commentary generation."""
        try:
            # Build generation context
            recent_events = "\n".join([
                f"- {e}" for e in request.recent_events[:3]
            ]) if request.recent_events else "特になし"

            context = GenerationContext(
                game_state=request.game_state,
                emotion_state=request.emotion_state,
                recent_events_summary=recent_events,
            )

            # Build prompts
            system_prompt = self._prompt_builder.build_system_prompt(
                self._character
            )
            user_prompt = self._prompt_builder.build_commentary_prompt(context)

            # Generate text
            logger.debug(f"Generating commentary for game state")
            text = await self._text_generator.generate(
                prompt=user_prompt,
                system_instruction=system_prompt,
            )

            if text:
                # Update conversation context
                self._conversation_service.add_commentary(text)

                # Publish domain event
                await self._event_publisher.publish(
                    CommentaryGeneratedEvent(text=text)
                )

            return GenerateCommentaryResponse(
                text=text,
                success=bool(text),
            )

        except Exception as e:
            logger.error(f"Commentary generation failed: {e}")
            return GenerateCommentaryResponse(
                text="",
                success=False,
                error=str(e),
            )
```

#### GenerateResponseUseCase (application/use_cases/generate_response.py)

```python
"""Generate chat response use case."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.llm_dto import (
    GenerateResponseRequest,
    GenerateResponseResponse,
)
from ailoveshen.application.ports.input.generate_response import IGenerateResponse
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.prompt_builder import IPromptBuilder
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities.chat_message import ChatMessage, ChatUser
from ailoveshen.domain.events import ChatResponseGeneratedEvent
from ailoveshen.domain.services.conversation_service import ConversationService
from ailoveshen.domain.value_objects.character_profile import CharacterProfile
from ailoveshen.domain.value_objects.generation_context import GenerationContext


class GenerateResponseUseCase(IGenerateResponse):
    """
    Use case for generating chat response.

    Coordinates:
    - Chat message context building
    - Prompt construction
    - Text generation
    - Conversation history management
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IPromptBuilder,
        event_publisher: IEventPublisher,
        conversation_service: ConversationService,
        character_profile: CharacterProfile,
    ) -> None:
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._event_publisher = event_publisher
        self._conversation_service = conversation_service
        self._character = character_profile

    async def execute(
        self,
        request: GenerateResponseRequest,
    ) -> GenerateResponseResponse:
        """Execute chat response generation."""
        try:
            # Create chat message domain entity
            chat_message = ChatMessage(
                id=str(uuid4()),
                user=ChatUser(
                    id=request.user_id,
                    name=request.user_name,
                    display_name=request.user_name,
                ),
                content=request.message,
            )

            # Add to conversation context
            self._conversation_service.add_chat_message(chat_message)

            # Build context
            context = GenerationContext(
                emotion_state=request.emotion_state,
            )
            context_summary = self._conversation_service.build_context_summary()

            # Build prompts
            system_prompt = self._prompt_builder.build_system_prompt(
                self._character
            )
            user_prompt = self._prompt_builder.build_chat_response_prompt(
                user_name=request.user_name,
                message=request.message,
                context=context,
                context_summary=context_summary,
            )

            # Generate text
            logger.debug(f"Generating response for: {request.message[:50]}...")
            text = await self._text_generator.generate(
                prompt=user_prompt,
                system_instruction=system_prompt,
            )

            if text:
                # Update conversation context
                self._conversation_service.add_response(text)

                # Publish domain event
                await self._event_publisher.publish(
                    ChatResponseGeneratedEvent(
                        text=text,
                        original_message=request.message,
                        user_name=request.user_name,
                    )
                )

            return GenerateResponseResponse(
                text=text,
                success=bool(text),
                original_message=request.message,
                user_name=request.user_name,
            )

        except Exception as e:
            logger.error(f"Response generation failed: {e}")
            return GenerateResponseResponse(
                text="",
                success=False,
                original_message=request.message,
                user_name=request.user_name,
                error=str(e),
            )
```

## 5. Infrastructure Layer

### 5.1 Gemini Text Generator

#### GeminiTextGenerator (infrastructure/adapters/gemini/gemini_text_generator.py)

```python
"""Gemini API text generator adapter."""

from __future__ import annotations

import asyncio
from typing import Optional

import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from loguru import logger

from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.core.exceptions import TextGenerationError


class GeminiTextGenerator(ITextGenerator):
    """
    Infrastructure adapter for Google Gemini API.

    Implements ITextGenerator output port.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-pro",
        temperature: float = 0.9,
        top_p: float = 0.95,
        top_k: int = 40,
        max_output_tokens: int = 500,
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._generation_config = GenerationConfig(
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_output_tokens=max_output_tokens,
        )
        self._model: Optional[genai.GenerativeModel] = None

    async def initialize(self) -> None:
        """Initialize Gemini model."""
        genai.configure(api_key=self._api_key)
        self._model = genai.GenerativeModel(
            model_name=self._model_name,
            generation_config=self._generation_config,
        )
        logger.info(f"Gemini initialized: model={self._model_name}")

    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
    ) -> str:
        """Generate text using Gemini."""
        if self._model is None:
            raise TextGenerationError("Gemini not initialized")

        try:
            # Combine system instruction with prompt if provided
            full_prompt = prompt
            if system_instruction:
                full_prompt = f"{system_instruction}\n\n{prompt}"

            # Run in executor to not block event loop
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._model.generate_content(
                    contents=full_prompt,
                ),
            )

            if response.text:
                return response.text.strip()

            # Handle blocked content
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
                logger.warning(f"Content blocked: {response.prompt_feedback.block_reason}")
                return ""

            return ""

        except Exception as e:
            raise TextGenerationError(f"Generation failed: {e}")

    async def generate_with_history(
        self,
        messages: list[dict],
        system_instruction: Optional[str] = None,
    ) -> str:
        """Generate with conversation history."""
        if self._model is None:
            raise TextGenerationError("Gemini not initialized")

        try:
            chat = self._model.start_chat(history=messages[:-1])

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: chat.send_message(messages[-1]["parts"][0]),
            )

            return response.text.strip() if response.text else ""

        except Exception as e:
            raise TextGenerationError(f"Generation with history failed: {e}")
```

### 5.2 Prompt Templates

#### PromptTemplates (infrastructure/adapters/gemini/prompt_templates.py)

```python
"""Prompt template implementation."""

from __future__ import annotations

from string import Template

from ailoveshen.application.ports.output.prompt_builder import IPromptBuilder
from ailoveshen.domain.value_objects.character_profile import CharacterProfile
from ailoveshen.domain.value_objects.generation_context import GenerationContext


# Template definitions
CHARACTER_SYSTEM_TEMPLATE = Template("""
あなたは「$name」という名前のAI配信者です。

## キャラクター設定
$description

## 話し方の特徴
- $speech_style
- 一人称は「$first_person」を使う
- 語尾は「$sentence_endings」を使うことが多い

## 配信スタイル
- Minecraftを実況プレイしながら、視聴者とコミュニケーションを取る
- ゲームの状況に応じた自然なリアクションをする
- 視聴者のコメントには親しみを込めて返答する

## 重要な注意
- 自分がAIであることは隠さないが、積極的には言わない
- 不適切な内容や攻撃的な発言は避ける
- 視聴者との関係性を大切にする
""")

COMMENTARY_TEMPLATE = Template("""
## 現在のゲーム状況
$game_state

## 最近のイベント
$recent_events

## あなたの感情状態
$emotion

## タスク
上記の状況を踏まえて、配信者として自然な実況・独り言・考えを1-2文で述べてください。
- ゲームの状況に即した内容
- キャラクターらしい話し方
- 視聴者が見ていることを意識した発言

## 出力
実況テキストのみを出力してください（説明や注釈は不要）。
""")

CHAT_RESPONSE_TEMPLATE = Template("""
## 視聴者からのコメント
ユーザー名: $user_name
コメント: $message

## 現在の状況
$context_summary

## あなたの感情状態
$emotion

## タスク
このコメントに対して、配信者として自然に返答してください。
- 視聴者の名前を呼んで親しみを込める
- 短く簡潔に（1-2文）
- キャラクターらしい話し方

## 出力
返答テキストのみを出力してください。
""")


class PromptTemplateBuilder(IPromptBuilder):
    """
    Infrastructure adapter for prompt building.

    Implements IPromptBuilder output port.
    """

    def build_system_prompt(self, character: CharacterProfile) -> str:
        """Build system prompt from character profile."""
        return CHARACTER_SYSTEM_TEMPLATE.substitute(
            name=character.name,
            description=character.description,
            speech_style=character.speech_style,
            first_person=character.first_person,
            sentence_endings=character.get_endings_description(),
        )

    def build_commentary_prompt(
        self,
        context: GenerationContext,
    ) -> str:
        """Build game commentary prompt."""
        return COMMENTARY_TEMPLATE.substitute(
            game_state=context.format_game_state(),
            recent_events=context.recent_events_summary,
            emotion=context.format_emotion(),
        )

    def build_chat_response_prompt(
        self,
        user_name: str,
        message: str,
        context: GenerationContext,
        context_summary: str,
    ) -> str:
        """Build chat response prompt."""
        return CHAT_RESPONSE_TEMPLATE.substitute(
            user_name=user_name,
            message=message,
            context_summary=context_summary,
            emotion=context.format_emotion(),
        )
```

## 6. Presentation Layer

### 6.1 LLM Service

#### LLMService (presentation/services/llm_service.py)

```python
"""LLM service for presentation layer."""

from __future__ import annotations

from typing import Optional

from loguru import logger

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateResponseRequest,
)
from ailoveshen.application.ports.input.generate_commentary import IGenerateCommentary
from ailoveshen.application.ports.input.generate_response import IGenerateResponse
from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.value_objects.emotion import EmotionState


class LLMService:
    """
    Presentation layer service for LLM operations.

    Provides simplified interface for other components.
    """

    def __init__(
        self,
        generate_commentary_use_case: IGenerateCommentary,
        generate_response_use_case: IGenerateResponse,
    ) -> None:
        self._generate_commentary = generate_commentary_use_case
        self._generate_response = generate_response_use_case
        self._current_emotion = EmotionState()

    async def generate_commentary(
        self,
        game_state: GameState,
        recent_events: Optional[list[str]] = None,
    ) -> str:
        """
        Generate game commentary.

        Args:
            game_state: Current game state
            recent_events: List of recent event descriptions

        Returns:
            Generated commentary text
        """
        request = GenerateCommentaryRequest(
            game_state=game_state,
            emotion_state=self._current_emotion,
            recent_events=recent_events or [],
        )

        response = await self._generate_commentary.execute(request)
        return response.text if response.success else ""

    async def generate_response(
        self,
        user_name: str,
        user_id: str,
        message: str,
    ) -> str:
        """
        Generate response to chat message.

        Args:
            user_name: Username
            user_id: User ID
            message: Message content

        Returns:
            Generated response text
        """
        request = GenerateResponseRequest(
            user_name=user_name,
            user_id=user_id,
            message=message,
            emotion_state=self._current_emotion,
        )

        response = await self._generate_response.execute(request)
        return response.text if response.success else ""

    def update_emotion(self, emotion_state: EmotionState) -> None:
        """Update current emotion state."""
        self._current_emotion = emotion_state
```

## 7. Composition Root (LLM部分)

```python
# src/ailoveshen/main.py (LLM部分の抜粋)

from ailoveshen.application.use_cases.generate_commentary import GenerateCommentaryUseCase
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.domain.services.conversation_service import ConversationService
from ailoveshen.domain.value_objects.character_profile import CharacterProfile
from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import GeminiTextGenerator
from ailoveshen.infrastructure.adapters.gemini.prompt_templates import PromptTemplateBuilder
from ailoveshen.presentation.services.llm_service import LLMService


def create_llm_service(
    config: GeminiConfig,
    event_publisher: IEventPublisher,
) -> LLMService:
    """Create LLM service with all dependencies."""

    # Infrastructure adapters
    text_generator = GeminiTextGenerator(
        api_key=config.api_key,
        model_name=config.main_model,
        temperature=config.generation.temperature,
        top_p=config.generation.top_p,
        top_k=config.generation.top_k,
        max_output_tokens=config.generation.max_output_tokens,
    )

    prompt_builder = PromptTemplateBuilder()

    # Domain services
    conversation_service = ConversationService(max_history=20)

    # Character profile (from config)
    character = CharacterProfile(
        name=config.character.name,
        description=config.character.description,
        speech_style=config.character.speech_style,
        first_person=config.character.first_person,
        sentence_endings=tuple(config.character.sentence_endings),
    )

    # Use cases
    generate_commentary_use_case = GenerateCommentaryUseCase(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        event_publisher=event_publisher,
        conversation_service=conversation_service,
        character_profile=character,
    )

    generate_response_use_case = GenerateResponseUseCase(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        event_publisher=event_publisher,
        conversation_service=conversation_service,
        character_profile=character,
    )

    # Presentation service
    return LLMService(
        generate_commentary_use_case=generate_commentary_use_case,
        generate_response_use_case=generate_response_use_case,
    )
```

## 8. データフロー図（Clean Architecture版）

```
┌────────────────────────────────────────────────────────────────────────┐
│                         PRESENTATION LAYER                              │
│  ┌──────────────┐                                                       │
│  │  LLMService  │  ← Orchestratorからの呼び出し                          │
│  └──────┬───────┘                                                       │
└─────────┼──────────────────────────────────────────────────────────────┘
          │ GenerateCommentaryRequest / GenerateResponseRequest
          ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         APPLICATION LAYER                               │
│  ┌──────────────────────┐    ┌──────────────────────┐                   │
│  │IGenerateCommentary   │    │IGenerateResponse     │ ← Input Ports     │
│  └──────────┬───────────┘    └──────────┬───────────┘                   │
│             │                           │                               │
│  ┌──────────▼───────────┐    ┌──────────▼───────────┐                   │
│  │GenerateCommentaryUC  │    │GenerateResponseUC    │                   │
│  │  - text_generator    │→   │  - text_generator    │→ ITextGenerator   │
│  │  - prompt_builder    │→   │  - prompt_builder    │→ IPromptBuilder   │
│  │  - event_publisher   │→   │  - event_publisher   │→ IEventPublisher  │
│  └──────────────────────┘    └──────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                            DOMAIN LAYER                                 │
│  ┌───────────────────┐  ┌────────────────────┐  ┌───────────────────┐   │
│  │ ConversationService│  │ CharacterProfile  │  │ GenerationContext │   │
│  │  (Domain Service) │  │  (Value Object)   │  │  (Value Object)   │   │
│  └───────────────────┘  └────────────────────┘  └───────────────────┘   │
│  ┌───────────────────┐  ┌────────────────────┐                          │
│  │   Conversation    │  │ConversationMessage │                          │
│  │    (Entity)       │  │  (Value Object)    │                          │
│  └───────────────────┘  └────────────────────┘                          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                       INFRASTRUCTURE LAYER                               │
│  ┌───────────────────────┐    ┌────────────────────────┐                 │
│  │ GeminiTextGenerator   │    │ PromptTemplateBuilder  │                 │
│  │ (implements           │    │ (implements            │                 │
│  │  ITextGenerator)      │    │  IPromptBuilder)       │                 │
│  └───────────┬───────────┘    └────────────────────────┘                 │
│              │                                                           │
│              ▼                                                           │
│    [Google Gemini API]                                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

## 9. 設定

```yaml
# config/default.yaml (LLM section)
gemini:
  api_key: "${GEMINI_API_KEY}"
  main_model: "gemini-2.5-pro"
  filter_model: "gemini-2.0-flash"

  generation:
    temperature: 0.9
    top_p: 0.95
    top_k: 40
    max_output_tokens: 500

  character:
    name: "AILoveShen"
    description: |
      明るく元気なAI配信者。
      Minecraftが大好きで、建築よりも冒険派。
      視聴者との交流を楽しんでいる。
    speech_style: |
      フレンドリーで親しみやすい話し方。
      時々ゲームに熱中して独り言が多くなる。
    first_person: "私"
    sentence_endings:
      - "だよ"
      - "だね"
      - "かな"
      - "！"
```

## 10. テスト計画

### 10.1 Unit Tests

```python
# tests/unit/domain/test_conversation.py
import pytest
from ailoveshen.domain.entities.conversation import Conversation
from ailoveshen.domain.value_objects.conversation_message import MessageRole


def test_conversation_add_user_message():
    """Test adding user messages."""
    conv = Conversation()
    msg = conv.add_user_message(
        content="Hello!",
        user_name="testuser",
    )

    assert msg.role == MessageRole.USER
    assert msg.content == "Hello!"
    assert len(conv) == 1


def test_conversation_max_history():
    """Test that conversation respects max history."""
    conv = Conversation(max_history=5)

    for i in range(10):
        conv.add_user_message(
            content=f"Message {i}",
            user_name="user",
        )

    assert len(conv) == 5
    assert conv.get_recent_messages()[0].content == "Message 5"
```

### 10.2 Integration Tests

```python
# tests/integration/test_llm_use_cases.py
import pytest
from unittest.mock import AsyncMock

from ailoveshen.application.dto.llm_dto import GenerateCommentaryRequest
from ailoveshen.application.use_cases.generate_commentary import GenerateCommentaryUseCase
from ailoveshen.domain.services.conversation_service import ConversationService
from ailoveshen.domain.value_objects.character_profile import CharacterProfile
from ailoveshen.domain.value_objects.emotion import EmotionState


@pytest.fixture
def mock_text_generator():
    generator = AsyncMock()
    generator.generate.return_value = "わー、森に来たね！"
    return generator


@pytest.fixture
def mock_prompt_builder():
    builder = AsyncMock()
    builder.build_system_prompt.return_value = "System prompt"
    builder.build_commentary_prompt.return_value = "User prompt"
    return builder


@pytest.mark.asyncio
async def test_generate_commentary_use_case(
    mock_text_generator,
    mock_prompt_builder,
):
    """Test commentary generation flow."""
    use_case = GenerateCommentaryUseCase(
        text_generator=mock_text_generator,
        prompt_builder=mock_prompt_builder,
        event_publisher=AsyncMock(),
        conversation_service=ConversationService(),
        character_profile=CharacterProfile(),
    )

    request = GenerateCommentaryRequest(
        game_state=create_mock_game_state(),
        emotion_state=EmotionState(),
    )

    response = await use_case.execute(request)

    assert response.success
    assert response.text == "わー、森に来たね！"
    mock_text_generator.generate.assert_called_once()
```
