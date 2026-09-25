"""地図を画像に描く出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from ailoveshen.domain.value_objects import Screenshot


class IMapRenderer(ABC):
    """
    ブリッジの地図のデータ（`IMinecraftBridge.map`）を、マス目のラベルつきの画像にする
    （docs/design/25_builds.md §3）。Gemini はその画像を見て、置き場所をマス目で選ぶ。
    """

    @abstractmethod
    def render(self, map_data: dict[str, Any]) -> Optional[Screenshot]:
        """画像にする。描けなければ None。"""
        ...
