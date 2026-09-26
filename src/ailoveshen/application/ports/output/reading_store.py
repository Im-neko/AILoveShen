"""視聴者の名前の読みの保存の出力ポート（docs/design/30_name_readings.md）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class NameReading:
    """名前 1 つの読み。source: viewer（本人が言った）か guess（Gemini の推測）。"""

    name: str
    reading: str
    source: str
    updated_at: str


class IReadingStore(ABC):
    """
    名前の読みの辞書を、再起動をまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> tuple[NameReading, ...]:
        """保存した読み。何も保存していなければ空。"""
        ...

    @abstractmethod
    def save(self, readings: tuple[NameReading, ...]) -> None:
        """辞書の全部を保存する（変わるたびに）。"""
        ...
