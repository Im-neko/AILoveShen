"""自分のメモを JSON ファイルに保存するアダプター。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from loguru import logger

from ailoveshen.application.ports.output.note_store import INoteStore, SavedNotes
from ailoveshen.domain.entities import Notebook
from ailoveshen.domain.value_objects import Note, NoteKind


class JsonNoteStore(INoteStore):
    """
    自分のメモを 1つの JSON ファイルに保存する。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても
    最後に完全に保存した内容が残る。
    """

    def __init__(self, path: Path | str) -> None:
        """
        ストアを初期化する。

        Args:
            path: JSON ファイル（ディレクトリは最初の保存のときに作る）
        """
        self._path = Path(path)

    def load(self) -> Optional[SavedNotes]:
        """保存したメモ。ファイルがまだなければ None。"""
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        saved = SavedNotes(
            notes=tuple(Note(**{**n, "kind": NoteKind(n["kind"])}) for n in data["notes"]),
            next_id=int(data["next_id"]),
        )
        logger.info(f"メモを {self._path} から読み込んだ（{len(saved.notes)} 件）")
        return saved

    def save(self, notebook: Notebook) -> None:
        """メモ帳を保存する。"""
        data = {
            "notes": [{**asdict(n), "kind": n.kind.value} for n in notebook.notes],
            "next_id": notebook.next_id,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
