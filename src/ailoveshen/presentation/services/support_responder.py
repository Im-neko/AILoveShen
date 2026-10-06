"""SupportResponder: 配信への応援（フォロー、サブスク、ギフト、ビッツ、レイド）に、お礼を言う。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence

from loguru import logger

from ailoveshen.application.ports.output.support_source import ISupportSource
from ailoveshen.application.use_cases.support_thanks import thanks_for
from ailoveshen.domain.events import DomainEvent, SupportReceivedEvent
from ailoveshen.domain.value_objects import SupportEvent, SupportKind


class SupportResponder:
    """
    応援を読み、お礼を 1 つずつ話す（docs/design/71_support_thanks.md）。

    - お礼は `thanks_for` の決まった言い方から選ぶ（LLM を待たない）。話す前に `SupportReceivedEvent` を
      出す: アバターがそれで喜ぶ（表情としぐさ）
    - フォローは、少し待って溜まった分を 1 回にまとめる（「A さん、B さん、C さんほか 2 人、フォローありがとう」）。
      ほかの応援は 1 件ずつ
    - 同じ人のフォローは `dedupe_seconds` の間、2 回目以降を言わない（フォロー・解除の繰り返し）
    - お礼とお礼の間は `min_interval_seconds` 以上空ける。溜まりすぎたら古いものから捨てる
    - `ignore` のアカウント（配信者自身、ボット）の応援には何も言わない
    """

    def __init__(
        self,
        say: Callable[[str], Awaitable[None]],
        publish: Callable[[DomainEvent], Awaitable[None]],
        min_interval_seconds: float = 3.0,
        batch_seconds: float = 2.0,
        backlog: int = 20,
        dedupe_seconds: float = 3600.0,
        ignore: Iterable[str] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            say: お礼の出し先（TTS とログ）
            publish: ドメインイベントの出し先（アバターが受ける）
            min_interval_seconds: お礼とお礼の最短の間
            batch_seconds: 最初の応援を受けてから、フォローをまとめるために待つ秒数
            backlog: お礼を待つ応援を何件まで取っておくか（溢れたら古いものから捨てる）
            dedupe_seconds: 同じ人のフォローを繰り返して言わない間
            ignore: 何も言わないアカウントのログイン名
            clock: 時計（テストで差し替える）
        """
        self._say = say
        self._publish = publish
        self._interval = min_interval_seconds
        self._batch = batch_seconds
        self._queue: deque[SupportEvent] = deque(maxlen=max(1, backlog))
        self._arrived = asyncio.Event()
        self._dedupe = dedupe_seconds
        self._followed: dict[str, float] = {}
        self._ignore = {i.strip().lower() for i in ignore if i.strip()}
        self._clock = clock
        self._last_said_at: float | None = None
        self._last = ""
        self.dropped = 0
        self.thanked = 0

    async def run(self, sources: Sequence[ISupportSource]) -> None:
        """キャンセルされるまで、読んでお礼を言う。"""
        readers = [asyncio.create_task(self._read(s)) for s in sources]
        for reader in readers:
            reader.add_done_callback(_report_reader_error)
        try:
            await self._thank_forever()
        finally:
            for reader in readers:
                reader.cancel()

    def accept(self, support: SupportEvent) -> None:
        """応援を 1 件受け取る（お礼の順番を待つ）。"""
        login = (support.login or "").lower()
        logger.info(f"[support] {support.kind.value}: {support.user_name}")
        if login in self._ignore:
            return
        if support.kind == SupportKind.FOLLOW and login:
            now = self._clock()
            seen = self._followed.get(login)
            if seen is not None and now - seen < self._dedupe:
                logger.info(f"[support] {support.user_name} のフォローは、さっき言ったので飛ばす")
                return
            self._followed[login] = now
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1
            logger.info(f"お礼が追いつかないので古い応援を飛ばした（{self.dropped} 件目）")
        self._queue.append(support)
        self._arrived.set()

    async def _read(self, source: ISupportSource) -> None:
        async for support in source.supports():
            self.accept(support)

    def _next_group(self) -> list[SupportEvent]:
        """次に言う分: 先頭が、フォローならいま溜まっているフォローすべて、それ以外なら 1 件。"""
        first = self._queue.popleft()
        if first.kind != SupportKind.FOLLOW:
            return [first]
        group = [first]
        rest: deque[SupportEvent] = deque(maxlen=self._queue.maxlen)
        for s in self._queue:
            (group if s.kind == SupportKind.FOLLOW else rest).append(s)
        self._queue = rest
        return group

    async def _thank_forever(self) -> None:
        while True:
            await self._arrived.wait()
            self._arrived.clear()
            # フォローをまとめるために、少し待つ
            if self._batch > 0 and self._queue and self._queue[0].kind == SupportKind.FOLLOW:
                await asyncio.sleep(self._batch)
            while await self.thank_next():
                pass

    async def thank_next(self) -> bool:
        """次の分（フォローは溜まった分すべて）にお礼を言う。待っているものがなければ偽。"""
        if not self._queue:
            return False
        await self._thank(self._next_group())
        return True

    async def _thank(self, group: list[SupportEvent]) -> None:
        if self._last_said_at is not None:
            wait = self._interval - (self._clock() - self._last_said_at)
            if wait > 0:
                await asyncio.sleep(wait)
        text = thanks_for(group, last=self._last)
        try:
            await self._publish(SupportReceivedEvent(support=group[0], count=len(group)))
            await self._say(text)
        except Exception as e:  # noqa: BLE001 - 1 件の失敗で、お礼を止めない
            logger.warning(f"お礼を言えなかった（{type(e).__name__}: {e}）")
        self._last = text
        self._last_said_at = self._clock()
        self.thanked += 1


def _report_reader_error(task: asyncio.Task[object]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.opt(exception=task.exception()).error("応援の読み取りが止まった")
