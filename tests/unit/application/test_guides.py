"""手引き（文章のスキル。設計書 192）: SKILL.md の読み込み、一覧、read_guide。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import ToolChoice
from ailoveshen.application.use_cases.guides import GuideBook
from ailoveshen.application.use_cases.raw_play import RawAdvancePlay, RawStartPlay
from ailoveshen.application.use_cases.search import WorldSearch
from ailoveshen.application.use_cases.tool_catalog import READ_GUIDE, SEARCH_TOOLS, tool_specs
from ailoveshen.domain.entities import Mission, ObjectivePlan
from ailoveshen.domain.value_objects import Guide
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)
from ailoveshen.infrastructure.adapters.prompts.guides import load_guides, parse_guide

TEXT = "---\nname: sample\ndescription: 例の手引き。使う場面の一行\n---\n\n# 見出し\n\n本文\n"


def test_parse_reads_the_frontmatter_and_the_body():
    guide = parse_guide(TEXT, "fallback")
    assert guide == Guide(name="sample", description="例の手引き。使う場面の一行", body="# 見出し\n\n本文")
    assert parse_guide("---\ndescription: x\n---\nbody", "folder").name == "folder"
    assert parse_guide("no frontmatter", "x") is None
    assert parse_guide("---\nname: x\n---\nbody", "x") is None  # description は必須


def test_load_reads_every_folder_and_skips_broken_ones(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text(TEXT, encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "SKILL.md").write_text("broken", encoding="utf-8")
    book = load_guides(tmp_path)
    assert [g.name for g in book.guides] == ["sample"]
    assert load_guides(tmp_path / "missing").guides == ()


def test_the_branch_mining_guide_in_the_repo_is_readable_and_names_the_dangers():
    book = load_guides("guides")
    ok, body = book.read("branch_mining")
    assert ok and "溶岩" in body and "水" in body and "砂利" in body and "階段状" in body
    assert "ブランチマイニング" in next(g for g in book.guides if g.name == "branch_mining").description


def test_read_refuses_an_unknown_name_and_lists_the_known_ones():
    ok, text = GuideBook([Guide("a", "d", "b")]).read("zzz")
    assert not ok and "guides: a" in text


def test_the_prompts_list_names_and_descriptions_but_not_the_body():
    builder = GamePromptTemplateBuilder(raw_bot=True, raw_only=True)
    guide = Guide("branch_mining", "鉱石を掘る", "本文の秘密")
    raw = builder.build_raw_prompt("task", {}, [], [], [guide])
    assert "- branch_mining: 鉱石を掘る" in raw and "read_guide" in raw and "本文の秘密" not in raw
    assert "手引き" not in builder.build_raw_prompt("task", {}, [], [], [])


def test_read_guide_is_a_lookup_in_both_tool_sets():
    assert READ_GUIDE in SEARCH_TOOLS
    assert READ_GUIDE in {t.name for t in tool_specs(skills=True, write_skill=True)}


@pytest.mark.asyncio
async def test_the_search_tools_return_the_guide_body():
    search = WorldSearch(Mock(), Mock(), guides=GuideBook([Guide("a", "d", "the body")]))
    assert await search.run(READ_GUIDE, {"name": "a"}) == (True, "the body")
    ok, text = await search.run(READ_GUIDE, {"name": "nope"})
    assert not ok and "no guide" in text


@pytest.mark.asyncio
async def test_raw_play_reads_a_guide_without_the_bridge_and_then_chooses_again():
    bridge = Mock()
    bridge.raw_state = AsyncMock(return_value={"self": {}, "reflex": False})
    bridge.skills = AsyncMock(return_value=[])
    bridge.run_tool = AsyncMock()
    generator = Mock()
    generator.choose_tool = AsyncMock(
        side_effect=[ToolChoice("read_guide", {"name": "a"}), ToolChoice("run_skill", {"intent": "x", "name": "chop"})]
    )
    bridge.run_skill = AsyncMock(side_effect=RuntimeError("stop here"))
    advance = RawAdvancePlay(
        bridge, generator, GamePromptTemplateBuilder(raw_bot=True, raw_only=True), Mock(), "task",
        guides=GuideBook([Guide("a", "d", "GUIDE BODY")]), sleep=AsyncMock(),
    )
    session = await RawStartPlay(ObjectivePlan(mission=Mission(text="x"))).execute()
    with pytest.raises(RuntimeError):
        await advance.execute(session)
    bridge.run_tool.assert_not_awaited()
    assert "GUIDE BODY" in generator.choose_tool.call_args.args[0]


def test_planning_notes_take_only_the_section_for_planning_with_the_guide_name():
    from ailoveshen.application.use_cases.guides import planning_notes, reference_of

    body = "# 見出し\n\n## 計画のための要点\n- 準備を最初に\n- 高さは y=16\n\n## 本文\n長い説明"
    other = Guide("b", "d", "## 本文\n要点なし")
    notes = planning_notes([Guide("a", "d", body), other])
    assert notes == "（手引き a より）\n- 準備を最初に\n- 高さは y=16"
    assert planning_notes([other]) == ""
    assert "長い説明" in reference_of([Guide("a", "d", body)]) and reference_of([]) == ""


def test_the_branch_mining_guide_has_notes_for_planning():
    from ailoveshen.application.use_cases.guides import planning_notes

    notes = planning_notes(load_guides("guides").guides)
    assert "branch_mining" in notes and "予備" in notes and "y = 16" in notes
