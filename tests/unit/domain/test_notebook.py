"""自分のメモ（Note、Notebook）のテスト。"""

import pytest

from ailoveshen.domain.entities import Notebook
from ailoveshen.domain.value_objects import Note, NoteKind


class TestNote:
    """Note のテスト。"""

    def test_a_note_is_one_short_sentence(self):
        """本文は空にできず、80 字まで。"""
        with pytest.raises(ValueError, match="needs text"):
            Note("n1", NoteKind.PLAN, " ", 1, 4)
        with pytest.raises(ValueError, match="at most 80"):
            Note("n1", NoteKind.PLAN, "あ" * 81, 1, 4)
        assert Note("n1", NoteKind.PLAN, "あ" * 80, 1, 4).text == "あ" * 80

    def test_each_kind_has_its_grounds(self):
        """lesson は小目標、viewer は視聴者の名前が要り、plan には何も付かない。"""
        with pytest.raises(ValueError, match="small goal"):
            Note("n1", NoteKind.LESSON, "北は崖ばかり", 1, 4)
        with pytest.raises(ValueError, match="viewer's name"):
            Note("n1", NoteKind.VIEWER, "羊が好き", 1, 4)
        with pytest.raises(ValueError, match="no small goal or viewer"):
            Note("n1", NoteKind.PLAN, "畑を空ける", 1, 4, about="neko")


class TestNotebook:
    """Notebook のテスト（上限と寿命）。"""

    def test_notes_expire_after_their_lifetime(self):
        """書いた日から 3 日目までは残り、その次の日に消える。"""
        book = Notebook()
        note = book.add(NoteKind.PLAN, "引っ越したら畑を空ける", day=2)
        assert (note.id, note.written_day, note.expires_day) == ("n1", 2, 5)
        assert book.expire(5) == ()
        assert book.expire(6) == (note,)
        assert book.notes == ()

    def test_keep_counts_the_lifetime_again_from_today(self):
        """まだ正しいメモは、keep した日から寿命を数え直す。"""
        book = Notebook()
        book.add(NoteKind.PLAN, "畑を空ける", day=2)
        kept = book.keep("n1", day=4)
        assert kept.expires_day == 7
        assert book.expire(6) == ()

    def test_a_full_notebook_needs_a_drop_first(self):
        """上限に達したら、先に消さないと書けない。消した分は空く。"""
        book = Notebook(max_notes=2)
        book.add(NoteKind.PLAN, "一つ目", day=1)
        book.add(NoteKind.PLAN, "二つ目", day=1)
        with pytest.raises(ValueError, match="full"):
            book.add(NoteKind.PLAN, "三つ目", day=1)
        book.drop("n1")
        assert book.add(NoteKind.PLAN, "三つ目", day=1).id == "n3"

    def test_unknown_notes_are_rejected(self):
        """ないメモを消したり残したりはできない（あるメモの id を言う）。"""
        book = Notebook()
        book.add(NoteKind.PLAN, "一つ目", day=1)
        with pytest.raises(ValueError, match="no note n9 .notes: n1"):
            book.drop("n9")
        with pytest.raises(ValueError, match="no note n9"):
            book.keep("n9", day=1)

    def test_limits_must_be_positive(self):
        """上限は正の数。"""
        with pytest.raises(ValueError, match="max_notes"):
            Notebook(max_notes=0)
