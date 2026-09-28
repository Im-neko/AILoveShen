"""検索（一覧から検索語に合うキーを Jev が選ぶ、docs/design/47 §3–4）のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.search import (
    KeySearch,
    SearchEntry,
    WorldSearch,
    facility_entries,
    memory_entries,
    rank,
)
from ailoveshen.domain.exceptions import ActionSelectionError, GameBridgeError
from ailoveshen.domain.value_objects import FastAnswer, FastQuestionKind, FastVerdict

ENTRIES = [
    SearchEntry("home", "home (done); 3m east", '{"name":"home"}'),
    SearchEntry(
        "home_farm", "farm (done); crops: wheat, carrots; 12m north", '{"name":"home_farm"}'
    ),
    SearchEntry(
        "tree_farm_1", "tree_farm (done); saplings and trees (logs)", '{"name":"tree_farm_1"}'
    ),
]


def _search(value=None, confidence=0.9, error=None):
    judge = AsyncMock()
    if error:
        judge.ask.side_effect = error
    else:
        judge.ask.return_value = FastVerdict(
            answers={"match": FastAnswer("match", value, confidence)}, elapsed_ms=30
        )
    recorder = Mock()
    return KeySearch(judge, recorder=recorder, timeout_seconds=0.1), judge, recorder


def test_rank_puts_word_and_japanese_overlap_first_and_keeps_the_order_on_ties():
    assert [e.key for e in rank("wheat", ENTRIES)] == ["home_farm", "home", "tree_farm_1"]
    entries = [SearchEntry("a", "石の採掘場", ""), SearchEntry("b", "小麦の畑", "")]
    assert rank("小麦畑", entries)[0].key == "b"


@pytest.mark.asyncio
async def test_jev_picks_the_key_and_its_detail_is_returned_and_recorded():
    search, judge, recorder = _search("B")
    result = await search.find("search_facility", "小麦", ENTRIES, "facility")

    assert result.by == "jev" and result.keys == ("home_farm",)
    assert result.text == '{"name":"home_farm"}'
    state, (question,) = judge.ask.call_args.args
    assert state == {"query": "小麦"}
    assert question.kind == FastQuestionKind.CHOICE
    assert "none" in question.criteria and question.criteria["B"].startswith("home_farm:")
    entry = recorder.record.call_args.args[0]
    assert entry["tool"] == "search_facility" and entry["jev"] == "B" and entry["by"] == "jev"


@pytest.mark.asyncio
async def test_none_says_nothing_matches_with_the_nearest_names():
    search, _, _ = _search("none")
    result = await search.find("search_facility", "iron farm", ENTRIES, "facility")
    assert result.by == "none" and result.keys == ()
    assert "no facility matches" in result.text and "home" in result.text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"value": "A", "confidence": 0.3},  # 迷っている
        {"value": "Z"},  # 一覧にない
        {"error": ActionSelectionError("Jev down")},
        {"error": TimeoutError()},
    ],
)
@pytest.mark.asyncio
async def test_without_a_sure_answer_the_closest_by_words_are_returned_as_unsure(kwargs):
    search, _, _ = _search(**kwargs)
    result = await search.find("search_facility", "wheat", ENTRIES, "facility")
    assert result.by == "fallback"
    assert result.keys[0] == "home_farm" and len(result.keys) == 3
    assert result.text.startswith("not sure")


@pytest.mark.asyncio
async def test_only_the_closest_entries_are_shown_to_jev():
    search, judge, _ = _search("A")
    search = KeySearch(judge, choices=2)
    await search.find("search_facility", "tree", ENTRIES, "facility")
    criteria = judge.ask.call_args.args[1][0].criteria
    assert set(criteria) == {"A", "B", "none"} and criteria["A"].startswith("tree_farm_1")


@pytest.mark.asyncio
async def test_an_empty_list_is_not_asked():
    search, judge, _ = _search("A")
    result = await search.find("search_memory", "iron", [], "remembered place")
    assert result.by == "empty" and "no remembered place is known yet" in result.text
    judge.ask.assert_not_awaited()


def test_facility_and_memory_entries_say_what_is_there():
    facilities = facility_entries(
        [
            {
                "name": "home_farm",
                "kind": "farm",
                "status": "done",
                "distance_m": 12,
                "direction": "north",
                "contents": {"crops": {"wheat": {"planted": 9, "ripe": 3}}},
            },
            {
                "name": "shed",
                "kind": "build",
                "status": "building",
                "purpose": "倉庫",
                "distance_m": 5,
                "direction": "east",
                "contents": {"placed": 3, "total": 40},
            },
        ]
    )
    assert facilities[0].summary == "farm (done); crops: wheat; 12m north"
    assert facilities[1].summary == "build (building); 倉庫; 3/40 blocks placed; 5m east"
    assert '"ripe":3' in facilities[0].detail

    memory = memory_entries(
        {
            "places": [
                {
                    "kind": "iron_ore",
                    "count": 4,
                    "x": 1,
                    "y": 30,
                    "z": 2,
                    "distance_m": 80,
                    "direction": "west",
                    "minutes_ago": 5,
                    "gives": ["raw_iron"],
                }
            ],
            "chests": [
                {
                    "x": 3,
                    "y": 64,
                    "z": 4,
                    "distance_m": 2,
                    "direction": "south",
                    "contents": {"wheat": 3, "cobblestone": 40},
                }
            ],
            "furnaces": [],
            "deaths": [
                {
                    "x": 9,
                    "y": 60,
                    "z": 9,
                    "distance_m": 200,
                    "direction": "north",
                    "minutes_ago": 30,
                }
            ],
        }
    )
    assert [e.key for e in memory] == ["iron_ore@1,30,2", "chest@3,64,4", "death@9,60,9"]
    assert "gives raw_iron" in memory[0].summary
    assert memory[1].summary.startswith("chest with cobblestone 40, wheat 3")


@pytest.mark.asyncio
async def test_world_search_reads_the_list_from_the_bridge():
    bridge = AsyncMock()
    bridge.memory.return_value = {"places": [], "chests": [], "furnaces": [], "deaths": []}
    ok, text = await WorldSearch(bridge, KeySearch(None)).run("search_memory", {"query": "iron"})
    assert ok and "no remembered place" in text

    bridge.facilities.side_effect = GameBridgeError("down")
    ok, text = await WorldSearch(bridge, KeySearch(None)).run("search_facility", {"query": "x"})
    assert not ok and "could not read" in text

    ok, text = await WorldSearch(bridge, KeySearch(None)).run("search_facility", {"query": " "})
    assert not ok and "query is empty" in text
