"""ChatResponder: 配信のチャットのコメントに、1 つずつ声で返事をする。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

from loguru import logger

from ailoveshen.application.ports.output.chat_source import IChatSource
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.value_objects import ChatComment
from ailoveshen.presentation.services.llm_service import LLMService


class ChatResponder:
    """
    チャットのコメントを読み、返事を作って話す（docs/streaming.md）。

    - 返事は 1 つずつ作る。作っている間に来たコメントは、新しいものから `backlog` 件だけ
      取っておき、古いものは捨てる（多いときに遅れを積み上げない）
    - 返事と返事の間は `min_interval_seconds` 以上空ける
    - `!` で始まるコメント（ほかのボットのコマンド）には返事をしない
    - 返事はプレイ中のセッションを見て作る。視聴者の頼みは中目標になることがある
      （`LLMService.generate_response`）
    """

    def __init__(
        self,
        llm: LLMService,
        session: Callable[[], PlaySession | None],
        say: Callable[[str], Awaitable[None]],
        min_interval_seconds: float = 5.0,
        backlog: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            llm: 返事を作る
            session: プレイ中のセッション（始まる前は None）
            say: 返事の出し先（TTS とログ）
            min_interval_seconds: 返事と返事の最短の間
            backlog: 返事を待つコメントを何件まで取っておくか
            clock: 時計（テストで差し替える）
        """
        self._llm = llm
        self._session = session
        self._say = say
        self._interval = min_interval_seconds
        self._queue: deque[ChatComment] = deque(maxlen=max(1, backlog))
        self._arrived = asyncio.Event()
        self._clock = clock
        self._last_reply_at: float | None = None
        self.dropped = 0  # 返事をせずに捨てたコメントの数

    async def run(self, source: IChatSource) -> None:
        """キャンセルされるまで、読んで返事をする。"""
        reader = asyncio.create_task(self._read(source))
        try:
            await self._answer_forever()
        finally:
            reader.cancel()

    def accept(self, comment: ChatComment) -> None:
        """コメントを 1 つ受け取る（返事の順番を待つ）。"""
        logger.info(f"[chat] {comment.user_name}: {comment.message}")
        if comment.message.lstrip().startswith("!"):
            return
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1
            logger.info(f"返事が追いつかないので古いコメントを飛ばした（{self.dropped} 件目）")
        self._queue.append(comment)
        self._arrived.set()

    async def answer_next(self) -> bool:
        """待っているコメントのうち一番古いものに返事をする。なければ偽。"""
        if not self._queue:
            self._arrived.clear()
            return False
        if self._last_reply_at is not None:
            wait = self._last_reply_at + self._interval - self._clock()
            if wait > 0:
                await asyncio.sleep(wait)
        comment = self._queue.popleft()
        started = self._clock()
        reply = await self._llm.generate_response(
            comment.user_name, comment.message, user_id=comment.user_id, session=self._session()
        )
        self._last_reply_at = self._clock()
        if not reply:
            logger.warning(f"{comment.user_name} への返事を作れなかった")
            return True
        logger.info(f"[reply] ({self._last_reply_at - started:.1f}s) {reply}")
        await self._say(reply)
        return True

    async def _answer_forever(self) -> None:
        while True:
            if not await self.answer_next():
                await self._arrived.wait()

    async def _read(self, source: IChatSource) -> None:
        async for comment in source.comments():
            self.accept(comment)
