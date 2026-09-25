"""テキスト生成の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ToolSpec:
    """モデルに呼ばせる道具 1 つ: 名前、説明、引数の JSON Schema（モデルに依存しない dict）。"""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolChoice:
    """モデルが呼んだ道具: 名前と引数（検証する前のもの）。"""

    name: str
    args: dict[str, Any]


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
        purpose: Optional[str] = None,
    ) -> str:
        """
        プロンプトからテキストを生成する。

        Args:
            prompt: ユーザーのプロンプト
            system_instruction: システム指示（キャラクターの前提）
            purpose: 用途の名前（例: "reply"、"town"）。アダプターはこれで考える深さを決める
                （設計書 19 §7）。None なら既定

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
        purpose: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        スキーマに従う JSON オブジェクトを生成する。

        Args:
            prompt: ユーザーのプロンプト
            schema: 期待するオブジェクトの JSON Schema（ただの dict。モデルに依存しない）
            system_instruction: システム指示（キャラクターの前提）
            purpose: 用途の名前（generate と同じ）

        Returns:
            パースした JSON オブジェクト。

        Raises:
            TextGenerationError: 生成に失敗したか、出力が JSON オブジェクトでないとき
        """
        ...

    @abstractmethod
    async def choose_tool(
        self,
        prompt: str,
        tools: Sequence[ToolSpec],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> ToolChoice:
        """
        道具を必ず 1 つ選ばせる（設計書 21）。呼び出しごとに独立で、会話の履歴は持ち越さない。

        Args:
            prompt: ユーザーのプロンプト（状況と直近の道具の記録を含む）
            tools: 選べる道具
            system_instruction: システム指示（キャラクターの前提）
            purpose: 用途の名前（generate と同じ）

        Returns:
            モデルが呼んだ道具と引数（検証する前のもの）

        Raises:
            TextGenerationError: 生成に失敗したか、道具を呼ばなかったとき
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """
        生成器が持つリソースを解放する。

        後始末のときに呼ぶ。
        """
        ...
