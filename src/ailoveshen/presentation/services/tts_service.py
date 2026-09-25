"""プレゼンテーション層の TTS サービス。"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from ailoveshen.application.dto.speech_dto import SpeakTextRequest, SpeakTextResponse
from ailoveshen.application.ports.input.speak_text import ISpeakText
from ailoveshen.domain.value_objects import EmotionState, SpeechPriority


class TTSService:
    """
    TTS のプレゼンテーション層のサービス。

    他のコンポーネントが発話を頼むための簡単な窓口。
    発話のリクエストを優先度付きのキューで管理する。

    特徴:
    - キューによる発話の処理
    - 優先度の扱い（割り込みのリクエストはキューを通らない）
    - 穏やかな停止
    """

    def __init__(
        self,
        speak_text_use_case: ISpeakText,
        max_queue_size: int = 10,
    ) -> None:
        """
        TTS サービスを初期化する。

        Args:
            speak_text_use_case: テキストを話すユースケース
            max_queue_size: キューに入る最大の数
        """
        self._use_case = speak_text_use_case
        self._max_queue_size = max_queue_size
        self._queue: asyncio.PriorityQueue[tuple[int, SpeakTextRequest]] = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._sequence = 0  # 同じ優先度の中で先入れ先出しにするため

    async def start(self) -> None:
        """
        TTS サービスの処理ループを始める。

        何度呼んでもよい。始めるのは 1回だけ。
        """
        if self._running:
            logger.debug("TTS サービスはすでに動いている")
            return

        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("TTS サービスを開始した")

    async def stop(self) -> None:
        """
        TTS サービスを穏やかに止める。

        今の発話が終わるのを待ってから止める。
        """
        if not self._running:
            return

        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # キューを空にする
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        logger.info("TTS サービスを停止した")

    async def speak(
        self,
        text: str,
        priority: SpeechPriority = SpeechPriority.NORMAL,
        emotion: Optional[EmotionState] = None,
        source: str = "unknown",
        language: str = "JP",
        speaker_id: int = 0,
    ) -> SpeakTextResponse:
        """
        音声の合成と再生を頼む。

        割り込みの優先度のリクエストはすぐに処理する。
        それ以外のリクエストはキューに入れて処理する。

        Args:
            text: 話すテキスト
            priority: 発話の優先度
            emotion: 上書きする感情（None なら今の感情を使う）
            source: 出どころの識別子（例: "commentary"、"chat"）
            language: 言語コード
            speaker_id: 複数話者のモデルでの話者 ID

        Returns:
            成功、失敗、キューに入ったかを示す SpeakTextResponse
        """
        if not text or not text.strip():
            return SpeakTextResponse.error_response("Empty text")

        request = SpeakTextRequest(
            text=text.strip(),
            priority=priority,
            emotion=emotion,
            source=source,
            language=language,
            speaker_id=speaker_id,
        )

        # 割り込みの優先度: すぐに処理する
        if priority >= SpeechPriority.INTERRUPT:
            logger.debug(f"割り込みの発話を処理する: {text[:30]}...")
            return await self._use_case.execute(request)

        # 通常の処理のためにキューに入れる
        try:
            # 優先度付きキューには優先度を負にして入れる（小さい数ほど優先度が高い）
            # 同じ優先度の中で先入れ先出しにするため、通し番号を加える
            self._sequence += 1
            priority_key = (-priority.value, self._sequence)

            self._queue.put_nowait((priority_key, request))
            logger.debug(
                f"発話をキューに入れた [{priority.name}]: {text[:30]}... "
                f"（キューの長さ: {self._queue.qsize()}）"
            )
            return SpeakTextResponse.queued_response()

        except asyncio.QueueFull:
            logger.warning(
                f"発話のキューがいっぱい（{self._max_queue_size}）なので、"
                f"リクエストを捨てる: {text[:30]}..."
            )
            return SpeakTextResponse.error_response("Queue full")

    async def speak_now(
        self,
        text: str,
        emotion: Optional[EmotionState] = None,
        source: str = "unknown",
        language: str = "JP",
        speaker_id: int = 0,
    ) -> SpeakTextResponse:
        """
        今の発話に割り込んで、すぐに話す。

        割り込みの優先度で話すための便利なメソッド。

        Args:
            text: 話すテキスト
            emotion: 上書きする感情
            source: 出どころの識別子
            language: 言語コード
            speaker_id: 話者 ID

        Returns:
            結果の SpeakTextResponse
        """
        return await self.speak(
            text=text,
            priority=SpeechPriority.INTERRUPT,
            emotion=emotion,
            source=source,
            language=language,
            speaker_id=speaker_id,
        )

    def get_queue_size(self) -> int:
        """今のキューの長さを返す。"""
        return self._queue.qsize()

    async def wait_until_idle(self) -> None:
        """
        キューに入れた発話のリクエストがすべて再生し終わるまで待つ。

        get_queue_size() == 0 と違い、今合成中か再生中のリクエストも待つ。
        """
        await self._queue.join()

    def is_running(self) -> bool:
        """サービスが動いているかを返す。"""
        return self._running

    async def _process_loop(self) -> None:
        """
        メインの処理ループ。

        キューに入った発話のリクエストを処理し続ける。
        """
        logger.debug("TTS の処理ループを開始した")

        while self._running:
            try:
                # 次のリクエストをタイムアウト付きで待つ
                try:
                    _, request = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                # リクエストを処理する
                logger.debug(f"キューの発話を処理する: {request.text[:30]}...")
                try:
                    await self._use_case.execute(request)
                finally:
                    self._queue.task_done()

            except asyncio.CancelledError:
                logger.debug("TTS の処理ループがキャンセルされた")
                break
            except Exception as e:
                logger.error(f"TTS の処理でエラー: {e}")
                await asyncio.sleep(0.5)  # エラーで空回りしないようにする

        logger.debug("TTS の処理ループを終えた")
