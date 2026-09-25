"""テキスト生成の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class ITextGenerator(ABC):
    """
    LLM のテキスト生成の出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
    ) -> str:
        """
        プロンプトからテキストを生成する。

        Args:
            prompt: ユーザーのプロンプト
            system_instruction: システム指示（キャラクターの前提）

        Returns:
            生成したテキスト。モデルが使えるテキストを返さなかったとき（ブロックされた
            など）は空文字列。

        Raises:
            TextGenerationError: 生成に失敗したとき
        """
        ...

    @abstractmethod
    async def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_instruction: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        スキーマに従う JSON オブジェクトを生成する。

        Args:
            prompt: ユーザーのプロンプト
            schema: 期待するオブジェクトの JSON Schema（ただの dict。モデルに依存しない）
            system_instruction: システム指示（キャラクターの前提）

        Returns:
            パースした JSON オブジェクト。

        Raises:
            TextGenerationError: 生成に失敗したか、出力が JSON オブジェクトでないとき
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """
        生成器が持つリソースを解放する。

        後始末のときに呼ぶ。
        """
        ...
