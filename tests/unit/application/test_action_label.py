"""実行する手の視聴者向けの動作（docs/design/91）。"""

import re
from pathlib import Path

import pytest

from ailoveshen.application.use_cases.action_label import (
    CANDIDATE_VERBS,
    DIRECT_TOOLS,
    action_label,
)
from ailoveshen.application.use_cases.tool_catalog import ACTION_TOOLS
from ailoveshen.domain.value_objects import Candidate, ToolCall

ROOT = Path(__file__).resolve().parents[3]


def suggest(cid: str, **description) -> tuple[ToolCall, tuple[Candidate, ...]]:
    return ToolCall("do_suggestion", {"id": cid}), (Candidate(cid, description),)


def test_digging_logs_is_gathering_and_other_blocks_are_digging():
    call, cands = suggest("dig oak_log at 1,70,2", verb="dig", target="oak_log")
    assert action_label(call, cands) == "オークの原木を集める"
    call, cands = suggest("dig dirt at 1,70,2", verb="dig", target="dirt")
    assert action_label(call, cands) == "dirtを掘る"


def test_building_the_house_names_the_block_and_the_progress():
    call, cands = suggest(
        "place oak_log (start the house here)",
        verb="place_plan",
        target="oak_log of the house",
        progress="1/115",
    )
    assert action_label(call, cands) == "家づくり: オークの原木を置く（1/115）"


def test_building_a_named_build_uses_its_name_and_clearing_is_not_placing():
    call, cands = suggest(
        "place oak_planks at 1,70,2 for barn",
        verb="place_plan",
        target="oak_planks of the build barn",
        progress="3/20",
    )
    assert action_label(call, cands) == "「barn」づくり: オークの板材を置く（3/20）"
    call, cands = suggest(
        "clear the block at 1,70,2 for barn",
        verb="place_plan",
        target="air of the build barn",
        progress="3/20",
    )
    assert action_label(call, cands) == "「barn」の場所のブロックをどける（3/20）"


def test_smelt_candidate_for_taking_from_the_furnace_is_not_smelting():
    call, cands = suggest("take iron_ingot from the furnace at 1,70,2", verb="smelt", target="iron_ingot")
    assert action_label(call, cands) == "かまどから鉄インゴットを取り出す"
    call, cands = suggest("smelt 4 raw_iron into iron_ingot", verb="smelt", target="iron_ingot")
    assert action_label(call, cands) == "鉄インゴットを精錬する"


def test_explore_names_the_direction():
    call, cands = suggest("explore north", verb="explore", target="north")
    assert action_label(call, cands) == "北へ探検する"


def test_direct_tool_calls_use_their_arguments():
    assert action_label(ToolCall("craft", {"item": "oak_planks"})) == "オークの板材をクラフトする"
    assert action_label(ToolCall("place", {"item": "dirt", "x": 1, "y": 2, "z": 3})) == "dirtを置く"
    assert action_label(ToolCall("go_home")) == "家に帰る"
    assert action_label(ToolCall("sleep")) == "ベッドで寝る"
    assert action_label(ToolCall("dig", {"x": 1, "y": 2, "z": 3})) == "ブロックを掘る"
    assert action_label(ToolCall("dig", {"x": 1, "y": 2, "z": 3, "demolish": True})) == "建物を壊す"
    assert action_label(ToolCall("run_skill", {"name": "hunt"})) == "技「hunt」を実行する"


def test_an_unknown_suggestion_or_verb_gives_no_label_so_the_goal_label_stays():
    assert action_label(ToolCall("do_suggestion", {"id": "nope"}), ()) == ""
    call, cands = suggest("something", verb="never_heard_of")
    assert action_label(call, cands) == ""
    assert action_label(ToolCall("brand_new_tool")) == ""


def test_every_bridge_candidate_verb_has_a_label():
    """ブリッジに verb を足したのに、ここに足し忘れると小目標の欄が述語のままになる。"""
    source = (ROOT / "minecraft-bridge" / "src").glob("*.mjs")
    verbs = set()
    for path in source:
        verbs |= set(re.findall(r"verb: '([a-z_]+)'", path.read_text(encoding="utf-8")))
    missing = verbs - CANDIDATE_VERBS
    assert not missing, f"no label for the verbs {sorted(missing)}"


def test_every_direct_action_tool_has_a_label():
    skipped = {"do_suggestion"}
    assert set(ACTION_TOOLS) - skipped <= DIRECT_TOOLS


@pytest.mark.parametrize("verb", sorted(CANDIDATE_VERBS))
def test_labels_are_japanese_and_nonempty_with_minimal_candidate(verb):
    call, cands = suggest(f"{verb} x", verb=verb, target="oak_log", item="oak_log")
    label = action_label(call, cands)
    assert label and re.search(r"[ぁ-んァ-ン一-龥]", label)
