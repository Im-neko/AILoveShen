"""新しく書いた教訓の確認: 手引きに反するものは消し、既存と同じものは重ねる（docs/design/201）。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.lesson_check import (
    DUPLICATE,
    OK,
    WRONG,
    LessonChecker,
    LessonVerdict,
    parse_verdict,
    similar_to,
    verdict_schema,
)
from ailoveshen.application.use_cases.lessons import LessonBook
from ailoveshen.domain.value_objects import Lesson


class _Store:
    def __init__(self):
        self.saved = None

    def load(self):
        return self.saved

    def save(self, lessons, next_id):
        self.saved = (list(lessons), next_id)


def _book(verdict=LessonVerdict(OK), error=None):
    checker = AsyncMock(spec=LessonChecker)
    if error:
        checker.check.side_effect = error
    else:
        checker.check.return_value = verdict
    return LessonBook(_Store(), mode="rules", recorder=Mock(), checker=checker), checker


async def _settle(book):
    await asyncio.gather(*list(book._checking))


@pytest.mark.asyncio
async def test_a_lesson_that_contradicts_the_guides_is_dropped():
    book, _ = _book(LessonVerdict(WRONG, reason="鉱石はバイオームで決まらない"))
    lesson = book.learn("沼地では鉄が出にくい", "鉄を探すとき", {"goal": "have"}, True, "failure")
    assert lesson in book.lessons  # 書くときは待たない
    await _settle(book)
    assert book.lessons == ()
    assert book._store.saved[0] == []


@pytest.mark.asyncio
async def test_a_lesson_that_says_the_same_is_merged_into_the_existing_one():
    book, checker = _book()
    old = book.learn("穴から登れないときは横に掘って出る", "", {"goal": "have"}, True, "failure")
    await _settle(book)
    checker.check.return_value = LessonVerdict(DUPLICATE, duplicate_of=old.id, reason="同じ助言")
    new = book.learn("穴を登れないなら登らず横へ掘る", "穴の底", {"goal": "have"}, True, "viewer:neko")
    await _settle(book)
    (kept,) = book.lessons
    assert kept.id == old.id and kept.taught == old.taught + new.taught
    assert kept.source == "viewer:neko" and kept.condition == "穴の底"


@pytest.mark.asyncio
async def test_a_duplicate_of_another_goal_is_kept():
    book, checker = _book()
    old = book.learn("夜は家で寝る", "", {"goal": "through_night"}, True, "failure")
    await _settle(book)
    checker.check.return_value = LessonVerdict(DUPLICATE, duplicate_of=old.id)
    book.learn("夜は家で寝よう", "", {"goal": "have"}, True, "failure")
    await _settle(book)
    assert len(book.lessons) == 2


@pytest.mark.asyncio
async def test_an_lesson_that_cannot_be_checked_is_kept():
    book, _ = _book(error=RuntimeError("down"))
    book.learn("石は斧で掘らない", "", {"goal": "have"}, True, "failure")
    await _settle(book)
    assert len(book.lessons) == 1


@pytest.mark.asyncio
async def test_an_ok_lesson_stays_and_the_check_is_recorded():
    book, checker = _book(LessonVerdict(OK))
    book.learn("松明は炭が要る", "", {"goal": "have"}, True, "failure")
    await _settle(book)
    assert len(book.lessons) == 1
    assert book._recorder.record.call_args.args[0]["verdict"] == OK


def test_learning_without_a_running_loop_does_not_check():
    book, checker = _book(LessonVerdict(WRONG))
    book.learn("松明は炭が要る", "", {"goal": "have"}, True, "failure")
    checker.check.assert_not_called()
    assert len(book.lessons) == 1


def test_parse_verdict_falls_back_to_ok_when_the_duplicate_target_is_unknown():
    assert parse_verdict({"verdict": "duplicate", "duplicate_of": "L9"}, {"L1"}) == LessonVerdict(OK)
    assert parse_verdict({"verdict": "duplicate", "duplicate_of": "L1", "reason": "x"}, {"L1"}) == LessonVerdict(DUPLICATE, "L1", "x")
    assert parse_verdict({"verdict": "wrong", "reason": "r"}, set()) == LessonVerdict(WRONG, reason="r")
    assert parse_verdict({}, set()) == LessonVerdict(OK)
    assert verdict_schema()["properties"]["verdict"]["enum"] == [OK, WRONG, DUPLICATE]


def test_similar_lessons_are_the_same_goal_and_near_in_text():
    new = Lesson("L3", "穴から登れないなら横に掘って出る", situation=(("goal", "have"),))
    near = Lesson("L1", "穴から登れないときは横に掘って出る", situation=(("goal", "have"),))
    other_goal = Lesson("L2", "穴から登れないときは横に掘って出る", situation=(("goal", "built"),))
    far = Lesson("L4", "ベッドは羊毛が要る", situation=(("goal", "have"),))
    assert similar_to(new, [near, other_goal, far, new]) == (near,)


@pytest.mark.asyncio
async def test_the_checker_puts_the_guides_before_the_cache_break_and_the_lessons_after_it():
    from ailoveshen.application.ports.output.text_generator import CACHE_BREAK

    generator = AsyncMock()
    generator.generate_json.return_value = {"verdict": "ok"}
    checker = LessonChecker(generator, lambda: "### branch_mining\n鉄は y=16 付近")
    new = Lesson("L2", "鉄は沼地に少ない", situation=(("goal", "have"),))
    await checker.check(new, [Lesson("L1", "鉄は坑道の壁で取る", situation=(("goal", "have"),))])
    args, kwargs = generator.generate_json.await_args
    before, after = args[0].split(CACHE_BREAK)
    assert "鉄は y=16 付近" in before and "鉄は沼地に少ない" not in before
    assert "鉄は沼地に少ない" in after and "L1" in after
    assert "system_instruction" not in kwargs and kwargs["purpose"] == "lesson_check"


def test_a_lesson_without_a_goal_key_can_be_a_duplicate_of_one_with_a_goal():
    new = Lesson("L3", "松明を作る前に炭を確かめる", situation=(("item", "torch"),))
    old = Lesson("L1", "松明は炭が要るので先に木炭にする", situation=(("goal", "have"), ("item", "torch")))
    assert similar_to(new, [old]) == (old,)


@pytest.mark.asyncio
async def test_a_lesson_added_by_the_operator_is_not_judged_again_by_the_llm():
    from ailoveshen.application.use_cases.lessons import OPERATOR_SOURCE

    book, checker = _book(LessonVerdict(WRONG))
    book.learn("鉄は y=16 付近に多い", "", {}, False, OPERATOR_SOURCE)
    await _settle(book)
    checker.check.assert_not_called()
    assert len(book.lessons) == 1
