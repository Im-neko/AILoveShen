"""非同期の Pub/Sub メッセージングのイベントバスの実装。"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import (
    IEventPublisher,
    IEventSubscriber,
)

if TYPE_CHECKING:
    from ailoveshen.domain.events import DomainEvent

T = TypeVar("T", bound="DomainEvent")

# イベントハンドラーの型エイリアス
EventHandler = Callable[[T], Awaitable[None]]


class AsyncEventBus(IEventPublisher, IEventSubscriber):
    """
    Pub/Sub パターンを実装する非同期のイベントバス。

    特徴:
    - 型安全なイベントの購読
    - 非同期のイベント処理
    - エラーの隔離（1つのハンドラーが失敗しても他に影響しない）
    - スレッドセーフな購読と購読解除
    - デバッグ用のログ
    """

    def __init__(self) -> None:
        """イベントバスを初期化する。"""
        self._handlers: dict[type, list[EventHandler]] = defaultdict(list)
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    async def publish(self, event: "DomainEvent") -> None:
        """
        登録済みのすべてのハンドラーにドメインイベントを発行する。

        Args:
            event: 発行するドメインイベント。
        """
        event_type = type(event)

        # 競合を避けるため、ロックを取ってハンドラーのスナップショットを取る
        async with self._async_lock:
            handlers = list(self._handlers.get(event_type, []))

        if not handlers:
            logger.debug(f"{event_type.__name__} のハンドラーは登録されていない")
            return

        logger.debug(f"{event_type.__name__} を {len(handlers)} 個のハンドラーに発行する")

        # すべてのハンドラーを並行に実行する
        tasks = [self._safe_execute(handler, event) for handler in handlers]
        await asyncio.gather(*tasks)

    async def publish_all(self, events: list["DomainEvent"]) -> None:
        """
        複数のドメインイベントを発行する。

        Args:
            events: 発行するドメインイベントのリスト。
        """
        for event in events:
            await self.publish(event)

    def subscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> Callable[[], None]:
        """
        特定のイベントの型を購読する。

        スレッドセーフ: どのスレッドからでも呼べる。

        Args:
            event_type: 購読するイベントの型。
            handler: イベントを処理する非同期関数。

        Returns:
            購読を解除する関数。
        """
        with self._sync_lock:
            self._handlers[event_type].append(handler)
        logger.debug(f"ハンドラーが {event_type.__name__} を購読した")

        # 購読を解除する関数を返す
        def unsubscribe() -> None:
            self.unsubscribe(event_type, handler)

        return unsubscribe

    def unsubscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> bool:
        """
        特定のイベントの型の購読を解除する。

        スレッドセーフ: どのスレッドからでも呼べる。

        Args:
            event_type: 購読を解除するイベントの型。
            handler: 外すハンドラー。

        Returns:
            ハンドラーが見つかって外せたら True。
        """
        with self._sync_lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
                logger.debug(f"ハンドラーが {event_type.__name__} の購読を解除した")
                return True
        return False

    def clear(self) -> None:
        """すべての購読を外す。"""
        with self._sync_lock:
            self._handlers.clear()
        logger.debug("イベントの購読をすべて外した")

    def handler_count(self, event_type: type[T] | None = None) -> int:
        """
        登録済みのハンドラーの数を返す。

        Args:
            event_type: 渡したときは、この型のハンドラーだけを数える。

        Returns:
            ハンドラーの数。
        """
        with self._sync_lock:
            if event_type is not None:
                return len(self._handlers.get(event_type, []))
            return sum(len(handlers) for handlers in self._handlers.values())

    async def _safe_execute(
        self,
        handler: EventHandler,
        event: "DomainEvent",
    ) -> None:
        """
        エラーを隔離してハンドラーを実行する。

        Args:
            handler: 実行するハンドラー関数。
            event: ハンドラーに渡すイベント。
        """
        try:
            await handler(event)
        except Exception as e:
            logger.exception(f"{type(event).__name__} のイベントハンドラーでエラー: {e}")


class SyncEventBus(IEventPublisher, IEventSubscriber):
    """
    テストや簡単な用途のための同期のイベントバス。

    ハンドラーを asyncio.run() で包んで同期で実行する。
    """

    def __init__(self) -> None:
        """同期のイベントバスを初期化する。"""
        self._async_bus = AsyncEventBus()

    async def publish(self, event: "DomainEvent") -> None:
        """イベントを発行する。"""
        await self._async_bus.publish(event)

    async def publish_all(self, events: list["DomainEvent"]) -> None:
        """複数のイベントを発行する。"""
        await self._async_bus.publish_all(events)

    def subscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> Callable[[], None]:
        """イベントの型を購読する。"""
        return self._async_bus.subscribe(event_type, handler)

    def unsubscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> bool:
        """イベントの型の購読を解除する。"""
        return self._async_bus.unsubscribe(event_type, handler)

    def publish_sync(self, event: "DomainEvent") -> None:
        """
        イベントを同期で発行する。

        イベントループがすでに動いていてもいなくても正しく動く。
        """
        try:
            loop = asyncio.get_running_loop()
            # イベントループが動いている: コルーチンを予約する
            future = asyncio.run_coroutine_threadsafe(self.publish(event), loop)
            future.result()
        except RuntimeError:
            # イベントループが動いていない: 作る
            asyncio.run(self.publish(event))


# イベントハンドラー用の便利なデコレーター
def event_handler(event_type: type[T]):
    """
    関数をイベントハンドラーとして印を付けるデコレーター。

    Usage:
        @event_handler(ChatMessageReceived)
        async def handle_chat_message(event: ChatMessageReceived):
            ...
    """

    def decorator(func: EventHandler[T]) -> EventHandler[T]:
        func._event_type = event_type  # type: ignore[attr-defined]
        return func

    return decorator
