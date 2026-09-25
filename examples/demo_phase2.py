#!/usr/bin/env python3
"""
Phase 2 デモ: TTS パイプライン

Phase 2 で実装した TTS（音声合成）パイプラインを動かす。
見せるもの:
- ドメイン層: 値オブジェクト、ドメインサービス、イベント
- アプリケーション層: ユースケース、DTO、ポート
- プレゼンテーション層: キューを管理する TTSService

注: TTS サーバーを起動しなくてよいように、アダプターはモックを使う。
本物の TTS は `pip install ailoveshen[tts]` でインストールし、
Style-Bert-VITS2 のサーバーを起動する。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List
from unittest.mock import AsyncMock, Mock

from ailoveshen.domain.value_objects import (
    EmotionState,
    EmotionType,
    SpeechPriority,
    SpeechResult,
    SpeechStatus,
)
from ailoveshen.infrastructure.events import AsyncEventBus
from ailoveshen.domain.events import SpeechStartedEvent, SpeechCompletedEvent
from ailoveshen.infrastructure.adapters.tts.emotion_style_service import EmotionStyleService
from ailoveshen.infrastructure.adapters.tts.voice_config import VoiceConfig
from ailoveshen.application.dto.speech_dto import SpeakTextRequest, SpeakTextResponse
from ailoveshen.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.presentation.services.tts_service import TTSService


def print_header(title: str) -> None:
    """整形したセクション見出しを表示する。"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def demo_domain_value_objects() -> None:
    """ドメインの値オブジェクトを見せる。"""
    print_header("ドメインの値オブジェクト")

    # SpeechResult
    print("SpeechResult:")
    result_completed = SpeechResult.completed("Hello world", 1500)
    print(f"  完了: {result_completed}")
    print(f"    - is_success: {result_completed.is_success}")
    print(f"    - duration_ms: {result_completed.audio_duration_ms}")

    result_failed = SpeechResult.failed("Error text", "Connection timeout")
    print(f"  失敗: {result_failed}")
    print(f"    - error_message: {result_failed.error_message}")

    # VoiceConfig
    print("\nVoiceConfig:")
    config = VoiceConfig(
        model_name="my_model",
        speaker_id=1,
        language="JP",
    )
    print(f"  {config}")

    # 検証
    print("\n  検証のテスト:")
    try:
        VoiceConfig(speaker_id=-1)
    except ValueError as e:
        print(f"    不正な speaker_id で例外: {e}")


def demo_emotion_style_service() -> None:
    """感情からスタイルへの対応を見せる。"""
    print_header("EmotionStyleService (TTS Adapter)")

    service = EmotionStyleService()

    emotions = [
        (EmotionType.NEUTRAL, 0.5),
        (EmotionType.HAPPY, 0.8),
        (EmotionType.SAD, 0.6),
        (EmotionType.ANGRY, 0.9),
        (EmotionType.EXCITED, 0.7),
    ]

    print("感情 -> スタイルの対応:")
    for emotion_type, intensity in emotions:
        state = EmotionState(primary=emotion_type, intensity=intensity)
        style = service.get_style_for_emotion(state)
        weight = service.get_style_weight(state)
        print(
            f"  {emotion_type.value:12} (強さ={intensity}) -> "
            f"スタイル: {style:10} 重み: {weight:.1f}"
        )

    print(f"\n使えるスタイル: {service.get_available_styles()}")


def demo_domain_events() -> None:
    """ドメインイベントを見せる。"""
    print_header("ドメインイベント")

    started = SpeechStartedEvent(
        text="Hello from AI",
        source="commentary",
        emotion=EmotionState(EmotionType.HAPPY, 0.8),
    )
    print(f"SpeechStartedEvent:")
    print(f"  event_type: {started.event_type}")
    print(f"  event_id: {started.event_id[:8]}...")
    print(f"  occurred_at: {started.occurred_at}")
    print(f"  text: {started.text}")
    print(f"  source: {started.source}")
    print(f"  emotion: {started.emotion.primary.value}")

    completed = SpeechCompletedEvent(
        text="Hello from AI",
        source="commentary",
        completed=True,
        duration_ms=1200,
    )
    print(f"\nSpeechCompletedEvent:")
    print(f"  completed: {completed.completed}")
    print(f"  duration_ms: {completed.duration_ms}")


async def demo_use_case() -> None:
    """依存をモックにして SpeakTextUseCase を見せる。"""
    print_header("SpeakTextUseCase（アプリケーション層）")

    # モックのアダプターを作る
    mock_synthesizer = AsyncMock()
    mock_synthesizer.synthesize.return_value = b"fake_audio_data"

    mock_audio_player = AsyncMock()
    mock_audio_player.play.return_value = True
    mock_audio_player.is_playing = Mock(return_value=False)
    mock_audio_player.get_duration_ms = Mock(return_value=1500)
    mock_audio_player.stop = Mock()

    # イベントバスは本物を使う
    event_bus = AsyncEventBus()
    events_received: List[str] = []

    async def on_started(event: SpeechStartedEvent) -> None:
        events_received.append(f"開始: {event.text[:20]}...")

    async def on_completed(event: SpeechCompletedEvent) -> None:
        events_received.append(f"完了: {event.text[:20]}... (completed={event.completed})")

    event_bus.subscribe(SpeechStartedEvent, on_started)
    event_bus.subscribe(SpeechCompletedEvent, on_completed)

    # 今の感情を持つ
    current_emotion = EmotionState(primary=EmotionType.HAPPY, intensity=0.7)

    # ユースケースを作る
    use_case = SpeakTextUseCase(
        synthesizer=mock_synthesizer,
        audio_player=mock_audio_player,
        event_publisher=event_bus,
        get_current_emotion=lambda: current_emotion,
    )

    # ユースケースを実行する
    print("発話のユースケースを実行中...")
    request = SpeakTextRequest(
        text="This is a test of the TTS system.",
        source="demo",
    )
    response = await use_case.execute(request)

    print(f"\nレスポンス:")
    print(f"  success: {response.success}")
    print(f"  message: {response.message}")
    print(f"  duration_ms: {response.duration_ms}")

    # イベントが処理されるのを待つ
    await asyncio.sleep(0.1)

    print(f"\n受け取ったイベント:")
    for event in events_received:
        print(f"  - {event}")

    # モックの呼び出しを確かめる
    print(f"\nモックの確認:")
    print(f"  synthesizer.synthesize の呼び出し: {mock_synthesizer.synthesize.called}")
    call_args = mock_synthesizer.synthesize.call_args
    print(f"  - 使った感情: {call_args.kwargs['emotion'].primary.value}")  # 今の感情


