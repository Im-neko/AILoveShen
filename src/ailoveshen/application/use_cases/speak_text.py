"""発話のユースケースの実装。"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from loguru import logger

from ailoveshen.application.dto.speech_dto import (
    SpeakTextRequest,
    SpeakTextResponse,
)
from ailoveshen.application.ports.input.speak_text import ISpeakText
from ailoveshen.application.ports.output.audio_player import IAudioPlayer
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.speech_synthesizer import ISpeechSynthesizer
from ailoveshen.domain.events import SpeechCompletedEvent, SpeechStartedEvent
from ailoveshen.domain.value_objects import EmotionState, SpeechPriority, SpeechRequest


class SpeakTextUseCase(ISpeakText):
    """
    音声を合成して再生するユースケース。

    TTS の中心の業務ロジック。次のことをまとめる:
    - 感情を選ぶ（今の感情か、指定された感情）
    - アダプターで音声を合成する
    - アダプターで音声を再生する
    - ドメインイベントを発行する
    """

    def __init__(
        self,
        synthesizer: ISpeechSynthesizer,
        audio_player: IAudioPlayer,
        event_publisher: IEventPublisher,
        get_current_emotion: Callable[[], EmotionState],
    ) -> None:
        """
        依存を受け取ってユースケースを初期化する（依存性の注入）。

        Args:
            synthesizer: 音声合成のアダプター
            audio_player: 音声再生のアダプター
            event_publisher: ドメインイベントの発行器
            get_current_emotion: 今の感情の状態を返す関数
        """
        self._synthesizer = synthesizer
        self._audio_player = audio_player
        self._event_publisher = event_publisher
        self._get_current_emotion = get_current_emotion

        # 割り込みの扱い
        self._interrupt_event = asyncio.Event()
        self._current_request: Optional[SpeechRequest] = None

    async def execute(self, request: SpeakTextRequest) -> SpeakTextResponse:
        """
        発話のユースケースを実行する。

        流れ:
        1. リクエストを検証して準備する
        2. 感情を決める（今の感情か、指定された感情）
        3. 必要なら割り込む
        4. 音声を合成する
        5. 開始のイベントを発行する
        6. 音声を再生する
        7. 完了のイベントを発行する
        """
        try:
            # テキストを検証する
            text = request.text.strip()
            if not text:
                return SpeakTextResponse.error_response("Empty text")

            # 感情を決める
            emotion = request.emotion or self._get_current_emotion()

            # ドメインの値オブジェクトを作る
            speech_request = SpeechRequest(
                text=text,
                priority=request.priority,
                emotion=emotion,
                source=request.source,
            )

            # 割り込みの優先度を扱う
            if speech_request.should_interrupt() and self._audio_player.is_playing():
                logger.info(f"今の発話に割り込む: {text[:50]}...")
                self._audio_player.stop()
                self._interrupt_event.set()
                # 今の再生が止まるのを待つ
                await asyncio.sleep(0.1)

            self._current_request = speech_request
            self._interrupt_event.clear()

            # 音声を合成する
            logger.debug(f"合成する: {text[:50]}... [emotion={emotion.primary.value}]")
            audio_data = await self._synthesizer.synthesize(
                text=text,
                emotion=emotion,
                speaker_id=request.speaker_id,
                language=request.language,
            )

            # イベントのために長さを得る
            duration_ms = self._audio_player.get_duration_ms(audio_data)

            # 開始のイベントを発行する
            await self._event_publisher.publish(
                SpeechStartedEvent(
                    text=text,
                    source=request.source,
                    emotion=emotion,
                    duration_ms=duration_ms,
                )
            )

            # 音声を再生する
            logger.debug(f"音声を再生する: {duration_ms}ms")
            completed = await self._audio_player.play(
                audio_data,
                interrupt_event=self._interrupt_event,
            )

            # 完了のイベントを発行する
            await self._event_publisher.publish(
                SpeechCompletedEvent(
                    text=text,
                    source=request.source,
                    completed=completed,
                    duration_ms=duration_ms if completed else 0,
                )
            )

            self._current_request = None

            if completed:
                return SpeakTextResponse.ok(
                    message="Speech completed",
                    duration_ms=duration_ms,
                )
            else:
                return SpeakTextResponse.interrupted()

        except Exception as e:
            logger.error(f"発話に失敗した: {e}")
            self._current_request = None
            return SpeakTextResponse.error_response(str(e))

    def request_interrupt(self) -> None:
        """
        今の再生の中断を頼む。

        何も再生していなくても呼んでよい。
        """
        self._interrupt_event.set()
        self._audio_player.stop()

    def is_speaking(self) -> bool:
        """今話しているかを調べる。"""
        return self._audio_player.is_playing()

    def get_current_request(self) -> Optional[SpeechRequest]:
        """今再生している発話のリクエストを返す（なければ None）。"""
        return self._current_request
