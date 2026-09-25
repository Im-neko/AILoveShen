"""自分のメモの編集（語彙と NoteKeeper）のテスト。"""

from unittest.mock import Mock

import pytest

from ailoveshen.application.ports.output.note_store import SavedNotes
from ailoveshen.application.use_cases.goal_vocabulary import (
    NoteChange,
    NoteOp,
    goal_schema,
    parse_note_changes,
)
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.domain.entities import Notebook
from ailoveshen.domain.value_objects import GoalPredicate, Note, NoteKind

GOALS = ["explored(32): goal explored(32) is stuck", "have(food, 4): goal have(food, 4) is met"]


def _add(kind: NoteKind, text: str = "メモ", **kwargs) -> NoteChange:
    return NoteChange(op=NoteOp.ADD, kind=kind, text=text, **kwargs)


class TestNoteVocabulary:
    """note_changes のスキーマとパースのテスト。"""

    def _notes_schema(self, **kwargs):
        schema = goal_schema([GoalPredicate.HAVE], ["m1"], **kwargs)
        return schema["properties"]["note_changes"]["items"]["properties"]

    def test_the_schema_offers_only_the_grounds_shown(self):
        """lesson は示した小目標があるとき、viewer は会話に視聴者がいるときだけ書ける。"""
        bare = self._notes_schema()
        assert bare["kind"]["enum"] == ["plan"]
        assert "goal" not in bare and "viewer" not in bare and "id" not in bare

        full = self._notes_schema(note_ids=["n1"], goals=2, viewers=["neko"])
        assert full["kind"]["enum"] == ["lesson", "plan", "viewer"]
        assert full["goal"]["maximum"] == 2
        assert full["viewer"]["enum"] == ["neko"]
        assert full["id"]["enum"] == ["n1"]

    def test_parse_note_changes(self):
        """add は種類と本文と根拠、drop と keep は id を読む。"""
        changes = parse_note_changes(
            {
                "note_changes": [
                    {"op": "add", "kind": "lesson", "text": " 北は崖 ", "goal": 1},
                    {"op": "add", "kind": "viewer", "text": "羊が好き", "viewer": "neko"},
                    {"op": "drop", "id": "n2"},
                    {"op": "keep", "id": "n3"},
                ]
            }
        )
        assert changes == (
            _add(NoteKind.LESSON, "北は崖", goal=1),
            _add(NoteKind.VIEWER, "羊が好き", viewer="neko"),
            NoteChange(op=NoteOp.DROP, note_id="n2"),
            NoteChange(op=NoteOp.KEEP, note_id="n3"),
        )
        assert parse_note_changes({}) == ()

    def test_malformed_changes_are_rejected(self):
        """op、種類、id がなければ理由をつけて断る。"""
        with pytest.raises(ValueError, match="op must be one of"):
            parse_note_changes({"note_changes": [{"op": "edit"}]})
        with pytest.raises(ValueError, match="needs a kind"):
            parse_note_changes({"note_changes": [{"op": "add", "text": "x"}]})
        with pytest.raises(ValueError, match="drop needs the id"):
            parse_note_changes({"note_changes": [{"op": "drop"}]})


class TestNoteKeeper:
    """NoteKeeper のテスト。"""

    @pytest.fixture
    def store(self):
        s = Mock()
        s.load.return_value = None
        return s

    def test_a_lesson_keeps_the_small_goal_it_was_learned_from(self, store):
        """lesson は番号ではなく小目標の文を根拠に持つ（番号は再起動や時間でずれる）。"""
        book = Notebook()
        NoteKeeper(store).commit(
            book, [_add(NoteKind.LESSON, "北は崖ばかり", goal=1)], 3, GOALS, []
        )
        assert book.notes[0].about == GOALS[0]
        store.save.assert_called_once_with(book)

    def test_grounds_must_be_ones_that_were_shown(self, store):
        """示していない小目標の番号や、会話にいない視聴者は根拠にできない。"""
        keeper = NoteKeeper(store)
        with pytest.raises(ValueError, match=r"small goal shown \(1-2\)"):
            keeper.commit(Notebook(), [_add(NoteKind.LESSON, goal=3)], 3, GOALS, [])
        with pytest.raises(ValueError, match="bob is not in the conversation"):
            keeper.commit(Notebook(), [_add(NoteKind.VIEWER, viewer="bob")], 3, GOALS, ["neko"])

    def test_nothing_changes_when_one_edit_fails(self, store):
        """編集のどれかが使えなければ、どれも反映しない。"""
        book = Notebook()
        with pytest.raises(ValueError):
            NoteKeeper(store).commit(
                book, [_add(NoteKind.PLAN), NoteChange(op=NoteOp.DROP, note_id="n9")], 3, [], []
            )
        assert book.notes == ()
        store.save.assert_not_called()

    def test_notes_need_to_know_the_day(self, store):
        """まだ何日目かわからなければ書けない（寿命を数えられない）。"""
        with pytest.raises(ValueError, match="day is not known"):
            NoteKeeper(store).commit(Notebook(), [_add(NoteKind.PLAN)], None, [], [])

    def test_expire_saves_only_when_something_expired(self, store):
        """寿命が過ぎたものがあれば消して保存する。何日目かわからなければ何もしない。"""
        book = Notebook()
        book.add(NoteKind.PLAN, "畑", day=1)
        keeper = NoteKeeper(store)
        keeper.expire(book, None)
        keeper.expire(book, 4)
        store.save.assert_not_called()
        keeper.expire(book, 5)
        assert book.notes == ()
        store.save.assert_called_once_with(book)

    def test_load_restores_the_saved_notes(self, store):
        """保存したメモと次の id を戻す。"""
        note = Note("n2", NoteKind.PLAN, "畑", 1, 4)
        store.load.return_value = SavedNotes(notes=(note,), next_id=3)
        book = Notebook()
        NoteKeeper(store).load(book, 2)
        assert book.notes == (note,) and book.next_id == 3

    def test_notes_from_a_later_day_belong_to_another_world(self, store):
        """今日より後の日に書いたメモは、作り直す前のワールドのもの: 捨てる。"""
        old = Note("n1", NoteKind.PLAN, "畑", 9, 12)
        store.load.return_value = SavedNotes(notes=(old,), next_id=2)
        book = Notebook()
        NoteKeeper(store).load(book, 0)
        assert book.notes == ()
        store.save.assert_called_once_with(book)
