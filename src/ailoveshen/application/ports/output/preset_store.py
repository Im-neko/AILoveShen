"""喋らせる文章のプリセットの保存の出力ポート（docs/design/54_admin_panel.md）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechPreset:
    """喋らせる文章 1 つ: 見出し、本文、分類（挨拶など。空でもよい）。"""

    id: str
    title: str
    text: str
    category: str = ""


class IPresetStore(ABC):
    """
    プリセットの一覧を、再起動をまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> tuple[SpeechPreset, ...]:
        """保存したプリセット（並びどおり）。何も保存していなければ空。"""
        ...

    @abstractmethod
    def save(self, presets: tuple[SpeechPreset, ...]) -> None:
        """一覧の全部を保存する（変わるたびに）。"""
        ...
