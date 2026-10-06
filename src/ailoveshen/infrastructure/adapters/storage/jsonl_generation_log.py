"""LLM の呼び出しの記録を、メモリ（デバッグの画面）と JSON Lines のファイルの両方に残すアダプター。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.generation_log import IGenerationLog
from ailoveshen.infrastructure.adapters.storage.memory_generation_log import InMemoryGenerationLog


class JsonlGenerationLog(IGenerationLog):
    """
    呼び出しを 1 行 1 件で追記する（プロンプト全文、出力、道具の呼び出し、トークン）。ファイルは
    実行ごとに 1 つ（`<dir>/gen-<開始時刻>.jsonl`）。画面に出す直近の分と画像は
    `InMemoryGenerationLog` が持つ（画像はファイルに書かない）。書けなくても生成は止めない。
    """

    def __init__(self, directory: str, size: int = 50, keep_days: int = 3) -> None:
        """
        Args:
            directory: 記録を置くディレクトリ（なければ作る）
            size: 画面に出す直近の件数
            keep_days: これより古い記録のファイルは、開始のときに消す

        Raises:
            ValueError: size か keep_days が正でないとき。
        """
        if keep_days <= 0:
            raise ValueError(f"keep_days must be positive, got {keep_days}")
        self._memory = InMemoryGenerationLog(size)
        started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._path = Path(directory) / f"gen-{started}.jsonl"
        self._prune(self._path.parent, keep_days)

    @property
    def path(self) -> Path:
        """記録のファイル。"""
        return self._path

    def record(self, entry: dict[str, Any]) -> None:
        """メモリに残し、ファイルに追記する。"""
        self._memory.record(entry)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning(f"LLM の呼び出しの記録を書けなかった（{self._path}）: {e}")

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """新しい順に最大 limit 件（メモリの分）。"""
        return self._memory.recent(limit)

    def record_image(self, data: bytes, mime_type: str) -> str:
        """画像を残し（メモリだけ）、id を返す。"""
        return self._memory.record_image(data, mime_type)

    def image(self, image_id: str) -> Optional[tuple[bytes, str]]:
        """残した画像。もう消えていれば None。"""
        return self._memory.image(image_id)

    @staticmethod
    def _prune(directory: Path, keep_days: int) -> None:
        """keep_days より古い `gen-*.jsonl` を消す。"""
        if not directory.is_dir():
            return
        limit = time.time() - keep_days * 86400
        for old in directory.glob("gen-*.jsonl"):
            try:
                if old.stat().st_mtime < limit:
                    old.unlink()
            except OSError as e:
                logger.warning(f"古い LLM の記録を消せなかった（{old}）: {e}")
