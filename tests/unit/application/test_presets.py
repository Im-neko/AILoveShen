"""喋らせる文章のプリセット（設計書 54）のテスト。"""

import itertools

import pytest

from ailoveshen.application.ports.output.preset_store import IPresetStore, SpeechPreset
from ailoveshen.application.use_cases.presets import (
    MAX_TEXT,
    PresetError,
    SpeechPresets,
    clean_text,
)
from ailoveshen.infrastructure.adapters.storage import JsonPresetStore


class MemoryStore(IPresetStore):
    def __init__(self, presets=()):
        self.presets = tuple(presets)
        self.saved = 0

    def load(self):
        return self.presets

    def save(self, presets):
        self.presets = tuple(presets)
        self.saved += 1


def _presets(store=None):
    ids = itertools.count(1)
    return SpeechPresets(store or MemoryStore(), new_id=lambda: f"p{next(ids)}")


def test_add_keeps_the_order_and_saves_every_change():
    store = MemoryStore()
    presets = _presets(store)
    hello = presets.add("はじまり", "みなさんこんにちは！", "挨拶")
    presets.add("おわり", "今日はありがとう！", "挨拶")
    assert [p.title for p in presets.all()] == ["はじまり", "おわり"]
    assert hello.category == "挨拶" and store.saved == 2
    presets.update("p1", "はじまり2", "こんにちは！", "")
    presets.remove("p2")
    assert [(p.id, p.title, p.text) for p in store.presets] == [("p1", "はじまり2", "こんにちは！")]


def test_an_empty_title_takes_the_head_of_the_text():
    preset = _presets().add("", "今日は畑を広げます。よろしくね", "")
    assert preset.title == "今日は畑を広げます。よろしくね"


def test_bad_text_and_unknown_ids_are_refused():
    presets = _presets()
    with pytest.raises(PresetError, match="empty"):
        presets.add("x", "   ")
    with pytest.raises(PresetError, match="over"):
        presets.add("x", "あ" * (MAX_TEXT + 1))
    with pytest.raises(PresetError, match="no preset"):
        presets.get("nope")
    with pytest.raises(PresetError, match="no preset"):
        presets.remove("nope")
    assert clean_text("  こんにちは \n") == "こんにちは"


def test_saved_presets_come_back_after_a_restart(tmp_path):
    path = tmp_path / "data" / "presets.json"
    first = SpeechPresets(JsonPresetStore(path), new_id=lambda: "a1")
    first.add("さいご", "ばいばい！\nまたね", "挨拶")
    again = SpeechPresets(JsonPresetStore(path))
    assert again.all() == (SpeechPreset("a1", "さいご", "ばいばい！\nまたね", "挨拶"),)
