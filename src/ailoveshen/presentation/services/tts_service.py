"""TTS service for presentation layer."""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from ailoveshen.application.dto.speech_dto import SpeakTextRequest, SpeakTextResponse
from ailoveshen.application.ports.input.speak_text import ISpeakText
from ailoveshen.domain.value_objects import EmotionState, SpeechPriority


class TTSService:
    """
    Presentation layer service for TTS.

    Provides a simple interface for other components to request speech.
    Manages a priority queue for speech requests.

    Features:
    - Queue-based speech processing
    - Priority handling (interrupt requests bypass queue)
    - Graceful shutdown
    """

    def __init__(
        self,
        speak_text_use_case: ISpeakText,
        max_queue_size: int = 10,
    ) -> None:
        """
        Initialize TTS service.

        Args:
            speak_text_use_case: Use case for speaking text
            max_queue_size: Maximum number of items in queue
        """
        self._use_case = speak_text_use_case
        self._max_queue_size = max_queue_size
        self._queue: asyncio.PriorityQueue[tuple[int, SpeakTextRequest]] = (
            asyncio.PriorityQueue(maxsize=max_queue_size)
        )
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._sequence = 0  # For FIFO ordering within same priority

    async def start(self) -> None:
        """
        Start the TTS service processing loop.

        Safe to call multiple times - will only start once.
        """
        if self._running:
            logger.debug("TTS service already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("TTS service started")

    async def stop(self) -> None:
        """
        Stop the TTS service gracefully.

        Waits for current speech to complete, then stops.
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

        # Clear the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        logger.info("TTS service stopped")

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
        Request speech synthesis and playback.

        Interrupt priority requests are processed immediately.
        Other requests are queued for processing.

        Args:
            text: Text to speak
            priority: Speech priority level
            emotion: Override emotion (None = use current emotion)
            source: Source identifier (e.g., "commentary", "chat")
            language: Language code
            speaker_id: Speaker ID for multi-speaker models

        Returns:
            SpeakTextResponse indicating success/failure/queued status
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

        # Handle interrupt priority - process immediately
        if priority >= SpeechPriority.INTERRUPT:
            logger.debug(f"Processing interrupt speech: {text[:30]}...")
            return await self._use_case.execute(request)

        # Queue for normal processing
        try:
            # Use negative priority for priority queue (lower number = higher priority)
            # Add sequence number for FIFO within same priority
            self._sequence += 1
            priority_key = (-priority.value, self._sequence)

            self._queue.put_nowait((priority_key, request))
            logger.debug(
                f"Queued speech [{priority.name}]: {text[:30]}... "
                f"(queue size: {self._queue.qsize()})"
            )
            return SpeakTextResponse.queued_response()

        except asyncio.QueueFull:
            logger.warning(
                f"Speech queue full ({self._max_queue_size}), "
                f"dropping request: {text[:30]}..."
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
        Speak immediately, interrupting any current speech.

        Convenience method for interrupt priority speech.

        Args:
            text: Text to speak
            emotion: Override emotion
            source: Source identifier
            language: Language code
            speaker_id: Speaker ID

        Returns:
            SpeakTextResponse with result
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
        """Get current queue size."""
        return self._queue.qsize()

    async def wait_until_idle(self) -> None:
        """
        Wait until every queued speech request has finished playing.

        Unlike get_queue_size() == 0, this also waits for the request that
        is currently being synthesized or played.
        """
        await self._queue.join()

    def is_running(self) -> bool:
        """Check if the service is running."""
        return self._running

    async def _process_loop(self) -> None:
        """
        Main processing loop.

        Continuously processes queued speech requests.
        """
        logger.debug("TTS processing loop started")

        while self._running:
            try:
                # Wait for next request with timeout
                try:
                    _, request = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                # Process the request
                logger.debug(f"Processing queued speech: {request.text[:30]}...")
                try:
                    await self._use_case.execute(request)
                finally:
                    self._queue.task_done()

            except asyncio.CancelledError:
                logger.debug("TTS processing loop cancelled")
                break
            except Exception as e:
                logger.error(f"TTS processing error: {e}")
                await asyncio.sleep(0.5)  # Prevent tight error loop

        logger.debug("TTS processing loop ended")
