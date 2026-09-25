"""音声再生の出力ポート。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Optional


class IAudioPlayer(ABC):
    """
    音声再生の出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    async def play(
        self,
        audio_data: bytes,
        interrupt_event: Optional[asyncio.Event] = None,
    ) -> bool:
        """
        音声データを再生する。

        Args:
            audio_data: 再生する音声データ（WAV 形式）
            interrupt_event: 割り込みを監視するイベント（任意）。
                           セットされたら、すぐに再生を止める。

        Returns:
            最後まで再生したら True、割り込まれたら False。

        Raises:
            AudioPlaybackError: エラーで再生に失敗したとき。
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """
        今の再生をすぐに止める。

        何も再生していなくても呼んでよい。
        """
        ...

    @abstractmethod
    def is_playing(self) -> bool:
        """
        今、音声を再生しているかを調べる。

        Returns:
            再生中なら True。
        """
        ...

    @abstractmethod
    def get_duration_ms(self, audio_data: bytes) -> int:
        """
        音声データの長さをミリ秒で返す。

        Args:
            audio_data: 音声データ（WAV 形式）

        Returns:
            長さ（ミリ秒）。

        Raises:
            AudioPlaybackError: 音声データが不正なとき。
        """
        ...
