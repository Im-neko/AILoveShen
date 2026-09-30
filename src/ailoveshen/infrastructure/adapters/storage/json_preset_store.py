"""喋らせる文章のプリセットを JSON ファイルに保存するアダプター。"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from ailoveshen.application.ports.output.preset_store import IPresetStore, SpeechPreset


class JsonPresetStore(IPresetStore):
    """
    プリセットを 1 つの JSON ファイルに保存する（手で直してもよい形: 並びどおりの一覧）。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても
    最後に完全に保存した内容が残る。
    """

    def __init__(self, path: Path | str) -> None:
        """
        Args:
            path: JSON ファイル（ディレクトリは最初の保存のときに作る）
        """
        self._path = Path(path)

    def load(self) -> tuple[SpeechPreset, ...]:
        """保存したプリセット。ファイルがまだなければ空。"""
        if not self._path.exists():
            return ()
        data = json.loads(self._path.read_text(encoding="utf-8"))
        presets = tuple(
            SpeechPreset(
                id=str(p["id"]),
                title=str(p.get("title", "")),
                text=str(p["text"]),
                category=str(p.get("category", "")),
            )
            for p in data.get("presets", [])
        )
        logger.info(f"喋らせる文章のプリセットを {self._path} から読み込んだ（{len(presets)} 件）")
        return presets

    def save(self, presets: tuple[SpeechPreset, ...]) -> None:
        """一覧の全部を保存する。"""
        data = {
            "presets": [
                {"id": p.id, "title": p.title, "text": p.text, "category": p.category}
                for p in presets
            ]
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
