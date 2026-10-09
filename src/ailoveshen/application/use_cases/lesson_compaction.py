"""教訓帳のコンパクション: いっぱいになったとき、似た教訓を LLM に統合させる（docs/design/144）。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.advisor import PURPOSE as ADVISOR_PURPOSE
from ailoveshen.domain.value_objects import (
    MAX_LESSON_CHARS,
    MAX_LESSON_CONDITION_CHARS,
    Lesson,
)

PURPOSE = "lesson_compaction"

SYSTEM = f"""\
あなたは AI 配信者の教訓帳を整理します。教訓帳がいっぱいになったので、同じことを言っている教訓を統合して
件数を減らします。ここに見せるのは、同じ目標（goal）の教訓です。

## 決まり
- 統合するのは、同じ場面の同じ問題について、同じ助言をしている教訓だけ。言い回しや細かい理由が違っても、
  言っていることが同じなら統合する（同じ助言が何度も書かれていることが多い。まとめて減らす）
- 別々の話は統合しない。統合した 1 文が「A。また B」と別の話をつなげた文になるなら、統合しない
  （例: 水の流出の話と作業台の置き場の話、柵の置き方と耕地の話）
- 同じ場面の同じ問題で、助言が食い違っていて、どちらが正しいか自分では決められない教訓は、統合せず
  conflicts に書く（食い違う教訓どおしを 1 文にしない。選んで混ぜると、間違いのほうを正しい顔で残す）。
  ids はその話の教訓を全部（食い違わないものも含めて）。note は何が食い違うかを 1 文
- 統合した教訓は、元の教訓の大事な中身（物の名前、数、やってはいけないこと）を落とさない 1 文
  （{MAX_LESSON_CHARS} 字まで、日本語）にする。条件（condition）は、どんなときの教訓かの短い句
  （{MAX_LESSON_CONDITION_CHARS} 字まで）
- 教訓を消してはいけない。統合しないものは、書かずにそのまま残す
- 元の教訓の ids は 2 つ以上。1 つの教訓は 1 回しか使わない
- [教わった回数・効いた回数・失敗した回数] はコードが合算する。書かない

## 出力
統合する組だけを merges に、決められない食い違いを conflicts に書く。なければどちらも空にする。
"""

ADVISOR_SYSTEM = f"""\
あなたは Minecraft に詳しい advisor です。AI 配信者の教訓帳に、同じ話で助言が食い違う教訓が入っています。
整理担当は、どちらが正しいか決められませんでした。あなたが決めて、正しい 1 件にまとめてください。

## 決まり
- 教訓の記録（教わった回数・効いた回数・失敗した回数）は参考になる。効いた教訓は信じてよいが、効いていない
  ことだけでは間違いと決めない。Minecraft の仕組み（1.21、サバイバル）に照らして、正しいほうを選ぶ
- 食い違う話ごとに、ids（見せた教訓から、その話の教訓を全部）と、正しい 1 文の教訓（text。
  {MAX_LESSON_CHARS} 字まで、日本語）、どんなときの教訓かの短い句（condition。{MAX_LESSON_CONDITION_CHARS} 字まで）を書く
