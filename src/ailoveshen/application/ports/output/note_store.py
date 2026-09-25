"""自分のメモの保存の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.entities import Notebook
from ailoveshen.domain.value_objects import Note


@dataclass(frozen=True)
class SavedNotes:
    """メモ帳のうち保存するもの: 残っているメモと、次の id の番号。"""

    notes: tuple[Note, ...]
    next_id: int


class INoteStore(ABC):
    """
    自分のメモを、再起動をまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> Optional[SavedNotes]:
        """保存したメモ。何も保存していなければ None。"""
        ...

    @abstractmethod
    def save(self, notebook: Notebook) -> None:
        """メモ帳を保存する（変更のたびに）。"""
        ...
