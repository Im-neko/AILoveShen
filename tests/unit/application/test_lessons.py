"""教訓帳（docs/design/35）のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.lessons import (
    LessonBook,
    ended_kind,
    score,
    situation_of,
)
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import (
    Activity,
    FastAnswer,
    FastVerdict,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    Lesson,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import format_activity
from ailoveshen.infrastructure.adapters.storage.json_lesson_store import JsonLessonStore

LOGS = GoalSpec(GoalPredicate.HAVE, item="oak_log", count=8)
NIGHT = {"goal": "through_night", "time": "night", "place": "outside", "body": "ok"}


def _obs(**kwargs) -> GameObservation:
    base = dict(state={"inventory": {"wooden_axe": 1}}, candidates=(), health=20.0, food=20)
    return GameObservation(**{**base, **kwargs})


class _Store:
    def __init__(self):
        self.saved = None

    def load(self):
        return self.saved

    def save(self, lessons, next_id):
        self.saved = (list(lessons), next_id)


def _book(answers=None, error=None, mode="jev", **kwargs):
    judge = AsyncMock()
    if error:
        judge.ask.side_effect = error
    else:
        judge.ask.side_effect = lambda state, questions: FastVerdict(
            {
                q.name: FastAnswer(q.name, *answers[i])
                for i, q in enumerate(questions)
                if i < len(answers or [])
            },
            20,
        )
    clock = iter(f"2026-09-26T00:00:{i:02d}+00:00" for i in range(60))
    book = LessonBook(
        _Store(), judge=judge, mode=mode, recorder=Mock(), clock=lambda: next(clock), **kwargs
    )
    return book, judge


def test_the_situation_comes_from_the_goal_and_the_observation():
    obs = _obs(time_phase="night", underground=True, food=5)
    assert situation_of(LOGS, obs, "stalled") == {
        "goal": "have",
        "item": "oak_log",
        "time": "night",
        "place": "underground",
        "body": "hungry",
        "pickaxe": "no",
        "ended": "stalled",
    }
    assert situation_of(None, _obs(inside_home=True, health=5))["place"] == "home"
    assert situation_of(None, _obs(state={"inventory": {"stone_pickaxe": 1}}))["pickaxe"] == "yes"


@pytest.mark.parametrize(
    "reason,kind",
    [
        ("goal have(log, 8) is stuck (actions keep failing)", "stuck"),
        ("goal x is reconsidered: Jev judged from the action record that it is stuck (…)", "stuck"),
        ("goal have(log, 8) stalled (no progress in 12 steps)", "stalled"),
        ("goal x is cut because the streamer died and respawned", "died"),
        ("goal have(log, 8) is met", "met"),
        ("the time of day changed from day to dusk", "other"),
        ("", ""),
    ],
)
def test_how_a_goal_ended(reason, kind):
    assert ended_kind(reason) == kind


def test_the_score_counts_what_matters_and_explicit_conditions_must_hold():
    snapshot = Lesson("L1", "木は斧で切る", situation=tuple({"goal": "have", "item": "oak_log", "time": "day", "place": "outside"}.items()))
    assert score(snapshot, {"goal": "have", "item": "oak_log", "time": "day"}) == 5  # 昼・外は数えない
    assert score(snapshot, {"goal": "have", "item": "birch_log"}) == 3  # 同じ種類の物
    assert score(snapshot, {"goal": "built"}) == 0
    # これからやる手順で当てはまる
    assert score(snapshot, {"goal": "built"}, [{"goal": "have", "item": "oak_log"}]) == 5
    night = Lesson("L2", "夜は家で寝る", situation=(("time", "night"),), explicit=True)
    assert score(night, {"time": "day", "goal": "have"}) is None  # 昼には思い出さない
    assert score(night, NIGHT) == 2  # 条件がすべて合う


@pytest.mark.asyncio
async def test_jev_keeps_only_the_lessons_it_is_sure_apply():
    book, judge = _book(answers=[(True, 0.9), (True, 0.4), (False, 0.9)])
    for text in ("夜は家で寝る", "夜はベッドを持ち歩く", "夜は松明を置く"):
        book.learn(text, "夜", {"time": "night", "goal": "through_night"}, True, "failure")
    recalled = await book.recall(NIGHT, context={"goal": "through_night()"})
    assert [n.text for n in recalled] == ["夜は家で寝る"]
    state, questions = judge.ask.await_args.args
    assert state["situation"] == NIGHT and state["goal"] == "through_night()"
    assert "夜は家で寝る" in questions[0].instructions


@pytest.mark.asyncio
async def test_without_jev_the_code_top_three_are_shown():
    book, _ = _book(error=ActionSelectionError("down"))
    for text in ("家で寝る", "松明を置く", "ベッドを持ち歩く", "地下で待つ", "扉を閉める"):
        book.learn(text, "", {"time": "night", "goal": "through_night"}, True, "failure")
    assert len(await book.recall(NIGHT)) == 3
    rules, judge = _book(mode="rules")
    rules.learn("夜は家で寝る", "", {"time": "night"}, True, "failure")
    assert [n.text for n in await rules.recall(NIGHT)] == ["夜は家で寝る"]
    judge.ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_nothing_to_recall_does_not_ask_jev():
    book, judge = _book(answers=[])
    book.learn("石はつるはしで掘る", "", {"goal": "have", "item": "cobblestone"}, True, "failure")
    assert await book.recall(NIGHT) == ()
    judge.ask.assert_not_awaited()


def test_the_same_lesson_is_strengthened_and_a_viewer_source_wins():
    book, _ = _book()
    book.learn("肉は焼いてから食べる", "", {"body": "hungry"}, True, "failure")
    again = book.learn("肉は、焼いてから食べる。", "お腹がすいたとき", {"body": "hungry"}, True, "viewer:neko")
    assert len(book.lessons) == 1
    assert (again.taught, again.source, again.condition) == (2, "viewer:neko", "お腹がすいたとき")


def test_outcomes_are_counted_and_lessons_that_never_help_are_dropped():
    book, _ = _book()
    good = book.learn("夜は家で寝る", "", {"time": "night"}, True, "failure")
    bad = book.learn("夜は走り回る", "", {"time": "night"}, True, "failure")
    for _ in range(2):
        book.mark_used([good])
        book.settle([good], "met")
    book.settle([good], "other")  # 時間帯が変わっただけは数えない
    for _ in range(4):
        book.mark_used([bad])
        book.settle([bad], "stuck")
    (kept,) = book.lessons
    assert (kept.text, kept.used, kept.helped, kept.proven) == ("夜は家で寝る", 2, 2, True)


def test_the_book_keeps_proven_lessons_when_it_is_full():
    book, _ = _book(max_lessons=2)
    proven = book.learn("夜は家で寝る", "", {"time": "night"}, True, "failure")
    for _ in range(2):
        book.settle([proven], "met")
    book.learn("木は斧で切る", "", {"item": "oak_log"}, True, "failure")
    book.learn("石はつるはしで掘る", "", {"item": "stone"}, True, "failure")
    assert [n.text for n in book.lessons] == ["夜は家で寝る", "石はつるはしで掘る"]


def test_the_book_is_saved_and_loaded_as_json(tmp_path):
    store = JsonLessonStore(tmp_path / "lessons.json")
    book = LessonBook(store, mode="rules")
    book.learn("夜は家で寝る", "夜", {"time": "night", "goal": "through_night"}, True, "viewer:neko")
    again = LessonBook(store, mode="rules")
    again.load()
    (lesson,) = again.lessons
    assert lesson.keys == {"time": "night", "goal": "through_night"} and lesson.viewer == "neko"
    assert again.learn("木は斧で切る", "", {}, False, "failure").id == "L2"


def test_recalled_lessons_are_shown_as_unverified_memories():
    lesson = Lesson("L1", "夜は家で寝る", condition="夜になったとき", source="viewer:neko", used=3, helped=2)
    text = format_activity(Activity(lessons=(lesson,)))
    assert "思い出したこと" in text and "確かめていない" in text
    assert "夜は家で寝る（こういうとき: 夜になったとき）（nekoさんに教わった、効いた 2 回・効かなかった 0 回）" in text
    assert "思い出したこと" not in format_activity(Activity())
