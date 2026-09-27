"""自分について話したことの JSON 保存（docs/design/40_small_talk.md）。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from loguru import logger

from ailoveshen.application.ports.output.self_fact_store import ISelfFactStore, SelfFact


class JsonSelfFactStore(ISelfFactStore):
    """
    自分について話したことを 1 つの JSON ファイルに保存する（手で直してもよい形: 話題ごとの文）。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても
    最後に完全に保存した内容が残る。
    """

    def __init__(self, path: Path | str) -> None:
        """
        Args:
            path: JSON ファイル（ディレクトリは最初の保存のときに作る）
        """
        self._path = Path(path)

    def load(self) -> tuple[SelfFact, ...]:
        """保存したもの。ファイルがまだなければ空。"""
        if not self._path.exists():
            return ()
        data = json.loads(self._path.read_text(encoding="utf-8"))
        facts = tuple(
            SelfFact(
                topic=topic,
                fact=str(f["fact"]),
                said_to=str(f.get("said_to", "")),
                updated_at=str(f.get("updated_at", "")),
            )
            for topic, f in data.get("facts", {}).items()
        )
        logger.info(f"自分について話したことを {self._path} から読み込んだ（{len(facts)} 件）")
        return facts

    def save(self, facts: tuple[SelfFact, ...]) -> None:
        """全部を保存する。"""
        data = {
            "facts": {f.topic: {k: v for k, v in asdict(f).items() if k != "topic"} for f in facts}
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
