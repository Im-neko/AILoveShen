"""TTS モジュールのファクトリー（Composition Root）。"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.domain.value_objects import EmotionState, EmotionType
from ailoveshen.infrastructure.adapters.audio.sounddevice_player import (
    SounddevicePlayer,
)
from ailoveshen.infrastructure.adapters.tts.emotion_style_service import EmotionStyleService
from ailoveshen.infrastructure.adapters.tts.style_bert_vits2_client import (
    StyleBertVits2Client,
)
from ailoveshen.presentation.services.tts_service import TTSService


def _default_emotion_provider() -> EmotionState:
    """既定の感情の提供元。中立の状態を返す。"""
    return EmotionState(primary=EmotionType.NEUTRAL, intensity=0.5)


def _parse_emotion_style_map(
    config_map: Optional[Dict[str, str]],
) -> Optional[Dict[EmotionType, str]]:
    """
    設定の形式の感情とスタイルの対応を、EmotionType をキーにした対応に変換する。

    Args:
        config_map: 感情の名前（文字列）からスタイル名への対応

    Returns:
        EmotionType からスタイル名への対応。config_map が None なら None
    """
    if not config_map:
        return None

    result = {}
    for emotion_str, style in config_map.items():
        try:
            emotion_type = EmotionType(emotion_str.lower())
            result[emotion_type] = style
        except ValueError:
            # 知らない感情の種類は飛ばす
            pass

    return result


def create_tts_service(
    config: Dict[str, Any],
    event_publisher: IEventPublisher,
    get_current_emotion: Optional[Callable[[], EmotionState]] = None,
) -> TTSService:
    """
    依存をすべてつないだ TTS サービスを作る。

    TTS モジュールの Composition Root。
    クリーンアーキテクチャに沿って、すべてのコンポーネントを作ってつなぐ。

    Args:
        config: TTS の設定の辞書（YAML の config["tts"]）
        event_publisher: ドメインイベントの発行先
        get_current_emotion: 今の感情状態を返す呼び出し可能オブジェクト。
                            None なら既定の中立の感情を使う。

    Returns:
        設定済みで、すぐ使える TTSService

    Example:
        ```python
        from ailoveshen.infrastructure.config import Settings
        from ailoveshen.infrastructure.events import AsyncEventBus

        settings = Settings()
        event_bus = AsyncEventBus()
        tts_config = settings.get("tts", {})

        tts_service = create_tts_service(
            config=tts_config,
            event_publisher=event_bus,
        )

        await tts_service.start()
        await tts_service.speak("Hello!")
        await tts_service.stop()
        ```
    """
    # 設定の各セクションを既定値付きで取り出す
    server_config = config.get("server", {})
    voice_config = config.get("voice", {})
    synthesis_config = config.get("synthesis", {})
    queue_config = config.get("queue", {})
    audio_config = config.get("audio", {})
    emotion_style_map_config = config.get("emotion_style_map")

    # 感情からスタイルへの対応を作る（Style-Bert-VITS2 固有）
    emotion_style_map = _parse_emotion_style_map(emotion_style_map_config)
    emotion_style_service = EmotionStyleService(
        style_map=emotion_style_map,
        default_style=voice_config.get("default_style", "Neutral"),
    )

    # インフラのアダプターを作る
    synthesizer = StyleBertVits2Client(
        host=server_config.get("host", "localhost"),
        port=server_config.get("port", 5000),
        timeout_seconds=server_config.get("timeout_seconds", 30.0),
        model_name=voice_config.get("model_name", "default"),
        sdp_ratio=synthesis_config.get("sdp_ratio", 0.2),
        noise=synthesis_config.get("noise", 0.6),
        noisew=synthesis_config.get("noisew", 0.8),
        length=synthesis_config.get("length", 1.0),
        emotion_style_service=emotion_style_service,
    )

    audio_player = SounddevicePlayer(
        device=audio_config.get("device"),
        blocksize=audio_config.get("blocksize", 1024),
    )

    # 渡された感情の提供元を使う。なければ既定のもの
    emotion_provider = get_current_emotion or _default_emotion_provider

    # ユースケースを作る
    speak_text_use_case = SpeakTextUseCase(
        synthesizer=synthesizer,
        audio_player=audio_player,
        event_publisher=event_publisher,
        get_current_emotion=emotion_provider,
    )

    # プレゼンテーション層のサービスを作る
    return TTSService(
        speak_text_use_case=speak_text_use_case,
        max_queue_size=queue_config.get("max_size", 10),
    )


async def create_and_connect_tts_service(
    config: Dict[str, Any],
    event_publisher: IEventPublisher,
    get_current_emotion: Optional[Callable[[], EmotionState]] = None,
) -> TTSService:
    """
    TTS サービスを作り、TTS サーバーにつなぐ。

    サービスを作って TTS サーバーとの接続まで済ませる、便利な関数。

    Args:
        config: TTS の設定の辞書
        event_publisher: ドメインイベントの発行先
        get_current_emotion: 今の感情状態を返す呼び出し可能オブジェクト

    Returns:
        接続して開始した TTSService

    Raises:
        ConnectionError: TTS サーバーへの接続に失敗したとき
    """
    # サービスを作る
    tts_service = create_tts_service(
        config=config,
        event_publisher=event_publisher,
        get_current_emotion=get_current_emotion,
    )

    # 接続のため、内部の合成器に触る
    # カプセル化を少し破るが、セットアップには必要
    use_case = tts_service._use_case  # type: ignore[attr-defined]
    if hasattr(use_case, "_synthesizer"):
        await use_case._synthesizer.connect()  # type: ignore[attr-defined]

    # サービスを開始する
    await tts_service.start()

    return tts_service
