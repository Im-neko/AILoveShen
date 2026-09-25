"""チャット返答生成の入力ポート（ユースケースのインターフェース）。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.llm_dto import (
    GenerateResponseRequest,
    GenerateResponseResponse,
)


class IGenerateResponse(ABC):
    """チャット返答のユースケースの入力ポート（サブループ / 割り込み）。"""

    @abstractmethod
    async def execute(self, request: GenerateResponseRequest) -> GenerateResponseResponse:
        """
        チャットへの返答を生成する。

        Args:
            request: チャット返答のリクエストのパラメータ

        Returns:
            生成したテキスト、またはエラーを含むレスポンス。
        """
        ...
