#!/usr/bin/env python3
"""Phase 1 Core Infrastructure の動作確認デモ."""

import asyncio
from dataclasses import dataclass

# =============================================================================
# 1. 設定管理のデモ
# =============================================================================
print("=" * 60)
print("1. 設定管理（Configuration）")
print("=" * 60)

from ailoveshen.infrastructure.config import load_settings

settings = load_settings()
print(f"  アプリ名: {settings.app_name}")
print(f"  デバッグ: {settings.debug}")
print(f"  Gemini モデル: {settings.gemini.main_model}")
print(f"  TTS サーバー: {settings.tts.server.host}:{settings.tts.server.port}")
print(f"  ログレベル: {settings.logging.level}")
print()

# =============================================================================
# 2. ロギングのデモ
# =============================================================================
print("=" * 60)
print("2. ロギング（Logging）")
print("=" * 60)

from ailoveshen.infrastructure.logging import get_logger, setup_logging

# デバッグモードでセットアップ（diagnoseは無効）
setup_logging(level="DEBUG", debug=False)

logger = get_logger(__name__)
logger.debug("これはDEBUGメッセージです")
logger.info("これはINFOメッセージです")
logger.warning("これはWARNINGメッセージです")
print()

# =============================================================================
# 3. Value Objectsのデモ
# =============================================================================
print("=" * 60)
print("3. Value Objects")
print("=" * 60)

from ailoveshen.domain.value_objects import (
    EmotionState,
    EmotionType,
    FilterResult,
    Position,
    SpeechPriority,
    SpeechRequest,
)

# EmotionState
emotion = EmotionState(EmotionType.HAPPY, 0.8)
print(f"  感情: {emotion.primary.value} (強さ: {emotion.intensity})")

# 感情の減衰
decayed = emotion.decay(0.3)
print(f"  減衰後: {decayed.primary.value} (強さ: {decayed.intensity})")

# Position
pos1 = Position(0, 0, 0)
pos2 = Position(3, 4, 0)
print(f"  {pos1} から {pos2} までの距離: {pos1.distance_to(pos2)}")

# SpeechRequest
speech = SpeechRequest(
    text="こんにちは！配信を始めます！",
    priority=SpeechPriority.HIGH,
    emotion=EmotionState(EmotionType.HAPPY, 0.8),
    source="commentary",
)
print(f"  発話: '{speech.text[:20]}...' (優先度: {speech.priority.name})")

# FilterResult
result = FilterResult.accept(0.85, "Interesting question")
print(f"  フィルター: should_respond={result.should_respond}, score={result.score}")
print()

# =============================================================================
# 4. Entityのデモ
# =============================================================================
print("=" * 60)
print("4. Entity & AggregateRoot")
print("=" * 60)

from ailoveshen.domain.entities import AggregateRoot, Entity
from ailoveshen.domain.events import DomainEvent


@dataclass
class UserMessage(Entity):
    """サンプルエンティティ."""

    content: str = ""
    user_name: str = ""


@dataclass(frozen=True)
class MessageReceived(DomainEvent):
    """サンプルドメインイベント."""

    message_id: str = ""
    content: str = ""


# エンティティの作成
msg1 = UserMessage(content="Hello!", user_name="TestUser")
msg2 = UserMessage(content="World!", user_name="TestUser")

print(f"  メッセージ 1: {msg1}")
print(f"  メッセージ 2: {msg2}")
print(f"  同じエンティティか: {msg1 == msg2}")  # False (異なるID)

# タイムスタンプがUTCか確認
print(f"  作成時刻 (UTC): {msg1.created_at}")
print()

# =============================================================================
# 5. EventBusのデモ
# =============================================================================
print("=" * 60)
print("5. EventBus（非同期Pub/Sub）")
print("=" * 60)


async def demo_event_bus():
    from ailoveshen.infrastructure.events import AsyncEventBus

    bus = AsyncEventBus()
    received_events: list[MessageReceived] = []

    # イベントハンドラーを定義
    async def on_message_received(event: MessageReceived) -> None:
        received_events.append(event)
        print(f"    [ハンドラー] 受信: '{event.content}'")

    # 購読
    unsubscribe = bus.subscribe(MessageReceived, on_message_received)
    print(f"  MessageReceived を購読 (ハンドラー数: {bus.handler_count()})")

    # イベント発行
    event1 = MessageReceived(message_id="1", content="Hello from EventBus!")
    event2 = MessageReceived(message_id="2", content="Second message!")

    print("  イベントを発行中...")
    await bus.publish(event1)
    await bus.publish(event2)

    print(f"  受け取ったイベント数: {len(received_events)}")

    # 購読解除
    unsubscribe()
    print(f"  購読を解除 (ハンドラー数: {bus.handler_count()})")

    # 購読解除後はイベントを受け取らない
    await bus.publish(MessageReceived(message_id="3", content="Won't be received"))
    print(f"  購読解除後のイベント数: {len(received_events)}")


asyncio.run(demo_event_bus())
print()

# =============================================================================
# 完了
# =============================================================================
print("=" * 60)
print("Phase 1 Core Infrastructure - 動作確認完了!")
print("=" * 60)
