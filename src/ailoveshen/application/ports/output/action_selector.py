"""行動選択の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ailoveshen.domain.value_objects import ActionDecision, Candidate


class IActionSelector(ABC):
    """
    次の行動を選ぶ、高速で型のある判断モデル（Jev）の出力ポート。

    このインターフェースはアプリケーション層で定義する。
    """

    @abstractmethod
    async def select(
        self,
        state: dict[str, Any],
        actions: Sequence[Candidate],
        instructions: str,
    ) -> ActionDecision:
        """
        与えられた状態について、与えられた行動からちょうど 1 つ選ぶ。

        Args:
            state: JSON にできるゲームの状態
            actions: 候補の行動（2 つ以上）
            instructions: 何を重視して選ぶか

        Raises:
            ActionSelectionError: モデルの呼び出しに失敗したとき
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """選択器が持つリソースを解放する。"""
        ...
