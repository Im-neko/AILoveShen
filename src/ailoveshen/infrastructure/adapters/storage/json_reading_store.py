"""視聴者の名前の読みを JSON ファイルに保存するアダプター。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from loguru import logger

from ailoveshen.application.ports.output.reading_store import IReadingStore, NameReading


class JsonReadingStore(IReadingStore):
    """
    名前の読みを 1 つの JSON ファイルに保存する（手で直してもよい形: 名前ごとの読み）。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても
    最後に完全に保存した内容が残る。
    """

    def __init__(self, path: Path | str) -> None:
        """
        Args:
            path: JSON ファイル（ディレクトリは最初の保存のときに作る）
        """
        self._path = Path(path)

    def load(self) -> tuple[NameReading, ...]:
        """保存した読み。ファイルがまだなければ空。"""
        if not self._path.exists():
            return ()
        data = json.loads(self._path.read_text(encoding="utf-8"))
        readings = tuple(
            NameReading(
                name=name,
                reading=str(r["reading"]),
                source=str(r.get("source", "viewer")),
                updated_at=str(r.get("updated_at", "")),
            )
            for name, r in data.get("readings", {}).items()
        )
        logger.info(f"名前の読みを {self._path} から読み込んだ（{len(readings)} 件）")
        return readings

    def save(self, readings: tuple[NameReading, ...]) -> None:
        """辞書の全部を保存する。"""
        data = {
            "readings": {
                r.name: {k: v for k, v in asdict(r).items() if k != "name"} for r in readings
            }
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
