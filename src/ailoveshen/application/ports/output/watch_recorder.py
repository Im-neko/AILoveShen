"""見張りの記録の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IWatchRecorder(ABC):
    """
    見張りのティックごとの記録（状態、質問、答え、確信度、費用）を残す出力ポート。
    19 §6 の評価（Jev と規則・Gemini 自身の確認との比較）と費用の比較の材料。
    """

    @abstractmethod
    def record(self, entry: dict[str, Any]) -> None:
        """1 件を残す。失敗しても例外を出さない（記録のためにプレイを止めない）。"""
        ...
