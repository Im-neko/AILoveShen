"""見張りの記録を JSON Lines のファイルに残すアダプター。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder


class JsonlWatchRecorder(IWatchRecorder):
    """
    見張りのティックを 1 行 1 件で追記する。ファイルは実行ごとに 1 つ
    （`<dir>/watch-<開始時刻>.jsonl`）。書けなくてもプレイは止めない。
    """

    def __init__(self, directory: str) -> None:
        """
        Args:
            directory: 記録を置くディレクトリ（なければ作る）
        """
        started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._path = Path(directory) / f"watch-{started}.jsonl"

    @property
    def path(self) -> Path:
        """記録のファイル。"""
        return self._path

    def record(self, entry: dict[str, Any]) -> None:
        """1 件を追記する（時刻をつける）。"""
        line = {"at": datetime.now(timezone.utc).isoformat(), **entry}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning(f"見張りの記録を書けなかった（{self._path}）: {e}")