- 元の教訓のどちらも正しくなければ、正しい内容で書き直してよい
- 自分でも決められない話は、resolutions に書かない（教訓はそのまま残る）
"""


@dataclass(frozen=True)
class LessonMerge:
    """統合する教訓の組と、統合した 1 件の本文・条件。"""

    ids: tuple[str, ...]
    text: str
    condition: str = ""


def _ids_schema(description: str) -> dict[str, Any]:
    return {"type": "array", "minItems": 2, "items": {"type": "string"}, "description": description}


def _lesson_properties() -> dict[str, Any]:
    return {
        "text": {
            "type": "string",
            "description": f"the merged lesson, one Japanese sentence of at most {MAX_LESSON_CHARS} characters",
        },
        "condition": {
            "type": "string",
            "description": f"when it applies, a short Japanese phrase of at most {MAX_LESSON_CONDITION_CHARS} characters",
        },
    }


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
                        "ids": _ids_schema("ids of the lessons that say the same thing, e.g. L3"),
                        **_lesson_properties(),
                    },
                    "required": ["ids", "text"],
                },
            },
            "conflicts": {
                "type": "array",
                "description": "lessons about the same thing whose advice contradicts and you cannot tell which is right",
                "items": {
                    "type": "object",
                    "properties": {
                        "ids": _ids_schema("ids of all the lessons about that thing, e.g. L3"),
                        "note": {"type": "string", "description": "what contradicts, in one Japanese sentence"},
                    },
                    "required": ["ids"],
                },
            },
        },
        "required": ["merges"],
    }


def resolution_schema() -> dict[str, Any]:
    """advisor の答えの JSON スキーマ。"""
    return {
        "type": "object",
        "properties": {
            "resolutions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ids": _ids_schema("ids of all the lessons about that one thing, e.g. L3"),
                        **_lesson_properties(),
                    },
                    "required": ["ids", "text"],
                },
            }
        },
        "required": ["resolutions"],
    }


def describe(lesson: Lesson) -> str:
    """教訓 1 件の行（id、状況、実績、本文、条件）。"""
    where = " ".join(f"{k}={v}" for k, v in lesson.situation) or "any time"
    record = f"教わった {lesson.taught}、効いた {lesson.helped}、失敗 {lesson.failed}"
    condition = f"（{lesson.condition}）" if lesson.condition else ""
    return f"[{lesson.id}] ({where}; {record}) {lesson.text}{condition}"


def _parse_groups(raw_items: Any, label: str) -> list[LessonMerge]:
    groups = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        ids = tuple(str(i).strip() for i in raw.get("ids") or [])
        text = str(raw.get("text") or raw.get("note") or "").strip()
        if len(set(ids)) < 2 or not text:
            logger.warning(f"{label}の組が正しくない: {raw!r}")
            continue
        groups.append(LessonMerge(ids, text, str(raw.get("condition") or "").strip()))
    return groups


def parse_merges(data: dict[str, Any]) -> list[LessonMerge]:
    """出力を統合の組にする。形が正しくない組は読み飛ばす。"""
    return _parse_groups(data.get("merges"), "統合")


def parse_conflicts(data: dict[str, Any]) -> list[tuple[str, ...]]:
    """出力の、決められない食い違いの教訓の ids。note はログのため（なくてもよい）。"""
    out = []
    for raw in data.get("conflicts") or []:
        ids = tuple(dict.fromkeys(str(i).strip() for i in (raw.get("ids") if isinstance(raw, dict) else None) or []))
        if len(ids) >= 2:
            logger.info(f"教訓の食い違い: {ids}: {raw.get('note', '')}")
            out.append(ids)
    return out


def parse_resolutions(data: dict[str, Any]) -> list[LessonMerge]:
    """advisor の答えを、統合の組にする。"""
    return _parse_groups(data.get("resolutions"), "advisor の解決")


class LessonCompactor:
    """教訓帳を LLM に見せて、統合してよい教訓の組を答えさせる。"""

    def __init__(self, text_generator: ITextGenerator, advisor: bool = False) -> None:
        """
        Args:
            text_generator: LLM（用途 lesson_compaction）
            advisor: 食い違う教訓を advisor（用途 advice。LITELLM_ADVISOR_MODEL）に決めさせるか
        """
        self._text_generator = text_generator
        self._advisor = advisor

    async def propose(self, lessons: Sequence[Lesson], target: int) -> list[LessonMerge]:
        """
        `target` 件まで減らすための統合の組を答えさせる。目標（goal）ごとに分けて聞く（全部を 1 回で見せると
        重なりを見つけきれず、別の目標の教訓も混ぜてしまう: 2026-10-09 に 200 件で測った）。

        Raises:
            TextGenerationError: 生成に失敗したとき
        """
        groups: dict[str | None, list[Lesson]] = {}
        for lesson in lessons:
            groups.setdefault(lesson.keys.get("goal"), []).append(lesson)
        merges: list[LessonMerge] = []
        for group in groups.values():
            if len(group) < 2:
                continue
            ids = {n.id for n in group}
            wanted = math.ceil(len(group) * target / max(len(lessons), 1))
            found, conflicts = await self._ask(group, wanted)
            conflicts = [c for c in conflicts if set(c) <= ids]
            resolved = await self._resolve(group, conflicts) if conflicts else []
            # advisor の解決を先に。先の組が使った教訓は、あとの組から外す（残りが 2 件以上なら統合する。
            # 丸ごと捨てると、食い違いの 2 件のせいで同じ話の十数件が統合されずに残った: 2026-10-09 の実機）
            taken: set[str] = set()
            for merge in [*resolved, *found]:
                if not set(merge.ids) <= ids:
                    logger.warning(f"同じ目標でない教訓の統合を捨てた: {merge.ids}")
                    continue
                rest = tuple(i for i in merge.ids if i not in taken)
                if len(rest) < 2:
                    continue
                taken.update(rest)
                merges.append(replace(merge, ids=rest))
        return merges

    async def _ask(
        self, group: Sequence[Lesson], target: int
    ) -> tuple[list[LessonMerge], list[tuple[str, ...]]]:
        body = "\n".join(describe(lesson) for lesson in group)
        prompt = (
            f"## 教訓（{len(group)} 件）\n{body}\n\n"
            f"## 頼み\nこの {len(group)} 件を {target} 件ほどまで減らしたい。同じ助言の教訓を探して、"
            "統合してよい組を merges に、決められない食い違いを conflicts に書いてください。"
        )
        data = await self._text_generator.generate_json(
            prompt, merge_schema(), system_instruction=SYSTEM, purpose=PURPOSE
        )
        return parse_merges(data), parse_conflicts(data)

    async def _resolve(
        self, group: Sequence[Lesson], conflicts: Sequence[tuple[str, ...]]
    ) -> list[LessonMerge]:
        """食い違う教訓を advisor に決めさせる。advisor がない・答えない・決められないときは、そのまま残す。"""
        if not self._advisor:
            logger.info(f"食い違う教訓は advisor がないので残す: {len(conflicts)} 組")
            return []
        by_id = {n.id: n for n in group}
        shown = "\n\n".join(
            "### 話 {}\n{}".format(i, "\n".join(describe(by_id[j]) for j in ids))
            for i, ids in enumerate(conflicts, 1)
        )
        prompt = f"## 食い違う教訓\n{shown}\n\n## 頼み\n話ごとに、正しい 1 件の教訓を resolutions に書いてください。"
        try:
            data = await self._text_generator.generate_json(
                prompt, resolution_schema(), system_instruction=ADVISOR_SYSTEM, purpose=ADVISOR_PURPOSE
            )
        except Exception as e:  # noqa: BLE001 - advisor が答えなくても、教訓はそのまま残して続ける
            logger.warning(f"食い違う教訓を advisor に決めさせられなかった（残す）: {type(e).__name__}: {e}")
            return []
        allowed = {i for ids in conflicts for i in ids}
        resolved = [m for m in parse_resolutions(data) if set(m.ids) <= allowed]
        logger.info(f"advisor に食い違いを聞いた: {len(conflicts)} 組 → {len(resolved)} 組を決めた")
        return resolved
