"""アプリケーション層の出力ポート（インターフェース）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

if TYPE_CHECKING:
    from ailoveshen.domain.events import DomainEvent

T = TypeVar("T", bound="DomainEvent")

# イベントハンドラーの型エイリアス
EventHandler = Callable[[T], Awaitable[None]]


class IEventPublisher(ABC):
    """
    イベント発行の出力ポート。

    ドメインイベントを発行するインターフェースを定める。
    具体的な実装はインフラ層が用意する。
    """

    @abstractmethod
    async def publish(self, event: "DomainEvent") -> None:
        """
        ドメインイベントを発行する。

        Args:
            event: 発行するドメインイベント。
        """
        ...

    @abstractmethod
    async def publish_all(self, events: list["DomainEvent"]) -> None:
        """
        複数のドメインイベントを発行する。

        Args:
            events: 発行するドメインイベントのリスト。
        """
        ...


class IEventSubscriber(ABC):
    """
    イベント購読の出力ポート。

    ドメインイベントを購読するインターフェースを定める。
    """

    @abstractmethod
    def subscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> Callable[[], None]:
        """
        特定の型のイベントを購読する。

        Args:
            event_type: 購読するイベントの型。
            handler: イベントを処理する非同期関数。

        Returns:
            購読を解除する関数。呼ぶと購読を外す。
        """
        ...

    @abstractmethod
    def unsubscribe(
        self,
        event_type: type[T],
        handler: EventHandler[T],
    ) -> bool:
        """
        特定の型のイベントの購読を解除する。

        Args:
            event_type: 購読を解除するイベントの型。
            handler: 外すハンドラー。

        Returns:
            ハンドラーが見つかって外せたら True、そうでなければ False。
        """
        ...
