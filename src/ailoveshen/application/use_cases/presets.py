"""
喋らせる文章のプリセット（docs/design/54_admin_panel.md）。

配信の始まりと終わりの挨拶など、決まった文章を保存しておき、管理画面から選んで喋らせる。
変わるたびに保存する。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from ailoveshen.application.ports.output.preset_store import IPresetStore, SpeechPreset

MAX_TEXT = 500
MAX_TITLE = 40
MAX_CATEGORY = 20
MAX_PRESETS = 200


class PresetError(ValueError):
    """プリセットとして保存できない（空、長すぎる、多すぎる、見つからない）。"""


def clean_text(text: str) -> str:
    """喋らせる文章として使えるなら整えたもの（前後の空白を取る）。使えなければ PresetError。"""
    text = (text or "").strip()
    if not text:
        raise PresetError("the text is empty")
    if len(text) > MAX_TEXT:
        raise PresetError(f"the text is over {MAX_TEXT} characters")
    return text


class SpeechPresets:
    """喋らせる文章の一覧。追加・直す・消す・並べ替え。変わるたびに保存する。"""

    def __init__(
        self,
        store: IPresetStore,
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex[:8],
    ) -> None:
        """
        Args:
            store: 保存先
            new_id: id の作り方（テストで差し替える）
        """
        self._store = store
        self._new_id = new_id
        self._presets: list[SpeechPreset] = list(store.load())

    def all(self) -> tuple[SpeechPreset, ...]:
        """並びどおりの一覧。"""
        return tuple(self._presets)

    def get(self, preset_id: str) -> SpeechPreset:
        """id のプリセット。なければ PresetError。"""
        found = next((p for p in self._presets if p.id == preset_id), None)
        if found is None:
            raise PresetError(f"no preset {preset_id}")
        return found

    def add(self, title: str, text: str, category: str = "") -> SpeechPreset:
        """末尾に足す。見出しが空なら本文の頭を見出しにする。"""
        if len(self._presets) >= MAX_PRESETS:
            raise PresetError(f"at most {MAX_PRESETS} presets")
        preset = self._make(self._new_id(), title, text, category)
        self._presets.append(preset)
        self._save()
        return preset

    def update(self, preset_id: str, title: str, text: str, category: str = "") -> SpeechPreset:
        """見出し・本文・分類を直す（並びは変えない）。"""
        index = self._index(preset_id)
        preset = self._make(preset_id, title, text, category)
        self._presets[index] = preset
        self._save()
        return preset

    def remove(self, preset_id: str) -> None:
        """消す。"""
        del self._presets[self._index(preset_id)]
        self._save()

    def _index(self, preset_id: str) -> int:
        for i, p in enumerate(self._presets):
            if p.id == preset_id:
                return i
        raise PresetError(f"no preset {preset_id}")

    @staticmethod
    def _make(preset_id: str, title: str, text: str, category: str) -> SpeechPreset:
        text = clean_text(text)
        title = (title or "").strip() or text.replace("\n", " ")[:MAX_TITLE]
        if len(title) > MAX_TITLE:
            raise PresetError(f"the title is over {MAX_TITLE} characters")
        category = (category or "").strip()
        if len(category) > MAX_CATEGORY:
            raise PresetError(f"the category is over {MAX_CATEGORY} characters")
        return SpeechPreset(id=preset_id, title=title, text=text, category=category)

    def _save(self) -> None:
        self._store.save(tuple(self._presets))
