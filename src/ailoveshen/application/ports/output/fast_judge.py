"""高頻度の判断モデル（Jev）の出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ailoveshen.domain.value_objects import FastQuestion, FastVerdict


class IFastJudge(ABC):
    """
    状態について、はい/いいえ・選択・段階の質問にまとめて速く答えるモデルの出力ポート
    （設計書 19 §11、21 §5）。質問は配信者（Gemini）かコードが書く。答えで起きることは
    アプリケーション層が決める（止める・起こす・知らせる）。完了の判定には使わない。
    """

    @abstractmethod
    async def ask(self, state: dict[str, Any], questions: Sequence[FastQuestion]) -> FastVerdict:
        """
        1 回の呼び出しで、すべての質問に答えさせる。

        Raises:
            ActionSelectionError: リクエストが失敗したとき
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """クライアントが持つリソースを解放する。"""
        ...
