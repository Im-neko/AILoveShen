"""TTS モジュールのファクトリー（Composition Root）。"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.speech_synthesizer import ISpeechSynthesizer
from ailoveshen.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.domain.value_objects import EmotionState, EmotionType
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


ENGINES = ("style_bert_vits2", "irodori")


def engine_of(config: Dict[str, Any]) -> str:
    """設定の読み上げの方式（tts.engine。既定は style_bert_vits2）。"""
    engine = str(config.get("engine") or "style_bert_vits2").strip().lower()
    if engine not in ENGINES:
        raise ValueError(f"tts.engine must be one of {', '.join(ENGINES)}, got {engine!r}")
    return engine


def describe_engine(config: Dict[str, Any]) -> str:
    """ログと起動の失敗の説明に使う、方式・つなぎ先・声。"""
    if engine_of(config) == "irodori":
        c = config.get("irodori", {})
        return f"Irodori-TTS {c.get('host', 'localhost')}:{c.get('port', 8088)}、声 {c.get('voice', 'shen')}"
    server = config.get("server", {})
    voice = config.get("voice", {}).get("model_name")
    return f"Style-Bert-VITS2 {server.get('host')}:{server.get('port')}、モデル {voice}"


def _parse_emotion_captions(config_map: Optional[Dict[str, str]]) -> Dict[EmotionType, str]:
    result: Dict[EmotionType, str] = {}
    for name, caption in (config_map or {}).items():
        try:
            result[EmotionType(name.lower())] = str(caption)
        except ValueError:
            pass
    return result


def create_synthesizer(config: Dict[str, Any]) -> ISpeechSynthesizer:
    """tts.engine の合成器を作る（接続はまだしない）。"""
    if engine_of(config) == "irodori":
        from ailoveshen.infrastructure.adapters.tts.irodori_tts_client import IrodoriTtsClient

        c = config.get("irodori", {})
        return IrodoriTtsClient(
            host=c.get("host", "localhost"),
            port=int(c.get("port", 8088)),
            timeout_seconds=float(c.get("timeout_seconds", 60.0)),
            voice=c.get("voice", "shen"),
            speed=float(c.get("speed", 1.0)),
            num_steps=c.get("num_steps"),
            seed=c.get("seed"),
            cfg_scale_text=c.get("cfg_scale_text"),
            cfg_scale_speaker=c.get("cfg_scale_speaker"),
            emotion_captions=(
                _parse_emotion_captions(c.get("emotion_captions")) if c.get("emotion") else None
            ),
            caption_min_intensity=float(c.get("caption_min_intensity", 0.6)),
            api_key=os.environ.get("IRODORI_API_KEY", ""),
            lora_adapter=str(c.get("lora_adapter") or ""),
        )

    server_config = config.get("server", {})
    voice_config = config.get("voice", {})
    synthesis_config = config.get("synthesis", {})
    # 感情からスタイルへの対応を作る（Style-Bert-VITS2 固有）
    emotion_style_service = EmotionStyleService(
        style_map=_parse_emotion_style_map(config.get("emotion_style_map")),
        default_style=voice_config.get("default_style", "Neutral"),
    )
    return StyleBertVits2Client(
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


def create_tts_service(
    config: Dict[str, Any],
    event_publisher: IEventPublisher,
    get_current_emotion: Optional[Callable[[], EmotionState]] = None,
    pronounce: Optional[Callable[[str], str]] = None,
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
        pronounce: 合成の前にテキストを置き換える（視聴者の名前の読み）。None なら何もしない

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
    queue_config = config.get("queue", {})
    audio_config = config.get("audio", {})

    synthesizer = create_synthesizer(config)

    # 音の再生は読み上げるときだけ要る（合成器だけを使う道具は sounddevice なしで動く）
    from ailoveshen.infrastructure.adapters.audio.sounddevice_player import SounddevicePlayer

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
        pronounce=pronounce,
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
    pronounce: Optional[Callable[[str], str]] = None,
) -> TTSService:
    """
    TTS サービスを作り、TTS サーバーにつなぐ。

    サービスを作って TTS サーバーとの接続まで済ませる、便利な関数。

    Args:
        config: TTS の設定の辞書
        event_publisher: ドメインイベントの発行先
        get_current_emotion: 今の感情状態を返す呼び出し可能オブジェクト
        pronounce: 合成の前にテキストを置き換える（視聴者の名前の読み）

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
        pronounce=pronounce,
    )

    # 接続のため、内部の合成器に触る
    # カプセル化を少し破るが、セットアップには必要
    use_case = tts_service._use_case  # type: ignore[attr-defined]
    if hasattr(use_case, "_synthesizer"):
        await use_case._synthesizer.connect()  # type: ignore[attr-defined]

    # サービスを開始する
    await tts_service.start()

    return tts_service
