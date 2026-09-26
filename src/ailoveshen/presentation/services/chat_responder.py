"""ChatResponder: 配信のチャットのコメントに、1 つずつ声で返事をする。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

from loguru import logger

from ailoveshen.application.ports.output.chat_source import IChatSource
from ailoveshen.application.use_cases.readings import COMMAND, VIEWER, NameReadings
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.value_objects import ChatComment
from ailoveshen.presentation.services.llm_service import LLMService


class ChatResponder:
    """
    チャットのコメントを読み、返事を作って話す（docs/streaming.md）。

    - 返事は 1 つずつ作る。作っている間に来たコメントは、新しいものから `backlog` 件だけ
      取っておき、古いものは捨てる（多いときに遅れを積み上げない）
    - 返事と返事の間は `min_interval_seconds` 以上空ける
    - `!` で始まるコメント（ほかのボットのコマンド）には返事をしない。ただし `!yomi よみ`
      （`!読み` / `!よみ`）は、その人の名前の読みを覚えて短く応える（設計書 30）
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
        readings: NameReadings | None = None,
    ) -> None:
        """
        Args:
            llm: 返事を作る
            session: プレイ中のセッション（始まる前は None）
            say: 返事の出し先（TTS とログ）
            min_interval_seconds: 返事と返事の最短の間
            backlog: 返事を待つコメントを何件まで取っておくか
            clock: 時計（テストで差し替える）
            readings: 視聴者の名前の読みの辞書（None: `!yomi` も読まない）
        """
        self._llm = llm
        self._session = session
        self._say = say
        self._interval = min_interval_seconds
        self._queue: deque[ChatComment] = deque(maxlen=max(1, backlog))
        self._arrived = asyncio.Event()
        self._clock = clock
        self._last_reply_at: float | None = None
        self._readings = readings
        self._acks: list[asyncio.Task[None]] = []
        self.dropped = 0  # 返事をせずに捨てたコメントの数

    async def run(self, source: IChatSource) -> None:
        """キャンセルされるまで、読んで返事をする。"""
        reader = asyncio.create_task(self._read(source))
        reader.add_done_callback(_report_reader_error)
        try:
            await self._answer_forever()
        finally:
            reader.cancel()

    def accept(self, comment: ChatComment) -> None:
        """コメントを 1 つ受け取る（返事の順番を待つ）。"""
        logger.info(f"[chat] {comment.user_name}: {comment.message}")
        if comment.message.lstrip().startswith("!"):
            self._command(comment)
            return
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1
            logger.info(f"返事が追いつかないので古いコメントを飛ばした（{self.dropped} 件目）")
        self._queue.append(comment)
        self._arrived.set()

    def _command(self, comment: ChatComment) -> None:
        """`!yomi よみ`: 名前の読みを覚えて、その読みで呼んで応える。"""
        match = COMMAND.match(comment.message)
        if self._readings is None or match is None:
            return
        learned = self._readings.learn(comment.user_name, match.group(1), VIEWER)
        if learned is None:
            return
        # 読み上げで名前が読みに置き換わる。返事の順番は待たない（短い決まった一言）
        task = asyncio.ensure_future(self._say(f"{comment.user_name}さん、読み方覚えたよ！"))
        self._acks = [t for t in self._acks if not t.done()] + [task]

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
            try:
                answered = await self.answer_next()
            except Exception as e:  # noqa: BLE001 - 1 つの返事の失敗でチャットを止めない
                logger.opt(exception=e).warning(f"返事に失敗した（{type(e).__name__}: {e}）")
                continue
            if not answered:
                await self._arrived.wait()

    async def _read(self, source: IChatSource) -> None:
        async for comment in source.comments():
            self.accept(comment)
        logger.error("チャットの読み込みが終わった（もうコメントは届かない）")


def _report_reader_error(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        error = task.exception()
        logger.opt(exception=error).error(
            f"チャットの読み込みが止まった（{type(error).__name__}: {error}）"
        )
