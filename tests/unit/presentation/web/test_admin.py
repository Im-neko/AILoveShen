"""管理画面（設計書 54）の API のテスト。"""

import pytest

pytest.importorskip("fastapi", reason="ailoveshen[stream] not installed")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ailoveshen.application.ports.output.preset_store import IPresetStore  # noqa: E402
from ailoveshen.application.use_cases.presets import SpeechPresets  # noqa: E402
from ailoveshen.presentation.web.admin import LOOPBACK, AdminPanel  # noqa: E402

# TestClient は testclient から、Host は testserver で来る
TESTING = LOOPBACK | {"testclient", "testserver"}

GUARD = {"X-Admin-Request": "1"}


class MemoryStore(IPresetStore):
    def __init__(self):
        self.presets = ()

    def load(self):
        return self.presets

    def save(self, presets):
        self.presets = tuple(presets)


class Spy:
    def __init__(self, spoken=True):
        self.said, self.instructed, self.rethought = [], [], []
        self.resets = 0
        self.started = True
        self.spoken = spoken

    async def speak(self, text):
        self.said.append(text)
        return self.spoken

    async def instruct(self, text, speak_reply):
        self.instructed.append((text, speak_reply))
        return "了解、木を集めるね"

    def rethink(self, reason):
        self.rethought.append(reason)
        return True

    async def reset(self):
        self.resets += 1
        return self.started


def _client(spy=None, allowed_hosts=TESTING):
    spy = spy or Spy()
    panel = AdminPanel(
        SpeechPresets(MemoryStore(), new_id=iter(["a1", "a2", "a3"]).__next__),
        speak=spy.speak,
        instruct=spy.instruct,
        rethink=spy.rethink,
        reset=spy.reset,
        allowed_hosts=allowed_hosts,
    )
    app = FastAPI()
    panel.install(app, lambda: {"goal": {"label": "木を集める"}, "objectives": []})
    return TestClient(app), spy


def test_the_page_and_the_state_are_served():
    client, _ = _client()
    page = client.get("/admin")
    assert page.status_code == 200 and "配信の管理画面" in page.text
    state = client.get("/api/admin/state").json()
    assert state["goal"]["label"] == "木を集める"
    assert state["presets"] == [] and state["log"] == []


def test_any_words_are_spoken_and_logged():
    client, spy = _client()
    r = client.post("/api/admin/say", json={"text": "  みなさん、おはよう！ "}, headers=GUARD)
    assert r.json() == {"ok": True, "spoken": True}
    assert spy.said == ["みなさん、おはよう！"]
    log = client.get("/api/admin/state").json()["log"]
    assert log[0]["kind"] == "say" and log[0]["text"] == "みなさん、おはよう！"
    assert client.post("/api/admin/say", json={"text": " "}, headers=GUARD).status_code == 400


def test_without_a_voice_it_says_so():
    client, _ = _client(Spy(spoken=False))
    assert client.post("/api/admin/say", json={"text": "こんにちは"}, headers=GUARD).json()["spoken"] is False
    assert "オフ" in client.get("/api/admin/state").json()["log"][0]["result"]


def test_presets_are_made_edited_used_and_deleted():
    client, spy = _client()
    made = client.post(
        "/api/admin/presets",
        json={"title": "はじまり", "text": "こんにちは！", "category": "挨拶"},
        headers=GUARD,
    ).json()
    assert made == {"id": "a1", "title": "はじまり", "text": "こんにちは！", "category": "挨拶"}
    client.put(
        "/api/admin/presets/a1",
        json={"title": "はじまり", "text": "みなさんこんにちは！", "category": "挨拶"},
        headers=GUARD,
    )
    assert client.post("/api/admin/presets/a1/say", headers=GUARD).json()["ok"] is True
    assert spy.said == ["みなさんこんにちは！"]
    assert client.get("/api/admin/state").json()["log"][0]["kind"] == "preset:はじまり"
    assert client.delete("/api/admin/presets/a1", headers=GUARD).json() == {"ok": True}
    assert client.get("/api/admin/state").json()["presets"] == []
    assert client.post("/api/admin/presets/a1/say", headers=GUARD).status_code == 404
    assert client.put("/api/admin/presets/zz", json={"text": "x"}, headers=GUARD).status_code == 404


def test_an_instruction_goes_to_the_streamer_and_its_reply_comes_back():
    client, spy = _client()
    r = client.post(
        "/api/admin/instruct", json={"text": "先に木を集めて", "reply": False}, headers=GUARD
    ).json()
    assert r == {"ok": True, "reply": "了解、木を集めるね"}
    assert spy.instructed == [("先に木を集めて", False)]
    assert client.post("/api/admin/instruct", json={"text": ""}, headers=GUARD).status_code == 400
    assert client.post("/api/admin/rethink", json={"reason": "夜になる"}, headers=GUARD).json() == {"ok": True}
    assert spy.rethought == ["夜になる"]


