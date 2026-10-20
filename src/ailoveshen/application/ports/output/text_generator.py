"""テキスト生成の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Optional

from ailoveshen.domain.value_objects import Screenshot

# プロンプトの中の「ここまでは次の呼び出しでも同じ」の印（docs/design/127 §5）。変わらない部分を前に、
# 変わる部分を後ろに並べ、境目に 1 つ置く。キャッシュの境目を指定できるモデル（LiteLLM 経由の Claude）は
# ここで分けて cache_control を付け、ほかのアダプターは印を取り除く（取り除いたあとは何も変わらない）
CACHE_BREAK = "\ue000"
# `generate_json` が JSON Schema の説明を入れる場所（docs/design/177）。印がなければ本文の最後に足す。スキーマは
# 呼び出しごとに変わらない用途だけ、変わらない部分の最後（最初の CACHE_BREAK の手前）に置く: 本文の後ろに足すと、
# 毎回違う状態の後ろにあって、キャッシュに入らない
SCHEMA_SLOT = "\ue001"


def split_cache_breaks(prompt: str) -> list[str]:
    """印で分けた部分（空の部分は捨てる）。印がなければ全体の 1 つ。"""
    return [part for part in prompt.split(CACHE_BREAK) if part.strip()] or [prompt.replace(CACHE_BREAK, "")]


def with_rules(rules: Optional[str], prompt: str) -> str:
    """
    用途ごとの決まり（性格・話し方を含む）を本文の先頭に置き、キャッシュの境目で区切る。
    システム指示には共通の文だけを入れるので（設計書 175）、用途ごとの決まりはここに置く。
    """
    return f"{rules}{CACHE_BREAK}{prompt}" if rules else prompt


def strip_cache_breaks(prompt: str) -> str:
    """印を取り除いたプロンプト。"""
    return prompt.replace(CACHE_BREAK, "")


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
        images: Sequence[Screenshot] = (),
    ) -> dict[str, Any]:
        """
        スキーマに従う JSON オブジェクトを生成する。

        Args:
            prompt: ユーザーのプロンプト
            schema: 期待するオブジェクトの JSON Schema（ただの dict。モデルに依存しない）
            system_instruction: システム指示（キャラクターの前提）
            purpose: 用途の名前（generate と同じ）
            images: プロンプトに添える画像（配信の画面。docs/design/23）

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
        images: Sequence[Screenshot] = (),
    ) -> ToolChoice:
        """
        道具を必ず 1 つ選ばせる（設計書 21）。呼び出しごとに独立で、会話の履歴は持ち越さない。

        Args:
            prompt: ユーザーのプロンプト（状況と直近の道具の記録を含む）
            tools: 選べる道具
            system_instruction: システム指示（キャラクターの前提）
            purpose: 用途の名前（generate と同じ）
            images: プロンプトに添える画像（配信の画面）

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
