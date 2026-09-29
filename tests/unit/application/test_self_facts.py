"""配信者が自分について話したことのテスト（docs/design/40_small_talk.md）。"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.ports.output.self_fact_store import ISelfFactStore, SelfFact
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.self_facts import SelfFacts
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.value_objects import CharacterProfile
from ailoveshen.infrastructure.adapters.storage import JsonSelfFactStore
from tests.unit.application.test_generate_response_use_case import _playing


class MemoryStore(ISelfFactStore):
    def __init__(self, facts=()):
        self.facts = tuple(facts)
        self.saves = 0

    def load(self):
        return self.facts

    def save(self, facts):
        self.facts = facts
        self.saves += 1


def _clock():
    now = [datetime(2026, 9, 27, tzinfo=UTC)]

    def tick():
        now[0] += timedelta(seconds=1)
        return now[0]

    return tick


def test_remember_new_topics_and_replace_the_same_topic():
    """話題ごとに 1 つ。同じ話題（空白・大文字小文字は問わない）は新しいもので置き換える。"""
    store = MemoryStore()
    facts = SelfFacts(store, clock=_clock())

    facts.remember([{"topic": "LoLのメイン", "fact": "シェンをよく使う"}], "ako")
    facts.remember([{"topic": "好きな食べ物", "fact": "いちご大福"}], "neko")
    facts.remember([{"topic": "LoL の メイン", "fact": "最近はオーンも練習してる"}], "ako")

    assert [(f.topic, f.fact, f.said_to) for f in facts.all()] == [
        ("好きな食べ物", "いちご大福", "neko"),
        ("LoL の メイン", "最近はオーンも練習してる", "ako"),
    ]
    assert store.facts == facts.all()


def test_ignore_what_is_not_a_fact_and_do_not_save_nothing_new():
    """形の合わないもの・空・長すぎるものは覚えない。何も変わらなければ保存しない。"""
    store = MemoryStore()
    facts = SelfFacts(store, clock=_clock())

    assert facts.remember(None, "neko") == ()
    assert (
        facts.remember(
            [{"topic": "", "fact": "x"}, "text", {"topic": "a" * 31, "fact": "x"}], "neko"
        )
        == ()
    )
    facts.remember([{"topic": "ランク", "fact": "ゴールド"}], "neko")
    facts.remember([{"topic": "ランク", "fact": "ゴールド"}], "neko")

    assert store.saves == 1


def test_forget_the_longest_unspoken_when_too_many():
    """多すぎたら、いちばん長く話していないものから忘れる。"""
    facts = SelfFacts(MemoryStore(), clock=_clock(), max_facts=2)
    for topic in ("a", "b", "c"):
        facts.remember([{"topic": topic, "fact": topic}], "neko")
    assert [f.topic for f in facts.all()] == ["b", "c"]


def test_json_store_keeps_them_across_restarts(tmp_path):
    """JSON に保存し、作り直しても（再起動・ワールドの作り直し）読み戻せる。"""
    path = tmp_path / "data" / "self_facts.json"
    SelfFacts(JsonSelfFactStore(path), clock=_clock()).remember(
        [{"topic": "LoLのメイン", "fact": "シェン"}], "ako"
    )
    again = SelfFacts(JsonSelfFactStore(path))
    assert [(f.topic, f.fact, f.said_to) for f in again.all()] == [("LoLのメイン", "シェン", "ako")]


@pytest.mark.asyncio
async def test_a_reply_shows_what_was_said_and_remembers_what_it_says_now():
    """返答のプロンプトには前に話したことを出し、返答の self_facts を覚える。"""
    before = SelfFact("好きな食べ物", "いちご大福", "neko", "2026-09-26T00:00:00+00:00")
    facts = SelfFacts(MemoryStore([before]))
    generator = AsyncMock()
    generator.generate_json.return_value = {
        "reply": "akoさん、シェンならクレッドにはレベル 2 まで無理しないかな！",
        "request": "none",
        "self_facts": [{"topic": "LoLのメイン", "fact": "シェンをよく使う（トップ）"}],
    }
    builder = AsyncMock()
    builder.build_system_prompt = lambda *a, **k: "system"
    prompts = []
    builder.build_chat_response_prompt = lambda **kw: prompts.append(kw) or "prompt"
    events = AsyncMock()
    use_case = GenerateResponseUseCase(
        text_generator=generator,
        prompt_builder=builder,
        event_publisher=events,
        conversation=Conversation(),
        character=CharacterProfile(),
        objectives=ObjectiveKeeper(bridge=AsyncMock(), event_publisher=events, store=AsyncMock()),
        self_facts=facts,
    )

    await use_case.execute(
        GenerateResponseRequest(
            user_name="ako", message="クレッド対面ってシェンだとどうしてる？", session=_playing()
        )
    )

    assert [f.topic for f in prompts[0]["self_facts"]] == ["好きな食べ物"]
    assert [(f.topic, f.said_to) for f in facts.all()] == [
        ("好きな食べ物", "neko"),
        ("LoLのメイン", "ako"),
    ]
