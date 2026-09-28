"""検索（一覧から検索語に合うキーを Jev が選ぶ、docs/design/47 §3–4）のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.search import (
    KeySearch,
    WorldSearch,
    facility_entries,
    item_entries,
    memory_entries,
    rank,
)
from ailoveshen.application.use_cases.tool_catalog import ACTION_TOOLS, QUERY_TOOLS, SKILL_TOOLS
from ailoveshen.domain.exceptions import ActionSelectionError, GameBridgeError
from ailoveshen.domain.value_objects import FastAnswer, FastQuestionKind, FastVerdict
from ailoveshen.infrastructure.adapters.prompts.abilities import load_abilities

FACILITIES = [
    {"name": "home", "kind": "home", "status": "done", "distance_m": 3, "direction": "E"},
    {
        "name": "home_farm",
        "kind": "farm",
        "status": "done",
        "distance_m": 12,
        "direction": "N",
        "contents": {"crops": {"wheat": {"planted": 9, "ripe": 3, "ja": "小麦"}}},
    },
    {
        "name": "tree_farm_1",
        "kind": "tree_farm",
        "status": "done",
        "distance_m": 30,
        "direction": "W",
        "contents": {"saplings": 2, "trees": 1, "empty": 0},
    },
]
ENTRIES = facility_entries(FACILITIES)


def _place(kind, ja, distance, gives=(), gives_ja=()):
    return {
        "kind": kind,
        "ja": ja,
        **({"gives": list(gives), "gives_ja": list(gives_ja)} if gives else {}),
        "places_known": 1,
        "nearest": [
            {
                "count": 3,
                "x": distance,
                "y": 30,
                "z": 0,
                "distance_m": distance,
                "direction": "E",
                "minutes_ago": 5,
            }
        ],
    }


# 実際の /memory に近い一覧: 種類が多い（16 を超える）
MEMORY = {
    "places": [_place(f"stone_{i}", "", 10 + i) for i in range(18)]
    + [
        _place("iron_ore", "鉄鉱石", 80, ["raw_iron"], ["鉄の原石"]),
        _place("sheep", "ヒツジ", 40),
    ],
    "chests": [
        {
            "x": 3,
            "y": 64,
            "z": 4,
            "distance_m": 2,
            "direction": "S",
            "contents": {"wheat": 3, "cobblestone": 40},
        }
    ],
    "furnaces": [],
    "deaths": [{"x": 9, "y": 60, "z": 9, "distance_m": 200, "direction": "N", "minutes_ago": 30}],
}


def _search(value=None, confidence=0.9, error=None, **kwargs):
    judge = AsyncMock()
    if error:
        judge.ask.side_effect = error
    else:
        judge.ask.return_value = FastVerdict(
            answers={"match": FastAnswer("match", value, confidence)}, elapsed_ms=30
        )
    recorder = Mock()
    return KeySearch(judge, recorder=recorder, timeout_seconds=0.1, **kwargs), judge, recorder


def test_japanese_queries_meet_the_japanese_names_in_english_summaries():
    """一覧の説明は英語に日本語名を添えたもの。日本語の検索語（漢字 1 文字も）で上に来る。"""
    assert rank("小麦の畑", ENTRIES)[0][1].key == "home_farm"
    assert rank("植林場", ENTRIES)[0][1].key == "tree_farm_1"
    memory = memory_entries(MEMORY)
    assert rank("鉄", memory)[0][1].key == "iron_ore"
    assert rank("wheat farm", ENTRIES)[0][1].key == "home_farm"
    assert [n for n, _ in rank("nothing like it", ENTRIES)] == [0, 0, 0]


@pytest.mark.asyncio
async def test_jev_picks_the_key_and_its_detail_is_returned_and_recorded():
    search, judge, recorder = _search("A")
    result = await search.find("search_facility", "小麦", ENTRIES, "facility")

    assert result.by == "jev" and result.keys == ("home_farm",)
    assert '"ripe":3' in result.text
    state, (question,) = judge.ask.call_args.args
    assert state == {"query": "小麦"}
    assert question.kind == FastQuestionKind.CHOICE
    assert "none" in question.criteria and question.criteria["A"].startswith("home_farm:")
    entry = recorder.record.call_args.args[0]
    assert entry["tool"] == "search_facility" and entry["jev"] == "A" and entry["by"] == "jev"


@pytest.mark.asyncio
async def test_the_exact_key_is_returned_without_asking():
    search, judge, _ = _search("B")
    result = await search.find("search_facility", "Home Farm", ENTRIES, "facility")
    assert result.by == "exact" and result.keys == ("home_farm",)
    judge.ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_none_says_nothing_matches_with_the_nearest_names():
    search, _, _ = _search("none")
    result = await search.find("search_facility", "iron farm", ENTRIES, "facility")
    assert result.by == "none" and result.keys == ()
    assert "no facility matches" in result.text and "home_farm" in result.text


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
async def test_without_a_sure_answer_only_entries_sharing_words_are_returned(kwargs):
    search, _, _ = _search(**kwargs)
    result = await search.find("search_facility", "小麦", ENTRIES, "facility")
    assert result.by == "fallback" and result.keys == ("home_farm",)
    assert result.text.startswith("not sure") and '"ripe":3' in result.text


@pytest.mark.asyncio
async def test_unsure_with_no_shared_word_gives_names_not_coordinates():
    """語が重ならない候補の座標を返すと、見当違いの場所へ歩く。"""
    search, _, _ = _search("A", confidence=0.2)
    result = await search.find("search_facility", "ダイヤ", ENTRIES, "facility")
    assert result.by == "fallback" and result.keys == ()
    assert "home_farm" in result.text and "distance_m" not in result.text


@pytest.mark.asyncio
async def test_a_long_list_with_no_shared_word_says_it_was_cut():
    """16 件を超え、どれとも語が重ならない: 正解が切り捨てられたかもしれないと言う。"""
    search, _, _ = _search("none")
    result = await search.find("search_memory", "羊", memory_entries(MEMORY), "remembered place")
    assert result.by == "none"
    assert "only 16 were compared" in result.text and "English name" in result.text


@pytest.mark.asyncio
async def test_only_the_closest_entries_are_shown_to_jev():
    search, judge, _ = _search("A", choices=2)
    await search.find("search_facility", "tree", ENTRIES, "facility")
    criteria = judge.ask.call_args.args[1][0].criteria
    assert set(criteria) == {"A", "B", "none"} and criteria["A"].startswith("tree_farm_1")


@pytest.mark.asyncio
async def test_an_empty_list_is_not_asked():
    search, judge, _ = _search("A")
    result = await search.find("search_memory", "iron", [], "remembered place")
    assert result.by == "empty" and "no remembered place is known yet" in result.text
    judge.ask.assert_not_awaited()


def test_facility_and_memory_entries_say_what_is_there_in_both_languages():
    assert ENTRIES[1].summary == "farm（畑 農場） (done); crops: wheat（小麦）; 12m N"
    build = facility_entries(
        [
            {
                "name": "shed",
                "kind": "build",
                "status": "building",
                "purpose": "倉庫",
                "distance_m": 5,
                "direction": "E",
                "contents": {"placed": 3, "total": 40},
            }
        ]
    )
    assert build[0].summary == "build（建物） (building); 倉庫; 3/40 blocks placed; 5m E"

    memory = {e.key: e for e in memory_entries(MEMORY)}
    assert memory["iron_ore"].summary.startswith("iron_ore（鉄鉱石）; gives raw_iron（鉄の原石）")
    assert '"x":80' in memory["iron_ore"].detail
    assert memory["chest@3,64,4"].summary.startswith(
        "chest（チェスト） with cobblestone 40, wheat 3"
    )
    assert "deaths" in memory


@pytest.mark.asyncio
async def test_world_search_reads_the_lists_from_the_bridge():
    bridge = AsyncMock()
    bridge.memory.return_value = {"places": [], "chests": [], "furnaces": [], "deaths": []}
    ok, text = await WorldSearch(bridge, KeySearch(None)).run("search_memory", {"query": "iron"})
    assert ok and "no remembered place" in text

    bridge.facilities.side_effect = GameBridgeError("down")
    ok, text = await WorldSearch(bridge, KeySearch(None)).run("search_facility", {"query": "x"})
    assert not ok and "could not read" in text

    ok, text = await WorldSearch(bridge, KeySearch(None)).run("search_facility", {"query": " "})
    assert not ok and "query is empty" in text


@pytest.mark.asyncio
async def test_item_info_reads_the_list_once_and_the_chosen_items_info():
    bridge = AsyncMock()
    bridge.items.return_value = [
        {"name": "iron_ingot", "ja": "鉄インゴット"},
        {"name": "iron_ore", "ja": "鉄鉱石"},
        {"name": "bread", "ja": "パン"},
    ]
    bridge.item_info.return_value = {
        "name": "iron_ingot",
        "how_to_get": {"smelt_from": ["raw_iron"]},
    }
    search, _, _ = _search("A")
    world = WorldSearch(bridge, search)

    ok, text = await world.run("item_info", {"query": "鉄インゴット"})
    assert ok and "raw_iron" in text
    bridge.item_info.assert_awaited_once_with("iron_ingot")
    await world.run("item_info", {"query": "iron_ingot"})  # 完全一致
    bridge.items.assert_awaited_once()
    assert item_entries([{"name": "bread", "ja": "パン"}])[0].summary == "パン"


@pytest.mark.asyncio
async def test_search_abilities_finds_the_way_to_do_it():
    abilities = load_abilities()
    search, judge, _ = _search("A")
    ok, text = await WorldSearch(AsyncMock(), search, abilities).run(
        "search_abilities", {"query": "畑を作る"}
    )
    assert ok and '"key":"make_farm"' in text
    assert judge.ask.call_args.args[1][0].criteria["A"].startswith("make_farm")


def test_the_ability_book_names_only_real_tools_and_entries():
    """帳が道具の変更に遅れない（機能を足したら帳にも足す: CLAUDE.md）。"""
    abilities = load_abilities()
    assert len(abilities) >= 20
    keys = {a["key"] for a in abilities}
    assert len(keys) == len(abilities)
    tools = {*ACTION_TOOLS, *QUERY_TOOLS, *SKILL_TOOLS}
    for a in abilities:
        assert a["title"] and a["words"] and a["when"] and a["how"], a["key"]
        assert set(a["tools"]) <= tools, (a["key"], set(a["tools"]) - tools)
        for tool in a["tools"]:
            assert tool in a["how"], (a["key"], tool)
        assert set(a.get("see", [])) <= keys, a["key"]
