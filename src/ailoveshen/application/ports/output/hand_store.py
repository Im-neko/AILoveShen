"""外した手の台帳の保存の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class IHandStore(ABC):
    """
    外した手の台帳（`HandLedger`）を、再起動をまたいで保つ出力ポート。

    再起動で台帳が消えると、外した手を LLM がまた選び直し、同じ失敗を回数分やり直す（設計書 184）。
    このインターフェースはアプリケーション層で定義する。インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def load(self) -> Optional[dict[str, Any]]:
        """保存した台帳の中身（`HandLedger.snapshot()` の形）。まだなければ None。"""
        raise NotImplementedError

    @abstractmethod
    def save(self, snapshot: dict[str, Any]) -> None:
        """台帳の中身を保存する。"""
        raise NotImplementedError
