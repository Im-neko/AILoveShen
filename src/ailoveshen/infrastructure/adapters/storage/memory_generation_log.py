"""LLM の呼び出しの記録をメモリに持つアダプター（デバッグ用）。"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

from ailoveshen.application.ports.output.generation_log import IGenerationLog


class InMemoryGenerationLog(IGenerationLog):
    """直近の呼び出しだけを持つ（再起動で消える）。ゲームと会話の生成器で 1 つを共有する。"""

    def __init__(self, size: int = 50) -> None:
        """
        Raises:
            ValueError: size が正でないとき。
        """
        if size <= 0:
            raise ValueError(f"size must be positive, got {size}")
        self._entries: deque[dict[str, Any]] = deque(maxlen=size)
        self._lock = threading.Lock()
        self._next_id = 1

    def record(self, entry: dict[str, Any]) -> None:
        """1 件を残し、通し番号をつける。"""
        with self._lock:
            self._entries.append({"id": self._next_id, **entry})
            self._next_id += 1

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """新しい順に最大 limit 件。"""
        with self._lock:
            return list(reversed(self._entries))[: max(0, limit)]
