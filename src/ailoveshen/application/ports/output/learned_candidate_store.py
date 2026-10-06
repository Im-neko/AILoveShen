"""覚えた候補の保存の出力ポート（docs/design/52）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Optional

from ailoveshen.domain.value_objects import LearnedCandidate


class ILearnedCandidateStore(ABC):
    """
    覚えた候補を、再起動とワールドの作り直しをまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> Optional[tuple[list[LearnedCandidate], int]]:
        """保存した手と次の id の番号。何も保存していなければ None。"""
        ...

    @abstractmethod
    def save(self, learned: Sequence[LearnedCandidate], next_id: int) -> None:
        """覚えた候補を保存する（変更のたびに）。"""
        ...
