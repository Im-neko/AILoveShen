"""新しく書いた教訓の確認: 手引き（確かな知識）に反していないか、既にある教訓と同じでないか（docs/design/201）。"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.text_generator import CACHE_BREAK, ITextGenerator
from ailoveshen.application.use_cases.lesson_compaction import describe
from ailoveshen.domain.value_objects import Lesson

PURPOSE = "lesson_check"
SIMILAR_SHOWN = 8  # 見せる既存の教訓の数（同じ目標のうち、文が近い順）
SIMILAR_MIN_RATIO = 0.2

OK = "ok"
WRONG = "wrong"
DUPLICATE = "duplicate"

SYSTEM = """\
あなたは AI 配信者の教訓帳の見張りです。新しく書かれた教訓を 1 件、受け入れてよいか確かめます。

## 参考（確かな知識。手引き）
$reference

## 決まり
- wrong: 参考か Minecraft 1.21（サバイバル）の仕組みに、はっきり反する教訓。例: 鉱石の多さがバイオームで
  決まると言う、鉄の道具を作るのに要る鉄インゴットの数が違う。迷うとき、確かめられないときは wrong にしない
- duplicate: 見せた既存の教訓のどれかと、同じ場面の同じ助言（言い回しや細かい理由が違うだけ）。duplicate_of に
  その id を書く。場面や助言が違うものは duplicate にしない
