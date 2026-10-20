"""外した手の台帳の JSON 保存（設計書 184）。"""

from ailoveshen.infrastructure.adapters.storage.json_hand_store import JsonHandStore


def test_it_saves_and_loads_a_snapshot_and_starts_empty_when_there_is_no_file(tmp_path):
    store = JsonHandStore(tmp_path / "data" / "hands.json")
    assert store.load() is None
    store.save({"fails": {"a": [[1.0, "why"]]}, "until": {"a": 5.0}, "shown": {"a": ["a", "why", 3]}})
    assert store.load() == {"fails": {"a": [[1.0, "why"]]}, "until": {"a": 5.0}, "shown": {"a": ["a", "why", 3]}}


def test_an_unreadable_file_is_ignored(tmp_path):
    path = tmp_path / "hands.json"
    path.write_text("{ not json", encoding="utf-8")
    assert JsonHandStore(path).load() is None
