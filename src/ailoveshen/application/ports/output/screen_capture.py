"""配信の画面を撮る出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ailoveshen.domain.value_objects import Screenshot


class IScreenCapture(ABC):
    """
    配信の画面（ゲームのソース）を 1 枚撮る出力ポート（docs/design/23_screen_vision.md）。
    撮れないとき（つながらない、ソースがない、遅い）は None を返し、例外は出さない:
    画像なしでプレイを続ける。
    """

    @abstractmethod
    async def capture(self) -> Optional[Screenshot]:
        """1 枚撮る。撮れなければ None。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """接続を閉じる。"""
        ...
