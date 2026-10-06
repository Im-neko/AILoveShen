"""配信への応援（フォロー、サブスクなど）を受け取る出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ailoveshen.domain.value_objects import SupportEvent


class ISupportSource(ABC):
    """
    配信への応援（Twitch のフォロー、サブスク、ギフト、ビッツ、レイドなど）を、届いた順に流す出力ポート。
    接続が切れたらつなぎ直し、閉じるまで流し続ける（例外で終わらない）。
    """

    @abstractmethod
    def supports(self) -> AsyncIterator[SupportEvent]:
        """届いた応援。close() まで終わらない。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """接続を閉じる。"""
        ...