def test_writes_need_the_guard_header_and_only_this_machine_gets_in():
    client, spy = _client()
    assert client.post("/api/admin/say", json={"text": "こんにちは"}).status_code == 403  # ヘッダーなし
    assert spy.said == []
    other = {**GUARD, "Origin": "http://evil.example"}
    assert client.post("/api/admin/say", json={"text": "こんにちは"}, headers=other).status_code == 403
    rebind = {**GUARD, "Host": "evil.example"}
    assert client.get("/api/admin/state", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/admin/say", json={"text": "こんにちは"}, headers=rebind).status_code == 403
    same = {**GUARD, "Origin": "http://127.0.0.1:8765", "Host": "127.0.0.1:8765"}
    assert client.post("/api/admin/say", json={"text": "こんにちは"}, headers=same).status_code == 200
    # このマシンでないクライアント（ここでは testclient）は、ヘッダーがあっても入れない
    away, _ = _client(allowed_hosts=frozenset({"127.0.0.1", "testserver"}))
    assert away.get("/api/admin/state").status_code == 403
    assert away.post("/api/admin/say", json={"text": "こんにちは"}, headers=GUARD).status_code == 403


def test_resetting_the_goals_needs_the_header_and_is_logged():
    client, spy = _client()
    assert client.post("/api/admin/reset").status_code == 403
    assert spy.resets == 0
    assert client.post("/api/admin/reset", headers=GUARD).json() == {"ok": True}
    assert spy.resets == 1
    log = client.get("/api/admin/state").json()["log"][0]
    assert log["kind"] == "reset" and "戻した" in log["result"]


def test_resetting_before_the_play_starts_says_so():
    spy = Spy()
    spy.started = False
    client, _ = _client(spy)
    assert client.post("/api/admin/reset", headers=GUARD).json() == {"ok": False}
    assert client.get("/api/admin/state").json()["log"][0]["result"] == "プレイ前"


def _client_with_lessons():
    from ailoveshen.application.use_cases.lessons import LessonBook

    class Store:
        saved = None

        def load(self):
            return self.saved

        def save(self, lessons, next_id):
            self.saved = (list(lessons), next_id)

    book = LessonBook(Store(), mode="rules")
    book.learn("沼地では鉄が出にくい", "鉄を探すとき", {"goal": "have"}, True, "failure")
    spy = Spy()
    panel = AdminPanel(
        SpeechPresets(MemoryStore(), new_id=iter(["a1"]).__next__),
        speak=spy.speak,
        instruct=spy.instruct,
        rethink=spy.rethink,
        reset=spy.reset,
        allowed_hosts=TESTING,
        lessons=book,
    )
    app = FastAPI()
    panel.install(app, lambda: {"goal": None, "objectives": []})
    return TestClient(app), book


def test_lessons_are_listed_added_and_removed_from_the_admin_page():
    client, book = _client_with_lessons()
    listed = client.get("/api/admin/lessons").json()
    assert listed["enabled"] and listed["lessons"][0]["text"] == "沼地では鉄が出にくい"
    assert listed["lessons"][0]["situation"] == {"goal": "have"}
    added = client.post("/api/admin/lessons", json={"text": "鉄は y=16 付近に多い", "condition": "鉄を探すとき"}, headers=GUARD)
    assert added.status_code == 200
    assert [n.source for n in book.lessons][-1] == "viewer:運営"
    assert client.delete("/api/admin/lessons/L1", headers=GUARD).json() == {"ok": True}
    assert [n.text for n in book.lessons] == ["鉄は y=16 付近に多い"]
    assert client.delete("/api/admin/lessons/L1", headers=GUARD).status_code == 404


def test_lesson_writes_need_the_guard_header_and_a_text():
    client, _ = _client_with_lessons()
    assert client.post("/api/admin/lessons", json={"text": "x"}).status_code == 403
    assert client.delete("/api/admin/lessons/L1").status_code == 403
    assert client.post("/api/admin/lessons", json={"text": "  "}, headers=GUARD).status_code == 400


def test_without_a_lesson_book_the_list_is_empty_and_writes_are_refused():
    client, _ = _client()
    assert client.get("/api/admin/lessons").json() == {"enabled": False, "lessons": []}
    assert client.post("/api/admin/lessons", json={"text": "x"}, headers=GUARD).status_code == 409
