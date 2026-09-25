"""実況生成の入力ポート（ユースケースのインターフェース）。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateCommentaryResponse,
)


class IGenerateCommentary(ABC):
    """ゲーム実況のユースケースの入力ポート（メインループ）。"""

    @abstractmethod
    async def execute(self, request: GenerateCommentaryRequest) -> GenerateCommentaryResponse:
        """
        実況を生成する。

        Args:
            request: 実況のリクエストのパラメータ

        Returns:
            生成したテキスト、またはエラーを含むレスポンス。
        """
        ...
