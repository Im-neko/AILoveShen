"""音声合成の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.domain.value_objects import EmotionState


class ISpeechSynthesizer(ABC):
    """
    音声合成の出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        emotion: EmotionState,
        speaker_id: int = 0,
        language: str = "JP",
    ) -> bytes:
        """
        テキストから音声を合成する。

        Args:
            text: 合成するテキスト
            emotion: 表す感情。アダプターがエンジン固有のスタイルに対応づける。
            speaker_id: 複数話者モデルの話者 ID
            language: 言語コード（"JP"、"EN"、"ZH"）

        Returns:
            音声データのバイト列（WAV 形式）

        Raises:
            SynthesisError: 合成に失敗したとき
        """
        ...

    @abstractmethod
    async def connect(self) -> None:
        """
        音声合成サービスに接続する。

        合成の前に呼ぶ。

        Raises:
            ConnectionError: 接続に失敗したとき
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """
        音声合成サービスとの接続を閉じる。

        後始末のときに呼ぶ。
        """
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """
        音声合成サービスに接続しているかを調べる。

        Returns:
            接続していて合成できるなら True。
        """
        ...
