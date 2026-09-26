"""教訓帳の保存の出力ポート（docs/design/35）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Optional

from ailoveshen.domain.value_objects import Lesson


class ILessonStore(ABC):
    """
    教訓帳を、再起動とワールドの作り直しをまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> Optional[tuple[list[Lesson], int]]:
        """保存した教訓と次の id の番号。何も保存していなければ None。"""
        ...

    @abstractmethod
    def save(self, lessons: Sequence[Lesson], next_id: int) -> None:
        """教訓帳を保存する（変更のたびに）。"""
        ...
