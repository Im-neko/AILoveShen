"""手の帳の保存の出力ポート（docs/design/52）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Optional

from ailoveshen.domain.value_objects import Move


class IMoveStore(ABC):
    """
    手の帳を、再起動とワールドの作り直しをまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> Optional[tuple[list[Move], int]]:
        """保存した手と次の id の番号。何も保存していなければ None。"""
        ...

    @abstractmethod
    def save(self, moves: Sequence[Move], next_id: int) -> None:
        """手の帳を保存する（変更のたびに）。"""
        ...
