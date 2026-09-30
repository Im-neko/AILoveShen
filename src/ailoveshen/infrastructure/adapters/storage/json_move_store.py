"""手の帳を JSON ファイルに保存するアダプター（docs/design/52）。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from ailoveshen.application.ports.output.move_store import IMoveStore
from ailoveshen.domain.value_objects import Move


class JsonMoveStore(IMoveStore):
    """
    手の帳を 1 つの JSON ファイルに保存する。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても
    最後に完全に保存した内容が残る。
    """

    def __init__(self, path: Path | str) -> None:
        """
        Args:
            path: JSON ファイル（ディレクトリは最初の保存のときに作る）
        """
        self._path = Path(path)

    def load(self) -> Optional[tuple[list[Move], int]]:
        """保存した手と次の id の番号。ファイルがまだなければ None。"""
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        moves = [
            Move(**{**m, "situation": tuple((k, v) for k, v in m.get("situation", {}).items())})
            for m in data["moves"]
        ]
        return moves, int(data["next_id"])

    def save(self, moves: Sequence[Move], next_id: int) -> None:
        """手の帳を保存する。"""
        data = {
            "moves": [{**asdict(m), "situation": dict(m.situation)} for m in moves],
            "next_id": next_id,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
