"""プロンプト組み立ての出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.domain.value_objects import CharacterProfile, GenerationContext


class IPromptBuilder(ABC):
    """
    LLM のプロンプトを組み立てる出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def build_system_prompt(self, character: CharacterProfile) -> str:
        """キャラクターを説明するシステム指示を組み立てる。"""
        ...

    @abstractmethod
    def build_commentary_prompt(self, context: GenerationContext) -> str:
        """ゲーム実況のプロンプトを組み立てる。"""
        ...

    @abstractmethod
    def build_chat_response_prompt(
        self,
        user_name: str,
        message: str,
        context: GenerationContext,
        takes_requests: bool = False,
        previous_error: str = "",
    ) -> str:
        """
        視聴者のチャットへの返答のプロンプトを組み立てる。

        Args:
            user_name: 視聴者
            message: 視聴者のチャットのメッセージ
            context: 配信者が今していることと、最近の会話
            takes_requests: 返答で頼みを中目標として受けてよいか（JSON で出力する）
            previous_error: 前回の返答で受けた頼みが使えなかった理由
        """
        ...
