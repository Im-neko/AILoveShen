"""発話の入力ポート（ユースケースのインターフェース）。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.speech_dto import (
    SpeakTextRequest,
    SpeakTextResponse,
)


class ISpeakText(ABC):
    """
    音声合成のユースケースの入力ポート。

    プレゼンテーション層はこのインターフェースで音声合成を頼む。
    依存性逆転の原則に従い、上位のモジュールは具体的な実装ではなく抽象に依存する。
    """

    @abstractmethod
    async def execute(self, request: SpeakTextRequest) -> SpeakTextResponse:
        """
        発話のユースケースを実行する。

        このメソッドは次のことをする:
        1. 話すときの感情を決める（今の感情か、指定された感情）
        2. 設定された TTS サービスで音声を合成する
        3. 設定された音声プレイヤーで再生する
        4. 発話の開始と完了のドメインイベントを発行する

        Args:
            request: 発話のリクエストのパラメータ

        Returns:
            成否とエラーの情報を含む発話の結果。
        """
        ...
