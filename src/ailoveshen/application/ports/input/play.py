"""プレイの入力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.game_dto import PlayStepReport
from ailoveshen.domain.entities import PlaySession


class IStartPlay(ABC):
    """入力ポート: LLM で家を設計し、そのプランをブリッジに渡して、セッションを始める。"""

    @abstractmethod
    async def execute(self) -> PlaySession:
        """
        家を設計し、建築プランを送る。

        Raises:
            TextGenerationError: LLM が正しい設計図を作れないとき
            GameBridgeError: ブリッジがプランを拒否したとき
        """
        ...


class IAdvancePlay(ABC):
    """入力ポート: 1 ステップ進める（決める時なら新しい目標、次に行動を 1 つ）。"""

    @abstractmethod
    async def execute(self, session: PlaySession) -> PlayStepReport:
        """観測し、決める時なら新しい目標を設定し、候補を 1 つ選んで実行する。"""
        ...
