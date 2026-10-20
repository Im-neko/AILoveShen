"""判断なしのプレイ（設計書 191）: 目標も提案もなく、LLM が技を書いて試して直す。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import ToolChoice
from ailoveshen.application.use_cases.raw_play import RawAdvancePlay, RawStartPlay
from ailoveshen.application.use_cases.skills import NotWritten, SkillWriter, WrittenSkill, skill_schema
from ailoveshen.application.use_cases.tool_catalog import RAW_QUERY_TOOLS, raw_tool_specs
from ailoveshen.domain.entities import Mission, ObjectivePlan
from ailoveshen.domain.value_objects import SkillDraft, SkillInfo, SkillRun
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)

STATE = {
    "self": {"position": {"x": 1, "y": 64, "z": 2}, "health": 20, "food": 20},
    "surroundings": {"legend": "l", "grid": ["g"]},
    "mobs": [],
    "inventory": {"oak_log": 3},
    "tool_wear": [],
    "time": "day",
    "reflex": False,
    "skill": None,
}
DRAFT = SkillDraft(description="chop", params={}, expects={"predicate": "have", "item": "oak_log", "count": "+1"}, code="x", raw=True)


def make(choices, runs=(), advisor=None):
    bridge = Mock()
    bridge.raw_state = AsyncMock(return_value=STATE)
    bridge.skills = AsyncMock(return_value=[SkillInfo(name="chop_tree", description="chop", version=2, params={}, expects={}, raw=True, code="")])
    bridge.run_tool = AsyncMock(return_value=(True, "recipe ...", 0.0, False))
    bridge.run_skill = AsyncMock(side_effect=list(runs))
    bridge.try_skill = AsyncMock(side_effect=list(runs))
    bridge.release_reflex = AsyncMock()
    generator = Mock()
    generator.choose_tool = AsyncMock(side_effect=[ToolChoice(n, a) for n, a in choices])
    prompts = GamePromptTemplateBuilder(raw_bot=True, raw_only=True)
    writer = Mock()
    writer.write = AsyncMock(return_value=WrittenSkill(name="chop_tree", version=3, description="chop", trial_args={"n": 1}, draft=DRAFT))
    writer.mark_proven = Mock()
    advance = RawAdvancePlay(bridge, generator, prompts, writer, "Survive.", advisor=advisor, sleep=AsyncMock())
    return advance, bridge, generator, writer


def session():
    return RawStartPlay(ObjectivePlan(mission=Mission(text="x"))).execute()


def ok_run(name="chop_tree", version=2):
    return SkillRun(name=name, version=version, ok=True, ended="done", summary="chopped", seconds=3.0)


@pytest.mark.asyncio
async def test_the_prompt_has_the_task_and_the_measured_state_but_no_goals_suggestions_or_advice():
    advance, bridge, generator, _ = make([("run_skill", {"intent": "木を切る", "name": "chop_tree", "args": "{}"})], [ok_run()])
    await advance.execute(await session())
    prompt = generator.choose_tool.call_args.args[0]
    assert "Survive." in prompt and '"oak_log": 3' in prompt and "chop_tree" in prompt
    for absent in ("ソルバー", "小目標", "needs", "do_suggestion", "reflex", "closed_space"):
        assert absent not in prompt
    offered = {t.name for t in generator.choose_tool.call_args.args[1]}
    assert offered == {"run_skill", "try_skill", "write_skill", "read_guide", "write_note", *RAW_QUERY_TOOLS}
    bridge.run_skill.assert_awaited_once_with("chop_tree", {}, None)


@pytest.mark.asyncio
async def test_a_lookup_is_answered_in_the_same_step_then_the_skill_runs():
    advance, bridge, generator, _ = make(
        [("recipe_of", {"item": "stick"}), ("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"})], [ok_run()]
    )
    report = await advance.execute(await session())
    bridge.run_tool.assert_awaited_once_with("recipe_of", {"item": "stick"})
    assert generator.choose_tool.await_count == 2
    assert report.result.ok and report.goal is None and report.status is None
    # 調べものの結果は、次の選択に見える
    assert "recipe_of" in generator.choose_tool.call_args.args[0]


@pytest.mark.asyncio
async def test_try_skill_writes_unsaved_runs_it_and_remembers_it_worked():
    advance, bridge, _, writer = make([("try_skill", {"intent": "試す", "name": "chop_tree", "what": "cut a log"})], [ok_run(version=0)])
    report = await advance.execute(await session())
    assert writer.write.call_args.kwargs["unsaved"] is True
    bridge.try_skill.assert_awaited_once_with(DRAFT, {"n": 1})
    writer.mark_proven.assert_called_once_with("chop_tree", {"n": 1})
    assert "not saved" not in report.result.result or "Save it with write_skill" in report.result.result
    assert report.result.ok


@pytest.mark.asyncio
async def test_a_failed_trial_is_kept_for_the_next_fix_and_an_unwritable_skill_is_a_refusal():
    failed = SkillRun(name="chop_tree", version=0, ok=False, ended="error", reason="boom", seconds=1.0)
    advance, _, generator, writer = make(
        [("try_skill", {"intent": "試す", "name": "chop_tree", "what": "w"}), ("try_skill", {"intent": "試す", "name": "chop_tree", "what": "w"})],
        [failed],
    )
    first = await advance.execute(await session())
    assert not first.result.ok and "fix it with try_skill again with the same name chop_tree" in first.result.result
    writer.write.return_value = NotWritten("tried 120 times")
    second = await advance.execute(await session())
    assert not second.result.ok and "tried 120 times" in second.result.result
    # 直すときに、前の失敗が書き手に渡る
    assert writer.write.call_args_list[1].args[5].startswith("trial of chop_tree (not saved) failed")


@pytest.mark.asyncio
async def test_it_waits_for_the_reflex_and_lets_go_after_the_limit():
    advance, bridge, _, _ = make([("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"})], [ok_run()])
    bridge.raw_state = AsyncMock(side_effect=[{**STATE, "reflex": True}, {**STATE, "reflex": True}, STATE, STATE, STATE])
    await advance.execute(await session())
    assert bridge.release_reflex.await_count == 0
    stuck, bridge2, _, _ = make([("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"})], [ok_run()])
    bridge2.raw_state = AsyncMock(return_value={**STATE, "reflex": True})
    await stuck.execute(await session())
    bridge2.release_reflex.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_bad_call_is_refused_not_raised():
    advance, _, _, _ = make([("goto", {"intent": "歩く", "x": 1, "y": 2, "z": 3})])
    report = await advance.execute(await session())
    assert not report.result.ok and "invalid call" in report.result.result


def test_the_raw_schema_asks_only_for_raw_skills_with_five_success_conditions():
    schema = skill_schema(raw_bot=True, raw_only=True)
    assert schema["properties"]["raw"]["enum"] == [True] and "raw" in schema["required"]
    assert schema["properties"]["expects"]["properties"]["predicate"]["enum"] == ["have", "at", "height", "out_of_closed_space", "block_near"]
    assert "raw" not in skill_schema(raw_bot=False)["properties"]


def test_the_skill_writer_prompts_describe_t_bot_and_no_bridge_tools():
    prompts = GamePromptTemplateBuilder(raw_bot=True, raw_only=True)
    system = prompts.build_skill_system()
    assert "t.bot" in system and "t.goto / t.dig / t.place などの道具" in system and "do_suggestion" not in system
    text = prompts.build_skill_prompt("chop", "cut", None, [], [])
    assert "配信者が今していること" not in text


def test_raw_tools_have_no_watch_questions_and_no_bridge_actions():
    specs = {t.name: t for t in raw_tool_specs()}
    assert set(specs) == {"run_skill", "try_skill", "write_skill", "read_guide", "write_note", *RAW_QUERY_TOOLS}
    assert all("watch" not in t.parameters["properties"] for t in specs.values())
    assert "intent" in specs["try_skill"].parameters["required"]


@pytest.mark.asyncio
async def test_a_skill_stopped_by_the_time_limit_gets_a_fix_it_hint_not_silence():
    stopped = SkillRun(name="chop_tree", version=1, ok=False, ended="stopped", reason="ran longer than 10 minutes", seconds=600.0)
    advance, _, _, _ = make([("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"})], [stopped])
    report = await advance.execute(await session())
    assert not report.result.ok
    assert "every wait needs a limit" in report.result.result


def test_a_failed_skill_shows_the_scene_when_it_ended():
    run = SkillRun(
        name="place", version=0, ok=False, ended="fail", reason="could not place",
        scene={"feet": "air", "held": "crafting_table", "looking_at": None},
    )
    text = run.describe()
    assert 'scene when it ended: {"feet":"air","held":"crafting_table","looking_at":null}' in text
    assert "scene" not in SkillRun(name="x", version=1, ok=True, ended="done", summary="s").describe()


@pytest.mark.asyncio
async def test_a_note_is_written_in_the_same_step_and_shows_in_every_later_prompt():
    advance, bridge, generator, _ = make(
        [
            ("write_note", {"text": "夜は野原にいると死ぬ。日没前に穴を掘って塞ぐ"}),
            ("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"}),
        ],
        [ok_run()],
    )
    await advance.execute(await session())
    bridge.run_tool.assert_not_awaited()  # メモはブリッジに行かない
    assert generator.choose_tool.await_count == 2
    assert "1. 夜は野原にいると死ぬ。日没前に穴を掘って塞ぐ" in generator.choose_tool.call_args.args[0]
    assert "write_note" in generator.choose_tool.call_args.args[0]  # 直近の結果にも出る


@pytest.mark.asyncio
async def test_notes_survive_into_the_next_step_and_are_withheld_on_the_last_query_round():
    queries = [("write_note", {"text": f"メモ{i}"}) for i in range(5)]
    advance, _, generator, _ = make(queries + [("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"})] * 2, [ok_run(), ok_run()])
    await advance.execute(await session())
    last_offered = {t.name for t in generator.choose_tool.call_args.args[1]}
    assert "write_note" not in last_offered and "run_skill" in last_offered
    await advance.execute(await session())
    assert "メモ0" in generator.choose_tool.call_args.args[0]


@pytest.mark.asyncio
async def test_the_advisor_is_offered_only_when_there_is_one_and_answers_in_the_same_step():
    advance, _, generator, _ = make([("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"})], [ok_run()])
    await advance.execute(await session())
    assert "ask_advisor" not in {t.name for t in generator.choose_tool.call_args.args[1]}

    advisor = Mock()
    advisor.ask = AsyncMock(return_value=(True, "夜は穴を掘って塞いで待つ"))
    advance, bridge, generator, _ = make(
        [
            ("ask_advisor", {"question": "夜に 3 回死んだ。次は？"}),
            ("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"}),
        ],
        [ok_run()],
        advisor=advisor,
    )
    await advance.execute(await session())
    assert "ask_advisor" in {t.name for t in generator.choose_tool.call_args_list[0].args[1]}
    advisor.ask.assert_awaited_once()
    assert advisor.ask.call_args.args[0] == "夜に 3 回死んだ。次は？"
    bridge.run_tool.assert_not_awaited()  # ブリッジには行かない
    assert "夜は穴を掘って塞いで待つ" in generator.choose_tool.call_args.args[0]


@pytest.mark.asyncio
async def test_a_failed_skill_at_night_is_not_told_to_run_again_as_it_is():
    night = dict(STATE, time="night")
    advance, bridge, _, _ = make(
        [("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"})],
        [SkillRun(name="chop_tree", version=2, ok=False, ended="stopped", reason="reflex: zombie 5m", seconds=1.0)],
    )
    bridge.raw_state = AsyncMock(return_value=night)
    await advance.execute(await session())
    text = advance._recent[-1].result
    assert "it is night" in text and "run it again as it is" not in text


def advised():
    """(advance, advisor, generator, now): `now[0]` が、次のステップで測れる状態。"""
    advisor = Mock()
    advisor.ask = AsyncMock(return_value=(True, "穴を掘って塞ぎ、朝まで待つ"))
    advance, bridge, generator, _ = make(
        [("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"})] * 5,
        [ok_run()] * 5,
        advisor=advisor,
    )
    now = [STATE]
    bridge.raw_state = AsyncMock(side_effect=lambda: now[0])
    return advance, advisor, generator, now


@pytest.mark.asyncio
async def test_a_death_makes_the_code_ask_the_advisor_once_and_the_answer_shows_in_the_prompt():
    advance, advisor, generator, now = advised()
    await advance.execute(await session())
    advisor.ask.assert_not_awaited()
    now[0] = dict(STATE, deaths={"count": 1, "last": {"time": "night", "damage_by": {"zombie": 20}}})
    await advance.execute(await session())
    advisor.ask.assert_awaited_once()
    assert "deaths" in advisor.ask.call_args.args[0]
    assert "穴を掘って塞ぎ、朝まで待つ" in generator.choose_tool.call_args.args[0]
    await advance.execute(await session())
    advisor.ask.assert_awaited_once()  # 同じ死では、もう聞かない


@pytest.mark.asyncio
async def test_nightfall_makes_the_code_ask_but_starting_in_the_dark_does_not():
    advance, advisor, _, now = advised()
    now[0] = dict(STATE, time="night")  # 最初から夜: 変わったのではない
    await advance.execute(await session())
    await advance.execute(await session())
    advisor.ask.assert_not_awaited()

    advance, advisor, _, now = advised()
    await advance.execute(await session())
    now[0] = dict(STATE, time="dusk")
    await advance.execute(await session())
    advisor.ask.assert_awaited_once()
    assert "夜" in advisor.ask.call_args.args[0]
    now[0] = dict(STATE, time="night")
    await advance.execute(await session())
    advisor.ask.assert_awaited_once()  # 夜の間は 1 回だけ


@pytest.mark.asyncio
async def test_without_an_advisor_nothing_is_asked_on_a_death():
    advance, bridge, generator, _ = make(
        [("run_skill", {"intent": "切る", "name": "chop_tree", "args": "{}"})], [ok_run()]
    )
    bridge.raw_state = AsyncMock(return_value=dict(STATE, deaths={"count": 2, "last": None}))
    await advance.execute(await session())
    assert generator.choose_tool.await_count == 1


@pytest.mark.asyncio
async def test_calling_a_saved_skill_name_as_a_tool_is_told_to_use_run_skill():
    advance, _, _, _ = make([("chop_tree", {"intent": "切る"})])
    report = await advance.execute(await session())
    assert "unknown tool 'chop_tree'" in advance._recent[-1].result
    assert "run_skill with name=chop_tree" in advance._recent[-1].result


@pytest.mark.asyncio
async def test_an_unknown_name_that_is_not_a_skill_gets_no_hint():
    advance, _, _, _ = make([("fly_away", {"intent": "飛ぶ"})])
    report = await advance.execute(await session())
    assert "unknown tool 'fly_away'" in advance._recent[-1].result and "run_skill" not in advance._recent[-1].result
