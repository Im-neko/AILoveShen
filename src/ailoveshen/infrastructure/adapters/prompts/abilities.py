"""やり方の帳（abilities.yaml）を読む（docs/design/47 §4.1）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

ABILITIES_PATH = Path(__file__).with_name("abilities.yaml")


def load_abilities(path: Path = ABILITIES_PATH) -> list[dict[str, Any]]:
    """帳の項目の一覧。読めなければ空（検索は「まだない」と返し、プレイは止めない）。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        logger.warning(f"やり方の帳を読めなかった（{path}）: {e}")
        return []
    return [a for a in data or [] if isinstance(a, dict) and a.get("key")]
