"""外した手の台帳を JSON ファイルに保存するアダプター。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.hand_store import IHandStore


class JsonHandStore(IHandStore):
    """
    外した手の台帳を 1つの JSON ファイルに保存する。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても最後に完全に保存した内容が残る。
    読めないファイルは捨てて空から始める（台帳は、なくても動く記録）。
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def load(self) -> Optional[dict[str, Any]]:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"外した手の台帳を読めなかった（空から始める）: {self._path}: {e}")
            return None
        return data if isinstance(data, dict) else None

    def save(self, snapshot: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
