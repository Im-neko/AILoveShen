"""配信のチャットを読む出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ailoveshen.domain.value_objects import ChatComment


class IChatSource(ABC):
    """
    配信のチャット（Twitch など）のコメントを、届いた順に流す出力ポート。
    接続が切れたらつなぎ直し、閉じるまで流し続ける（例外で終わらない）。
    """

    @abstractmethod
    def comments(self) -> AsyncIterator[ChatComment]:
        """届いたコメント。close() まで終わらない。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """接続を閉じる。"""
        ...
