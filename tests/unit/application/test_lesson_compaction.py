"""教訓帳がいっぱいになったとき、似た教訓を統合する（docs/design/144）。"""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.lesson_compaction import (
    LessonCompactor,
    LessonMerge,
    merge_schema,
    parse_merges,
)
from ailoveshen.application.use_cases.lessons import LessonBook
from ailoveshen.domain.exceptions import TextGenerationError


class _Store:
    def __init__(self):
        self.saved = None

    def load(self):
        return self.saved

    def save(self, lessons, next_id):
        self.saved = (list(lessons), next_id)


def _compactor(merges=(), error=None):
    compactor = AsyncMock(spec=LessonCompactor)
    if error:
        compactor.propose.side_effect = error
    else:
        compactor.propose.return_value = list(merges)
    return compactor


def _book(compactor, max_lessons=8):
    return LessonBook(_Store(), mode="rules", max_lessons=max_lessons, recorder=Mock(), compactor=compactor)


SENTENCES = [
    "畑の水は四マス以内に置く",
    "夜は家の外で原木を切らない",
    "鉄のつるはしがないと鉄鉱石は掘れない",
    "松明は壁の高さの目の位置に付ける",
    "バケツで水を汲むときは足元の小麦を避ける",
    "ベッドは奥の壁に沿って置くと眠れない",
    "ゾンビは昼でも暗い洞窟に湧く",
    "苗木は建物から離れた開けた地面に植える",
    "石炭が足りなければ地下の壁を掘る",
    "かまどには燃料と材料を別に入れる",
    "チェストは家の入口から離して置く",
    "クリーパーの近くでは爆発に注意して逃げる",
]


def _learn(book, i, goal="farmed", source="failure"):
    return book.learn(SENTENCES[i], "", {"goal": goal}, True, source)


async def _fill(book, n, goal="farmed"):
    for i in range(n):
        _learn(book, i, goal)
    if book._compacting is not None:
        await book._compacting


@pytest.mark.asyncio
async def test_the_book_may_grow_a_little_past_the_limit_while_a_compactor_is_there():
    book = _book(_compactor(), max_lessons=8)
    assert book.limit == 10
    await _fill(book, 9)
    assert len(book.lessons) == 8  # 統合の答えがなければ、弱いほうから消して上限に収める


@pytest.mark.asyncio
async def test_similar_lessons_merge_into_one_with_the_records_added_up():
    compactor = _compactor()
    book = _book(compactor, max_lessons=8)
    await _fill(book, 8)
    first, second = book.lessons[0], book.lessons[1]
    book._lessons[0] = replace(first, helped=2, used=3, taught=2)
    book._lessons[1] = replace(second, failed=1, source="viewer:neko")
    first, second = book.lessons[0], book.lessons[1]
    compactor.propose.return_value = [LessonMerge((first.id, second.id), "畑の水は 4 マス以内に置く", "畑を作るとき")]
    _learn(book, 8)
    await book._compacting
    merged = next(n for n in book.lessons if n.text == "畑の水は 4 マス以内に置く")
    assert (merged.taught, merged.used, merged.helped, merged.failed) == (3, 3, 2, 1)
    assert merged.source == "viewer:neko" and merged.condition == "畑を作るとき"
    assert dict(merged.situation) == {"goal": "farmed"} and merged.explicit
    ids = {n.id for n in book.lessons}
    assert first.id not in ids and second.id not in ids
    assert len(book.lessons) == 8  # 9 件 → 8 件
    assert merged.id in {n.id for n in book._store.saved[0]}  # 保存される


@pytest.mark.asyncio
async def test_a_merge_across_different_goals_or_with_missing_ids_is_skipped():
    compactor = _compactor()
    book = _book(compactor, max_lessons=8)
    await _fill(book, 7)
    _learn(book, 7, "lit")
    a, b = book.lessons[0], book.lessons[7]
    compactor.propose.return_value = [
        LessonMerge((a.id, b.id), "混ぜてはいけない"),
        LessonMerge((a.id, "L99"), "ない教訓"),
    ]
    _learn(book, 8)
    await book._compacting
    texts = {n.text for n in book.lessons}
    assert "混ぜてはいけない" not in texts and "ない教訓" not in texts
    assert len(book.lessons) == 8  # 統合できなかった分は、これまでどおり弱いほうから消す


@pytest.mark.asyncio
async def test_a_failed_compaction_falls_back_to_cutting_the_weakest():
    book = _book(_compactor(error=TextGenerationError("down")), max_lessons=8)
    await _fill(book, 9)
    assert len(book.lessons) == 8


@pytest.mark.asyncio
async def test_a_merge_with_too_long_text_is_skipped():
    compactor = _compactor()
    book = _book(compactor, max_lessons=8)
    await _fill(book, 8)
    a, b = book.lessons[0], book.lessons[1]
    compactor.propose.return_value = [LessonMerge((a.id, b.id), "あ" * 81)]
    _learn(book, 8)
    await book._compacting
    assert len(book.lessons) == 8 and all(len(n.text) <= 80 for n in book.lessons)


def test_without_a_compactor_the_book_is_cut_at_the_limit_as_before():
    book = LessonBook(_Store(), mode="rules", max_lessons=2)
    for i in range(3):
        _learn(book, i)
    assert len(book.lessons) == 2 and book.limit == 2


def test_the_proposal_is_parsed_and_malformed_groups_are_dropped():
    merges = parse_merges(
        {
            "merges": [
                {"ids": ["L1", "L2"], "text": "まとめ", "condition": "いつも"},
                {"ids": ["L3"], "text": "1 件だけ"},
                {"ids": ["L4", "L4"], "text": "同じ id"},
                {"ids": ["L5", "L6"], "text": ""},
            ]
        }
    )
    assert merges == [LessonMerge(("L1", "L2"), "まとめ", "いつも")]
    assert merge_schema()["properties"]["merges"]["items"]["properties"]["ids"]["minItems"] == 2


@pytest.mark.asyncio
async def test_the_compactor_shows_every_lesson_with_its_record_and_asks_for_the_target():
    generator = AsyncMock()
    generator.generate_json.return_value = {"merges": []}
    book = LessonBook(_Store(), mode="rules")
    book.learn("畑の水を置く", "畑を作るとき", {"goal": "farmed"}, True, "failure")
    result = await LessonCompactor(generator).propose(book.lessons, 150)
    assert result == []
    prompt = generator.generate_json.await_args.args[0]
    assert "[L1] (goal=farmed; 教わった 1、効いた 0、失敗 0) 畑の水を置く（畑を作るとき）" in prompt
    assert "150 件まで減らしたい" in prompt
    assert generator.generate_json.await_args.kwargs["purpose"] == "lesson_compaction"
