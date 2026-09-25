"""LLM の呼び出しの記録をメモリに持つアダプター（デバッグ用）。"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Optional

from ailoveshen.application.ports.output.generation_log import IGenerationLog


class InMemoryGenerationLog(IGenerationLog):
    """直近の呼び出しだけを持つ（再起動で消える）。ゲームと会話の生成器で 1 つを共有する。"""

    def __init__(self, size: int = 50, images: int = 10) -> None:
        """
        Raises:
            ValueError: size が正でないとき。
        """
        if size <= 0:
            raise ValueError(f"size must be positive, got {size}")
        self._entries: deque[dict[str, Any]] = deque(maxlen=size)
        self._lock = threading.Lock()
        self._next_id = 1
        # 画像は呼び出しの記録とは別に、直近の数枚だけ（記録を読むたびに画像を送らない）
        self._images: deque[tuple[str, bytes, str]] = deque(maxlen=max(1, images))
        self._next_image = 1

    def record(self, entry: dict[str, Any]) -> None:
        """1 件を残し、通し番号をつける。"""
        with self._lock:
            self._entries.append({"id": self._next_id, **entry})
            self._next_id += 1

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """新しい順に最大 limit 件。"""
        with self._lock:
            return list(reversed(self._entries))[: max(0, limit)]

    def record_image(self, data: bytes, mime_type: str) -> str:
        """画像を残し、id を返す。"""
        with self._lock:
            image_id = f"img{self._next_image}"
            self._next_image += 1
            self._images.append((image_id, data, mime_type))
            return image_id

    def image(self, image_id: str) -> Optional[tuple[bytes, str]]:
        """残した画像。もう消えていれば None。"""
        with self._lock:
            for i, data, mime in self._images:
                if i == image_id:
                    return data, mime
        return None
