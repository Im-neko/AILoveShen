"""LLM の呼び出しの記録（デバッグ用）の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IGenerationLog(ABC):
    """
    LLM の呼び出しごとの記録（用途、考える深さ、思考の要約、出力、トークン）を持つ出力ポート。
    デバッグのエンドポイント（目標ボードの /api/debug/gemini）が読む。
    """

    @abstractmethod
    def record(self, entry: dict[str, Any]) -> None:
        """1 件を残す（古いものは消える）。"""
        ...

    @abstractmethod
    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """新しい順に最大 limit 件。"""
        ...
