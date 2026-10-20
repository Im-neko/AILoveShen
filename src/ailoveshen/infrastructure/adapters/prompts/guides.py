"""手引き（guides/<名前>/SKILL.md）を読む（設計書 192）。"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from loguru import logger

from ailoveshen.application.use_cases.guides import GuideBook
from ailoveshen.domain.value_objects import Guide

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def parse_guide(text: str, fallback_name: str) -> Guide | None:
    """SKILL.md の文から手引きにする。frontmatter（name、description）がなければ None。"""
    m = _FRONTMATTER.match(text)
    if m is None:
        return None
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict) or not str(meta.get("description") or "").strip():
        return None
    name = str(meta.get("name") or fallback_name).strip()
    return Guide(name=name, description=str(meta["description"]).strip(), body=m.group(2).strip())


def load_guides(directory: str | Path) -> GuideBook:
    """`directory/<名前>/SKILL.md` を全部読む。読めない・形が違うものは捨てて警告する（プレイは止めない）。"""
    if not str(directory):
        return GuideBook()
    root = Path(directory)
    guides: list[Guide] = []
    for path in sorted(root.glob("*/SKILL.md")):
        try:
            guide = parse_guide(path.read_text(encoding="utf-8"), path.parent.name)
        except (OSError, yaml.YAMLError) as e:
            logger.warning(f"手引きを読めなかった（{path}）: {e}")
            continue
        if guide is None:
            logger.warning(f"手引きを捨てた（{path}）: frontmatter の description がない")
            continue
        guides.append(guide)
    return GuideBook(guides)
