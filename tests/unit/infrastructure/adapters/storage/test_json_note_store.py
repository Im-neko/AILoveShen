"""JsonNoteStore のテスト。"""

from ailoveshen.domain.entities import Notebook
from ailoveshen.domain.value_objects import NoteKind
from ailoveshen.infrastructure.adapters.storage.json_note_store import JsonNoteStore


class TestJsonNoteStore:
    """JsonNoteStore のテスト。"""

    def test_nothing_saved_yet(self, tmp_path):
        """ファイルがなければ None。"""
        assert JsonNoteStore(tmp_path / "notes.json").load() is None

    def test_round_trip(self, tmp_path):
        """メモ（種類と根拠も）と次の id を保存して読み戻す。"""
        book = Notebook()
        book.add(NoteKind.LESSON, "北は崖ばかり", day=2, about="explored(32): stuck")
        book.add(NoteKind.VIEWER, "羊が好き", day=3, about="neko")
        book.drop("n1")
        store = JsonNoteStore(tmp_path / "data" / "notes.json")
        store.save(book)

        saved = store.load()

        assert saved.notes == book.notes
        assert saved.next_id == 3
