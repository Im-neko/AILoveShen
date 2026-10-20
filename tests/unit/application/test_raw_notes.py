"""自分用のメモ（設計書 196）: 書く・書き直す・いっぱいになったら足せず書き直しを選ばせる。"""

from ailoveshen.application.use_cases.raw_notes import MAX_NOTE_CHARS, MAX_NOTES, NoteBook


def test_a_note_is_added_in_order_and_the_same_text_is_not_added_twice():
    book = NoteBook()
    assert book.write("a") == (True, "noted as 1")
    assert book.write("b") == (True, "noted as 2")
    assert book.write("a") == (True, "already noted")
    assert book.notes == ("a", "b")


def test_a_note_can_be_rewritten_by_number_and_a_wrong_number_is_refused():
    book = NoteBook()
    book.write("a")
    assert book.write("c", replace=1) == (True, "note 1 rewritten")
    assert book.notes == ("c",)
    ok, why = book.write("d", replace=3)
    assert not ok and "no note 3" in why


def test_an_empty_or_too_long_note_is_refused():
    book = NoteBook()
    assert not book.write("  ")[0]
    ok, why = book.write("あ" * (MAX_NOTE_CHARS + 1))
    assert not ok and str(MAX_NOTE_CHARS) in why
    assert book.notes == ()


def test_when_full_nothing_is_dropped_and_a_rewrite_is_asked_for():
    book = NoteBook()
    for i in range(MAX_NOTES):
        book.write(f"n{i}")
    ok, why = book.write("new")
    assert not ok and "replace" in why and str(MAX_NOTES) in why
    assert book.notes[0] == "n0" and len(book.notes) == MAX_NOTES
    assert book.write("new", replace=1) == (True, "note 1 rewritten")
