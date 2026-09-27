"""配信者が自分について話したことの保存の出力ポート（docs/design/40_small_talk.md）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SelfFact:
    """自分について話したこと 1 つ。topic ごとに 1 つ（同じ話題は新しいもので置き換える）。"""

    topic: str
    fact: str
    said_to: str
    updated_at: str


class ISelfFactStore(ABC):
    """
    自分について話したことを、再起動とワールドの作り直しをまたいで保つ出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> tuple[SelfFact, ...]:
        """保存したもの。何も保存していなければ空。"""
        ...

    @abstractmethod
    def save(self, facts: tuple[SelfFact, ...]) -> None:
        """全部を保存する（変わるたびに）。"""
        ...
