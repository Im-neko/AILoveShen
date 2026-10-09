"""教訓帳のコンパクション: いっぱいになったとき、似た教訓を LLM に統合させる（docs/design/144）。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.value_objects import (
    MAX_LESSON_CHARS,
    MAX_LESSON_CONDITION_CHARS,
    Lesson,
)

PURPOSE = "lesson_compaction"

SYSTEM = f"""\
あなたは AI 配信者の教訓帳を整理します。教訓帳がいっぱいになったので、似た教訓を統合して件数を減らします。

## 決まり
- 統合するのは、同じことを言っている・重なっている教訓だけ（言い回しが違っても、同じ目標・同じ物・同じ場面で
  同じ助言なら統合する）。目標（goal）が違う教訓、助言が食い違う教訓、場面が違って別々に思い出すべき教訓は
  統合しない
- 統合した教訓は、元の教訓の大事な中身（物の名前、数、やってはいけないこと）を落とさない 1 文
  （{MAX_LESSON_CHARS} 字まで、日本語）にする。条件（condition）は、どんなときの教訓かの短い句
  （{MAX_LESSON_CONDITION_CHARS} 字まで）
- 教訓を消してはいけない。統合しないものは、書かずにそのまま残す
- 元の教訓の ids は 2 つ以上。1 つの教訓は 1 回しか使わない
- [教わった回数・効いた回数・失敗した回数] はコードが合算する。書かない

## 出力
統合する組だけを merges に書く。統合するものがなければ空にする。
"""


@dataclass(frozen=True)
class LessonMerge:
    """統合する教訓の組と、統合した 1 件の本文・条件。"""

    ids: tuple[str, ...]
    text: str
    condition: str = ""


def merge_schema() -> dict[str, Any]:
    """統合の出力の JSON スキーマ。"""
    return {
        "type": "object",
        "properties": {
            "merges": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ids": {
                            "type": "array",
                            "minItems": 2,
                            "items": {"type": "string"},
                            "description": "ids of the lessons that say the same thing, e.g. L3",
                        },
                        "text": {
                            "type": "string",
                            "description": f"the merged lesson, one Japanese sentence of at most {MAX_LESSON_CHARS} characters",
                        },
                        "condition": {
                            "type": "string",
                            "description": f"when it applies, a short Japanese phrase of at most {MAX_LESSON_CONDITION_CHARS} characters",
                        },
                    },
                    "required": ["ids", "text"],
                },
            }
        },
        "required": ["merges"],
    }


def describe(lesson: Lesson) -> str:
    """教訓 1 件の行（id、状況、実績、本文、条件）。"""
    where = " ".join(f"{k}={v}" for k, v in lesson.situation) or "any time"
    record = f"教わった {lesson.taught}、効いた {lesson.helped}、失敗 {lesson.failed}"
    condition = f"（{lesson.condition}）" if lesson.condition else ""
    return f"[{lesson.id}] ({where}; {record}) {lesson.text}{condition}"


def parse_merges(data: dict[str, Any]) -> list[LessonMerge]:
    """出力を統合の組にする。形が正しくない組は読み飛ばす。"""
    merges = []
    for raw in data.get("merges") or []:
        if not isinstance(raw, dict):
            continue
        ids = tuple(str(i).strip() for i in raw.get("ids") or [])
        text = str(raw.get("text") or "").strip()
        if len(set(ids)) < 2 or not text:
            logger.warning(f"統合の組が正しくない: {raw!r}")
            continue
        merges.append(LessonMerge(ids, text, str(raw.get("condition") or "").strip()))
    return merges


class LessonCompactor:
    """教訓帳を LLM に見せて、統合してよい教訓の組を答えさせる。"""

    def __init__(self, text_generator: ITextGenerator) -> None:
        self._text_generator = text_generator

    async def propose(self, lessons: Sequence[Lesson], target: int) -> list[LessonMerge]:
        """
        `target` 件まで減らすための統合の組を答えさせる。

        Raises:
            TextGenerationError: 生成に失敗したとき
        """
        body = "\n".join(describe(lesson) for lesson in lessons)
        prompt = (
            f"## 教訓帳（{len(lessons)} 件）\n{body}\n\n"
            f"## 頼み\n{target} 件まで減らしたい（{max(len(lessons) - target, 0)} 件以上減らす統合を探す）。"
            "統合してよい組を merges に書いてください。"
        )
        data = await self._text_generator.generate_json(
            prompt, merge_schema(), system_instruction=SYSTEM, purpose=PURPOSE
        )
        return parse_merges(data)
