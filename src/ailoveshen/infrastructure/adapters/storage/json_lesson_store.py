"""教訓帳を JSON ファイルに保存するアダプター（docs/design/35）。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from ailoveshen.application.ports.output.lesson_store import ILessonStore
from ailoveshen.domain.value_objects import Lesson


class JsonLessonStore(ILessonStore):
    """
    教訓帳を 1 つの JSON ファイルに保存する。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても
    最後に完全に保存した内容が残る。
    """

    def __init__(self, path: Path | str) -> None:
        """
        Args:
            path: JSON ファイル（ディレクトリは最初の保存のときに作る）
        """
        self._path = Path(path)

    def load(self) -> Optional[tuple[list[Lesson], int]]:
        """保存した教訓と次の id の番号。ファイルがまだなければ None。"""
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        lessons = [
            Lesson(**{**n, "situation": tuple((k, v) for k, v in n.get("situation", {}).items())})
            for n in data["lessons"]
        ]
        return lessons, int(data["next_id"])

    def save(self, lessons: Sequence[Lesson], next_id: int) -> None:
        """教訓帳を保存する。"""
        data = {
            "lessons": [{**asdict(n), "situation": dict(n.situation)} for n in lessons],
            "next_id": next_id,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