async def demo_tts_service() -> None:
    """プレゼンテーション層の TTSService を見せる。"""
    print_header("TTSService（プレゼンテーション層）")

    # モックのユースケースを作る
    mock_use_case = AsyncMock()
    mock_use_case.execute.return_value = SpeakTextResponse.ok(duration_ms=1000)

    # サービスを作る
    tts_service = TTSService(
        speak_text_use_case=mock_use_case,
        max_queue_size=5,
    )

    print("TTS サービスを起動中...")
    await tts_service.start()
    print(f"  is_running: {tts_service.is_running()}")

    # 発話をいくつかキューに入れる
    print("\n発話のリクエストをキューに入れる...")
    responses = []

    # 通常の優先度はキューに入る
    response = await tts_service.speak(
        text="First message, normal priority",
        priority=SpeechPriority.NORMAL,
        source="demo",
    )
    responses.append(("NORMAL", response))

    response = await tts_service.speak(
        text="Second message, high priority",
        priority=SpeechPriority.HIGH,
        source="demo",
    )
    responses.append(("HIGH", response))

    print(f"  追加後のキューの長さ: {tts_service.get_queue_size()}")

    for priority, resp in responses:
        print(f"  [{priority}] queued={resp.queued}, success={resp.success}")

    # 割り込みの優先度はすぐ処理する
    print("\n割り込みの優先度でメッセージを送る...")
    response = await tts_service.speak_now(
        text="Interrupt! Chat response",
        source="chat",
    )
    print(f"  割り込みのレスポンス: success={response.success}, queued={response.queued}")

    # キューを処理させる
    await asyncio.sleep(0.2)

    print("\nTTS サービスを停止中...")
    await tts_service.stop()
    print(f"  is_running: {tts_service.is_running()}")


async def demo_event_bus_integration() -> None:
    """発話イベントでのイベントバスの連携を見せる。"""
    print_header("イベントバスの連携")

    event_bus = AsyncEventBus()

    # 発話イベントを聞く外部のコンポーネントを模す
    speech_log: List[str] = []

    async def log_speech_started(event: SpeechStartedEvent) -> None:
        speech_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] 開始: {event.text}")

    async def log_speech_completed(event: SpeechCompletedEvent) -> None:
        status = "完了" if event.completed else "中断"
        speech_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {status}: {event.text}")

    event_bus.subscribe(SpeechStartedEvent, log_speech_started)
    event_bus.subscribe(SpeechCompletedEvent, log_speech_completed)

    print("発話イベントを発行中...")

    await event_bus.publish(
        SpeechStartedEvent(
            text="Hello viewers!",
            source="greeting",
            emotion=EmotionState(EmotionType.HAPPY, 0.8),
        )
    )

    await event_bus.publish(
        SpeechCompletedEvent(
            text="Hello viewers!",
            source="greeting",
            completed=True,
            duration_ms=800,
        )
    )

    await event_bus.publish(
        SpeechStartedEvent(
            text="Let me explain this...",
            source="commentary",
        )
    )

    await event_bus.publish(
        SpeechCompletedEvent(
            text="Let me explain this...",
            source="commentary",
            completed=False,  # 中断
            duration_ms=0,
        )
    )

    await asyncio.sleep(0.1)

    print("\n発話のログ（外部のリスナーから）:")
    for entry in speech_log:
        print(f"  {entry}")


async def main() -> None:
    """すべてのデモを実行する。"""
    print("\n" + "=" * 60)
    print("  AILoveShen Phase 2: TTS パイプラインのデモ")
    print("=" * 60)

    # ドメイン層のデモ
    demo_domain_value_objects()
    demo_emotion_style_service()
    demo_domain_events()

    # アプリケーション層のデモ
    await demo_use_case()

    # プレゼンテーション層のデモ
    await demo_tts_service()

    # 連携のデモ
    await demo_event_bus_integration()

    print_header("デモ完了")
    print("Phase 2 の TTS パイプラインの部品を一通り動かした")
    print("\n動かした部品:")
    print("  - ドメイン: SpeechResult, SpeechStatus")
    print("  - インフラ: EmotionStyleService, VoiceConfig")
    print("  - ドメイン: SpeechStartedEvent, SpeechCompletedEvent")
    print("  - アプリケーション: SpeakTextUseCase, SpeakTextRequest/Response")
    print("  - プレゼンテーション: キューを管理する TTSService")
    print("  - インフラ: モックの StyleBertVits2Client, SounddevicePlayer")
    print("\n本物の TTS は、依存をインストールして Style-Bert-VITS2 のサーバーを起動する:")
    print("  pip install ailoveshen[tts]")
    print("  python server_fastapi.py")


if __name__ == "__main__":
    asyncio.run(main())