- ok: それ以外。経験から得た教訓（確かめられないもの）は ok
- reason は 1 文（日本語）。ok のときは空でよい
"""


@dataclass(frozen=True)
class LessonVerdict:
    """確認の結果。kind: ok / wrong / duplicate。duplicate のときだけ duplicate_of（既存の教訓の id）。"""

    kind: str = OK
    duplicate_of: str = ""
    reason: str = ""


def verdict_schema() -> dict[str, Any]:
    """確認の出力の JSON スキーマ。理由を先に書かせて、判定は理由に従わせる（2026-10-20: 判定を先に書かせたら、
    理由で「正しい」と言いながら wrong と答え、正しい教訓 L1924 が消えた）。"""
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "one Japanese sentence: what the lesson says and whether it contradicts the reference or the game (empty for ok)"},
            "verdict": {"type": "string", "enum": [OK, WRONG, DUPLICATE]},
            "duplicate_of": {
                "type": "string",
                "description": "duplicate only: the id of the existing lesson that says the same, e.g. L3",
            },
        },
        "required": ["reason", "verdict"],
    }


def confirm_schema() -> dict[str, Any]:
    """wrong の見直しの出力の JSON スキーマ。"""
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "one Japanese sentence: does the first reason really show that the lesson is false?"},
            "confirmed": {"type": "boolean", "description": "true only if the lesson is clearly false (contradicts the reference or the game)"},
        },
        "required": ["reason", "confirmed"],
    }


def parse_verdict(data: dict[str, Any], known: set[str]) -> LessonVerdict:
    """出力を確認の結果にする。形が正しくないものは ok（教訓を落とさない）。"""
    kind = str(data.get("verdict") or OK).strip()
    reason = str(data.get("reason") or "").strip()
    if kind == DUPLICATE:
        target = str(data.get("duplicate_of") or "").strip()
        if target not in known:
            logger.warning(f"教訓の重複の相手がない（ok にする）: {data!r}")
            return LessonVerdict(OK)
        return LessonVerdict(DUPLICATE, target, reason)
    if kind == WRONG:
        return LessonVerdict(WRONG, reason=reason)
    return LessonVerdict(OK)


def same_goal(a: Lesson, b: Lesson) -> bool:
    """同じ目標の教訓か（目標のキーがない教訓は、どの目標とも重ねてよい）。"""
    goal_a, goal_b = a.keys.get("goal"), b.keys.get("goal")
    return goal_a is None or goal_b is None or goal_a == goal_b


def similar_to(lesson: Lesson, lessons: Sequence[Lesson], limit: int = SIMILAR_SHOWN) -> tuple[Lesson, ...]:
    """同じ目標の既存の教訓を、文が近い順に（重複の候補）。"""
    scored = []
    for other in lessons:
        if other.id == lesson.id or not same_goal(lesson, other):
            continue
        ratio = difflib.SequenceMatcher(None, lesson.text, other.text).ratio()
        if ratio >= SIMILAR_MIN_RATIO:
            scored.append((ratio, other))
    scored.sort(key=lambda p: p[0], reverse=True)
    return tuple(other for _, other in scored[:limit])


class LessonChecker:
    """新しい教訓を 1 件、手引きと既存の教訓に照らして LLM に確かめさせる。"""

    def __init__(self, text_generator: ITextGenerator, reference: Callable[[], str]) -> None:
        """
        Args:
            text_generator: LLM（用途 lesson_check）
            reference: 参考にする確かな知識の文（手引きの本文）。決まりと一緒に、プロンプトの固定の先頭
                （キャッシュの境目の前）に置く。変わるのは手引きを直したときだけなので、キャッシュに乗る
        """
        self._text_generator = text_generator
        self._reference = reference

    async def check(self, lesson: Lesson, existing: Sequence[Lesson]) -> LessonVerdict:
        """
        Raises:
            TextGenerationError: 生成に失敗したとき
        """
        candidates = similar_to(lesson, existing)
        shown = "\n".join(describe(n) for n in candidates) or "（なし）"
        where = " ".join(f"{k}={v}" for k, v in lesson.situation) or "any time"
        condition = f"（{lesson.condition}）" if lesson.condition else ""
        # 決まりと手引きは固定の先頭（キャッシュの境目の前）、変わる教訓はそのあと。システム指示に置くと、道具を
        # 渡さない呼び出しではキャッシュの印が付かず、毎回全部を読み直していた（2026-10-20: 実測 0%）
        fixed = SYSTEM.replace("$reference", self._reference() or "（手引きなし）")
        prompt = (
            f"{fixed}{CACHE_BREAK}"
            f"## 新しい教訓\n[{lesson.id}] ({where}; {lesson.source}) {lesson.text}{condition}\n\n"
            f"## 既存の教訓（同じ目標、近い順）\n{shown}\n\n"
            "## 頼み\nこの新しい教訓を、wrong / duplicate / ok のどれにするか答えてください。"
        )
        data = await self._text_generator.generate_json(prompt, verdict_schema(), purpose=PURPOSE)
        verdict = parse_verdict(data, {n.id for n in candidates})
        if verdict.kind == WRONG and not await self._confirmed(fixed, lesson, condition, verdict.reason):
            logger.info(f"教訓を消さなかった（見直しで違った）: {lesson.id} {lesson.text}: {verdict.reason}")
            return LessonVerdict(OK, reason=f"見直しで違った: {verdict.reason}")
        return verdict

    async def _confirmed(self, fixed: str, lesson: Lesson, condition: str, reason: str) -> bool:
        """wrong と答えた理由が、本当に教訓の誤りを示しているか、もう一度聞く。消すのは取り返しがつかないので、
        確かでなければ残す。固定の先頭は同じなので、キャッシュに乗る。"""
        prompt = (
            f"{fixed}{CACHE_BREAK}"
            f"## 見直し\n[{lesson.id}] {lesson.text}{condition}\n\n"
            f"一度目の判定は wrong で、理由はこうでした: {reason}\n\n"
            "## 頼み\nこの理由は、教訓が参考か Minecraft 1.21 の仕組みに**はっきり反する**ことを本当に示していますか。"
            "理由が教訓を正しいと言っているとき、確かめられないとき、経験則にすぎないときは confirmed=false にしてください。"
        )
        data = await self._text_generator.generate_json(prompt, confirm_schema(), purpose=PURPOSE)
        return bool(data.get("confirmed"))
