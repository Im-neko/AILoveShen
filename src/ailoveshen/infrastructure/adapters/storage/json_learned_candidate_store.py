"""覚えた候補を JSON ファイルに保存するアダプター（docs/design/52）。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from ailoveshen.application.ports.output.learned_candidate_store import ILearnedCandidateStore
from ailoveshen.domain.value_objects import LearnedCandidate


class JsonLearnedCandidateStore(ILearnedCandidateStore):
    """
    覚えた候補を 1 つの JSON ファイルに保存する。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても
    最後に完全に保存した内容が残る。
    """

    def __init__(self, path: Path | str) -> None:
        """
        Args:
            path: JSON ファイル（ディレクトリは最初の保存のときに作る）
        """
        self._path = Path(path)

    def load(self) -> Optional[tuple[list[LearnedCandidate], int]]:
        """保存した手と次の id の番号。ファイルがまだなければ None。"""
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        learned = [
            LearnedCandidate(**{**m, "situation": tuple((k, v) for k, v in m.get("situation", {}).items())})
            for m in data["candidates"]
        ]
        return learned, int(data["next_id"])

    def save(self, learned: Sequence[LearnedCandidate], next_id: int) -> None:
        """覚えた候補を保存する。"""
        data = {
            "candidates": [{**asdict(m), "situation": dict(m.situation)} for m in learned],
            "next_id": next_id,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
