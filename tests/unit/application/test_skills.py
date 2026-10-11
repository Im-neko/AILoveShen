"""技のテスト（docs/design/22_skills.md）: 道具、書く・直す、見張りと judge、道具のステップ、見せ方。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import ToolChoice
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase
from ailoveshen.application.use_cases.skills import (
    NotWritten,
    SkillWriter,
    WrittenSkill,
    order_skills,
    skill_schema,
)
from ailoveshen.application.use_cases.tool_catalog import (
    ToolCallError,
    parse_tool_call,
    tool_specs,
)
from ailoveshen.application.use_cases.watcher import JUDGE, ToolWatcher
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import SkillLearnedEvent, SkillRevisedEvent
from ailoveshen.domain.exceptions import SkillRejectedError
from ailoveshen.domain.value_objects import (
    Activity,
    GoalStatus,
    SkillInfo,
    SkillRun,
    ToolCall,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)
from tests.unit.application.test_play_use_cases import HAVE_PLANKS, _judged, _obs, _session
from tests.unit.application.test_tool_control import FAST, _judge

HUNT = SkillInfo(
    name="hunt",
    description="近くの動物を狩る",
    version=2,
    params={"type": "object", "properties": {"animal": {"type": "string"}}},
    expects={"predicate": "have", "item": "food", "count": "+1"},
    verified=True,
    uses=3,
    successes=2,
    failures=1,
    last_failure="no animal in sight",
    code="export default async function (t) { return t.done() }",
)
FIX_DIAGNOSIS = (
    "till was refused with 'will not till more: 1 seed for 1 tilled cell', so sow must come first"
)
DRAFT = {
    "description": "近くの動物を狩る",
    "params_json": '{"type": "object", "properties": {"animal": {"type": "string"}}}',
    "expects": {"predicate": "have", "item": "food", "count": "+1", "name": ""},
    "code": "export default async function (t, a) { return t.done() }",
    "trial_args_json": '{"animal": "pig"}',
}


class TestSkillTools:
    def test_skill_tools_are_offered_only_with_skills(self):
        assert {"run_skill", "write_skill"}.isdisjoint({t.name for t in tool_specs()})
        specs = {t.name: t for t in tool_specs(skills=True, write_skill=True)}
        assert specs["run_skill"].parameters["required"] == ["intent", "name"]
        assert specs["write_skill"].parameters["required"] == ["intent", "name", "what"]
        # 技を書く道具は、行き詰まり・同じ手の繰り返しのときだけ。技があっても、ふだんは出さない
        assert "write_skill" not in {t.name for t in tool_specs(skills=True)}

    def test_run_skill_args_are_parsed_from_json(self):
        call = parse_tool_call(
            ToolChoice(
                "run_skill",
                {"name": "hunt", "args": '{"animal": "pig"}', "version": 1, "intent": "狩る"},
            )
        )
        assert call.args == {"name": "hunt", "args": {"animal": "pig"}, "version": 1}
        with pytest.raises(ToolCallError, match="JSON object"):
            parse_tool_call(ToolChoice("run_skill", {"name": "hunt", "args": "[1]", "intent": "i"}))
        with pytest.raises(ToolCallError, match="snake_case"):
            parse_tool_call(ToolChoice("run_skill", {"name": "Hunt!", "intent": "i"}))
        with pytest.raises(ToolCallError, match="needs what"):
            parse_tool_call(ToolChoice("write_skill", {"name": "hunt", "intent": "i"}))

    def test_skills_that_fit_the_goal_come_first(self):
        other = SkillInfo(name="torches", description="松明", version=1, successes=9, verified=True)
        trial = SkillInfo(
            name="hunt2", description="狩り", version=1, expects={"item": "food"}, verified=False
        )
        assert [s.name for s in order_skills([other, trial, HUNT], "food")] == [
            "hunt",
            "hunt2",
            "torches",
        ]


class TestSkillWriter:
    @pytest.fixture
    def parts(self):
        generator = AsyncMock()
        generator.generate_json.return_value = DRAFT
        prompts = Mock()
        prompts.build_skill_prompt.return_value = "skill prompt"
        prompts.build_skill_system.return_value = "skill system"
        bridge = AsyncMock()
        bridge.skill.return_value = None
        bridge.save_skill.return_value = 1
        return generator, prompts, bridge

    @pytest.mark.asyncio
    async def test_writes_saves_and_returns_the_trial_args(self, parts):
        generator, prompts, bridge = parts
        written = await SkillWriter(generator, prompts, bridge).write(
            "hunt", "近くの動物を狩る", Activity(), (), [HUNT]
        )
        assert written == WrittenSkill("hunt", 1, "近くの動物を狩る", {"animal": "pig"})
        name, draft = bridge.save_skill.await_args.args
        assert name == "hunt"
        assert draft.expects == {"predicate": "have", "item": "food", "count": "+1"}  # 空は落とす
        assert draft.params["properties"] == {"animal": {"type": "string"}}
        kwargs = generator.generate_json.await_args.kwargs
        assert kwargs["purpose"] == "skill_write"
        assert kwargs["system_instruction"] == "skill system"

    @pytest.mark.asyncio
    async def test_a_rejected_skill_is_rewritten_with_the_reason(self, parts):
        generator, prompts, bridge = parts
        bridge.save_skill.side_effect = [SkillRejectedError("the code does not compile"), 2]
        written = await SkillWriter(generator, prompts, bridge).write(
            "hunt", "狩る", Activity(), (), []
        )
        assert written.version == 2
        errors = [c.kwargs["previous_error"] for c in prompts.build_skill_prompt.call_args_list]
        assert errors == ["", "the code does not compile"]

    @pytest.mark.asyncio
    async def test_fixing_shows_the_previous_code_and_the_failure(self, parts):
        generator, prompts, bridge = parts
        bridge.skill.return_value = HUNT
        generator.generate_json.return_value = {**DRAFT, "diagnosis": FIX_DIAGNOSIS}
        written = await SkillWriter(generator, prompts, bridge).write(
            "hunt", "豚がいないときは探す", Activity(), (), [HUNT], "skill hunt v2 failed: no pig"
        )
        kwargs = prompts.build_skill_prompt.call_args.kwargs
        assert kwargs["previous"] == HUNT
        assert kwargs["skills"] == []  # 直す技は「ほかの技」に入れない
        assert written.revised_because == FIX_DIAGNOSIS  # 見つけた原因を、直した理由にする

    @pytest.mark.asyncio
    async def test_a_fix_without_a_cause_or_with_the_same_code_is_sent_back(self, parts):
        generator, prompts, bridge = parts
        bridge.skill.return_value = HUNT
        generator.generate_json.side_effect = [
            DRAFT,  # 原因がない
            {**DRAFT, "diagnosis": FIX_DIAGNOSIS, "code": HUNT.code},  # 前と同じコード
            {**DRAFT, "diagnosis": FIX_DIAGNOSIS},
        ]
        written = await SkillWriter(generator, prompts, bridge).write(
            "hunt", "豚を探す", Activity(), (), [HUNT], "skill hunt v2 failed: no pig"
        )
        assert written.version == 1
        errors = [c.kwargs["previous_error"] for c in prompts.build_skill_prompt.call_args_list]
        assert errors[0] == "" and "write the cause in diagnosis" in errors[1]
        assert "the code is the same as v2" in errors[2]
        assert bridge.save_skill.await_count == 1  # 差し戻したものは保存しない

    @pytest.mark.asyncio
    async def test_a_new_skill_needs_no_cause(self, parts):
        generator, prompts, bridge = parts  # bridge.skill は None（同じ名前がない）
        written = await SkillWriter(generator, prompts, bridge).write(
            "hunt", "狩る", Activity(), (), []
        )
        assert isinstance(written, WrittenSkill) and written.revised_because == ""

    @pytest.mark.asyncio
    async def test_at_most_n_writes_of_one_skill_per_hour(self, parts):
        generator, prompts, bridge = parts
        now = [0.0]
        writer = SkillWriter(generator, prompts, bridge, rewrites_per_hour=2, clock=lambda: now[0])
        for _ in range(2):
            assert isinstance(await writer.write("hunt", "x", Activity(), (), []), WrittenSkill)
        refused = await writer.write("hunt", "x", Activity(), (), [])
        assert isinstance(refused, NotWritten) and "at most 2" in refused.why
        assert isinstance(await writer.write("other", "x", Activity(), (), []), WrittenSkill)
        now[0] = 3601.0
        assert isinstance(await writer.write("hunt", "x", Activity(), (), []), WrittenSkill)


class TestRawBotSkills:
    """生の mineflayer の bot を触る技（docs/design/168）。"""

    RAW_DRAFT = {**DRAFT, "raw": True}

    def test_the_schema_offers_raw_only_when_it_is_on(self):
        assert "raw" not in skill_schema()["properties"]
        assert skill_schema(raw_bot=True)["properties"]["raw"]["type"] == "boolean"
        assert "raw" not in skill_schema(raw_bot=True)["required"]

    @pytest.mark.asyncio
    async def test_a_raw_skill_is_saved_with_the_flag(self):
        generator = AsyncMock()
        generator.generate_json.return_value = self.RAW_DRAFT
        prompts = Mock()
        bridge = AsyncMock()
        bridge.skill.return_value = None
        bridge.save_skill.return_value = 1
        await SkillWriter(generator, prompts, bridge, raw_bot=True).write(
            "jump_tower", "跳んで積む", Activity(), (), []
        )
        _, draft = bridge.save_skill.await_args.args
        assert draft.raw is True and draft.to_dict()["raw"] is True
        assert generator.generate_json.await_args.args[1]["properties"]["raw"]

    @pytest.mark.asyncio
    async def test_a_skill_without_the_flag_is_not_raw(self):
        generator = AsyncMock()
        generator.generate_json.return_value = DRAFT
        bridge = AsyncMock()
        bridge.skill.return_value = None
        bridge.save_skill.return_value = 1
        await SkillWriter(generator, Mock(), bridge, raw_bot=True).write("hunt", "狩る", Activity(), (), [])
        _, draft = bridge.save_skill.await_args.args
        assert draft.raw is False and "raw" not in draft.to_dict()

    def test_the_writer_is_taught_the_real_bot_only_when_it_is_on(self):
        off = GamePromptTemplateBuilder().build_skill_system()
        on = GamePromptTemplateBuilder(raw_bot=True).build_skill_system()
        assert "t.bot" not in off and "生の bot" not in off
        assert "t.bot" in on and "t.Vec3" in on and "t.goals" in on and "t.sleep" in on
        assert on.startswith(off)

    def test_a_run_that_fell_back_says_which_version_failed_its_trial(self):
        run = SkillRun(name="hunt", version=1, ok=True, ended="done", summary="ok", fell_back_from=2)
        assert run.describe() == (
            "skill hunt v1 (v2 failed its trial, so the version that worked was run) succeeded: ok"
        )
        assert SkillRun(name="hunt", version=1, ok=True, ended="done", summary="ok").describe() == (
            "skill hunt v1 succeeded: ok"
        )

    def test_the_tool_prompt_suggests_a_raw_skill_only_when_it_is_on(self):
        state = {"goal": "have 8 food", "self": {}}
        off = GamePromptTemplateBuilder().build_tool_prompt(Activity(), state, (), (), skills=[HUNT])
        on = GamePromptTemplateBuilder(raw_bot=True).build_tool_prompt(
            Activity(), state, (), (), skills=[HUNT]
        )
        assert "本物の bot" not in off and "本物の bot を触る技" in on
        assert "本物の bot" not in GamePromptTemplateBuilder(raw_bot=True).build_tool_prompt(
            Activity(), state, (), ()
        )

    def test_the_stuck_section_points_to_a_skill_only_when_raw_is_on(self):
        state = {"goal": "have 8 food", "self": {}}
        off = GamePromptTemplateBuilder().build_tool_prompt(Activity(), state, (), (), failing=3)
        on = GamePromptTemplateBuilder(raw_bot=True).build_tool_prompt(
            Activity(), state, (), (), failing=3
        )
        assert "## 行き詰まっている" in off and "手そのものが足りない" not in off
        assert "手そのものが足りない" in on and "write_skill" in on.split("## 行き詰まっている")[1]
        calm = GamePromptTemplateBuilder(raw_bot=True).build_tool_prompt(Activity(), state, (), ())
        assert "手そのものが足りない" not in calm

    def test_the_advice_prompt_allows_recommending_a_skill_only_when_raw_is_on(self):
        off = GamePromptTemplateBuilder().build_advice_prompt("q", "S")
        on = GamePromptTemplateBuilder(raw_bot=True).build_advice_prompt("q", "S")
        assert "write_skill" not in off and "write_skill" in on and "the hand is missing" in on

    def test_the_schema_offers_the_position_expectations(self):
        predicates = skill_schema()["properties"]["expects"]["properties"]["predicate"]["enum"]
        assert "at" in predicates and "out_of_closed_space" in predicates

    def test_a_skill_that_runs_an_earlier_version_says_so(self):
        info = SkillInfo(name="hunt", description="狩る", version=3, runs_version=2, verified=True)
        assert "（呼ぶと v2 が動く）" in info.describe()
        assert "呼ぶと" not in SkillInfo(name="h", description="d", version=3, runs_version=3).describe()
        assert "呼ぶと" not in HUNT.describe()

    def test_a_raw_skill_says_so_in_the_list(self):
        raw = SkillInfo(name="jump_tower", description="跳んで積む", version=1, raw=True)
        assert "（生の bot）" in raw.describe()
        assert "（生の bot）" not in HUNT.describe()


class JudgingBridge:
    """技を実行している間、judge() の質問を 1 つ出すブリッジ。"""

    def __init__(self):
        self.answers = []
        self.done = asyncio.Event()

    async def state(self):
        if self.answers:
            return {"action": None, "pending_judge": None}
        return {
            "action": None,
            "pending_judge": {"id": 4, "question": "Is it dark?", "kind": "yes_no"},
        }

    async def answer_judge(self, judge_id, answer, confidence):
        self.answers.append((judge_id, answer, confidence))
        self.done.set()

    async def abort(self, reason):
        return True


class TestWatchingSkills:
    @pytest.mark.asyncio
    async def test_the_skill_runs_instead_of_a_tool_and_judge_questions_are_answered(self):
        bridge = JudgingBridge()
        judge = _judge({JUDGE: (True, 0.8)})

        async def execute():
            await bridge.done.wait()
            return True, "skill hunt v1 succeeded: hunted", 3.0, False

        call = ToolCall("run_skill", {"name": "hunt", "args": {}}, intent="狩る")
        outcome = await ToolWatcher(bridge, judge, None, FAST).run(call, execute=execute)

        assert outcome.ok and outcome.result == "skill hunt v1 succeeded: hunted"
        assert bridge.answers == [(4, True, 0.8)]
        assert JUDGE in judge.asked[0]


class TestSkillStep:
    """control: tools で技を呼ぶ 1 ステップ。"""

    @pytest.fixture
    def bridge(self):
        b = AsyncMock()
        b.observe.return_value = _obs()
        b.set_goal.return_value = GoalStatus(met=False, remaining=3)
        b.check.side_effect = _judged()
        b.state.return_value = {"action": None, "surroundings": {}, "mobs": []}
        b.skills.return_value = [HUNT]
        b.run_skill.return_value = SkillRun(
            name="hunt", version=2, ok=True, ended="done", summary="hunted a pig", learned=True
        )
        return b

    @pytest.fixture
    def watcher(self):
        w = AsyncMock()

        async def run(call, execute=None):
            from ailoveshen.domain.value_objects import ToolOutcome

            ok, result, seconds, refused = await execute()
            return ToolOutcome(call, ok, result, seconds, refused)

        w.run.side_effect = run
        return w

    def _use_case(self, bridge, watcher, writer):
        self.text_generator = AsyncMock()
        self.prompt_builder = Mock()
        self.prompt_builder.build_tool_prompt.return_value = "tool prompt"
        self.events = AsyncMock()
        store = Mock()
        store.load.return_value = None
        note_store = Mock()
        note_store.load.return_value = None
        return AdvancePlayUseCase(
            bridge=bridge,
            text_generator=self.text_generator,
            prompt_builder=self.prompt_builder,
            action_selector=AsyncMock(),
            event_publisher=self.events,
            conversation=Conversation(),
            objectives=ObjectiveKeeper(bridge=bridge, event_publisher=self.events, store=store),
            town=AsyncMock(),
            notes=NoteKeeper(note_store),
            control="tools",
            tool_watcher=watcher,
            skills=writer,
        )

    def _published(self, kind):
        return [
            c.args[0] for c in self.events.publish.call_args_list if isinstance(c.args[0], kind)
        ]

    @pytest.mark.asyncio
    async def test_run_skill_is_watched_and_learning_is_announced(self, bridge, watcher):
        use_case = self._use_case(bridge, watcher, AsyncMock())
        self.text_generator.choose_tool.return_value = ToolChoice(
            "run_skill", {"name": "hunt", "args": '{"animal": "pig"}', "intent": "豚を狩る"}
        )

        report = await use_case.execute(_session(HAVE_PLANKS))

        bridge.run_skill.assert_awaited_once_with("hunt", {"animal": "pig"}, None)
        assert report.result.ok
        assert report.result.result == "skill hunt v2 succeeded: hunted a pig"
        assert self._published(SkillLearnedEvent)[0].description == "近くの動物を狩る"
        assert self.prompt_builder.build_tool_prompt.call_args.kwargs["skills"] == [HUNT]
        offered = {t.name for t in self.text_generator.choose_tool.call_args.args[1]}
        assert "run_skill" in offered and "write_skill" not in offered

    @pytest.mark.asyncio
    async def test_a_failure_is_kept_for_the_fix_and_write_skill_tries_the_new_version(
        self, bridge, watcher
    ):
        writer = AsyncMock()
        writer.write.return_value = WrittenSkill(
            "hunt", 3, "近くの動物を狩る", {}, revised_because="skill hunt v2 failed (fail): no pig"
        )
        use_case = self._use_case(bridge, watcher, writer)
        failed = SkillRun(name="hunt", version=2, ok=False, ended="fail", reason="no pig")
        bridge.run_skill.side_effect = [
            failed,
            SkillRun(name="hunt", version=3, ok=True, ended="done", summary="ok"),
        ]
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("run_skill", {"name": "hunt", "intent": "狩る"}),
            ToolChoice("write_skill", {"name": "hunt", "what": "豚を探す", "intent": "直す"}),
        ]
        session = _session(HAVE_PLANKS)

        first = await use_case.execute(session)
        second = await use_case.execute(session)

        assert not first.result.ok and "no pig" in first.result.result
        assert writer.write.await_args.args[5] == failed.describe()  # 前の失敗を見せて直す
        assert bridge.run_skill.await_args.args == ("hunt", {}, 3)  # 直した版をすぐ試す
        assert second.result.result.startswith("wrote hunt v3; trial: skill hunt v3 succeeded")
        assert self._published(SkillRevisedEvent)[0].version == 3

    @pytest.mark.asyncio
    async def test_a_failed_run_says_to_find_the_cause_and_fix_it(self, bridge, watcher):
        use_case = self._use_case(bridge, watcher, AsyncMock())
        self.text_generator.choose_tool.return_value = ToolChoice(
            "run_skill", {"name": "hunt", "intent": "狩る"}
        )
        ends = (("fail", True), ("error", True), ("stopped", False), ("refused", False))
        for ended, expected in ends:
            bridge.run_skill.return_value = SkillRun(
                name="hunt", version=2, ok=False, ended=ended, reason="no pig"
            )
            report = await use_case.execute(_session(HAVE_PLANKS))
            note = "Find the cause first" in report.result.result
            assert note is expected, ended
            if expected:
                assert "write_skill (name hunt" in report.result.result
        bridge.run_skill.return_value = SkillRun(
            name="hunt", version=2, ok=True, ended="done", summary="hunted a pig"
        )
        report = await use_case.execute(_session(HAVE_PLANKS))
        assert "Find the cause first" not in report.result.result

    @pytest.mark.asyncio
    async def test_a_refused_run_does_not_replace_the_failure_shown_for_the_fix(
        self, bridge, watcher
    ):
        # 同じ理由の失敗が続いてブリッジが動かさなかった断りは、直すときに見せる「本当の失敗」ではない
        use_case = self._use_case(bridge, watcher, AsyncMock())
        failed = SkillRun(name="hunt", version=2, ok=False, ended="fail", reason="no pig")
        refused = SkillRun(
            name="hunt",
            version=2,
            ok=False,
            ended="refused",
            reason="hunt v2 failed 5 times in a row for the same reason (no pig)",
        )
        bridge.run_skill.side_effect = [failed, refused]
        self.text_generator.choose_tool.return_value = ToolChoice(
            "run_skill", {"name": "hunt", "intent": "狩る"}
        )
        session = _session(HAVE_PLANKS)
        await use_case.execute(session)
        await use_case.execute(session)
        assert use_case._skill_failures["hunt"] == failed.describe()

    @pytest.mark.asyncio
    async def test_a_skill_that_cannot_be_written_is_a_refused_step(self, bridge, watcher):
        writer = AsyncMock()
        writer.write.return_value = NotWritten("hunt was written 3 times in the last hour")
        use_case = self._use_case(bridge, watcher, writer)
        self.text_generator.choose_tool.return_value = ToolChoice(
            "write_skill", {"name": "hunt", "what": "x", "intent": "書く"}
        )
        report = await use_case.execute(_session(HAVE_PLANKS))
        assert not report.result.ok and "3 times" in report.result.result
        bridge.run_skill.assert_not_awaited()


class TestSkillPrompts:
    def test_the_tool_prompt_lists_skills_only_when_used(self):
        builder = GamePromptTemplateBuilder()
        state = {"surroundings": {}, "mobs": []}
        without = builder.build_tool_prompt(Activity(), state, (), ())
        assert "覚えた技" not in without
        listed = builder.build_tool_prompt(Activity(), state, (), (), skills=[HUNT])
        assert (
            "- hunt v2: 近くの動物を狩る（引数 ['animal']、成功 2/3、最後の失敗: no animal"
            in listed
        )
        assert "- まだない" in builder.build_tool_prompt(Activity(), state, (), (), skills=[])

    def test_the_skill_prompt_shows_the_code_to_fix_and_the_api_is_in_the_system(self):
        builder = GamePromptTemplateBuilder()
        prompt = builder.build_skill_prompt(
            "hunt", "豚を探す", Activity(), (), [], previous=HUNT, last_failure="no pig"
        )
        assert "## 直す前の版（v2、成功 2/3）" in prompt
        assert HUNT.code in prompt and "前の失敗: no pig" in prompt
        system = builder.build_skill_system()
        assert "t.find_blocks" in system and "座標を決め打ちしない" in system
        assert "$" not in system.replace('"$引数名"', "")


def test_a_failed_run_describes_why_with_the_record():
    run = SkillRun(
        name="hunt",
        version=2,
        ok=False,
        ended="fail",
        reason="no pig",
        expects_lines=("food 2/3",),
        log=("looked around",),
        calls=({"tool": "goto", "args": {"x": 1}, "ok": False, "result": "no path"},),
    )
    assert run.describe() == (
        "skill hunt v2 failed (fail): no pig; expects: food 2/3; log: looked around; "
        'last calls: goto({"x": 1})=failed no path'
    )


def test_a_broken_fix_points_to_the_version_that_worked():
    fixed = SkillInfo(name="hunt", description="狩り", version=3, last_good_version=2)
    assert fixed.describe().startswith("hunt v3（試し中。v2 は成功した）: 狩り")


def test_a_skill_failing_in_a_row_is_not_verified_and_says_so():
    failing = SkillInfo(
        name="farm", description="畑", version=1, verified=False, successes=3, uses=143,
        failures=140, streak=140, failing=True, last_failure="remaining work 41 -> 41",
    )
    line = failing.describe()
    assert "同じ理由で 140 回続けて失敗中" in line and "試し中" not in line
    assert "成功 3/143" in line
    # 上限の手前は、続いた回数を添える
    warned = SkillInfo(name="farm", description="畑", version=1, verified=True, successes=3,
                       uses=10, failures=2, streak=3, last_failure="x")
    assert "（同じ理由で 3 回続けて失敗）" in warned.describe()
    assert "続けて失敗中" not in warned.describe()
    # 1 回だけなら添えない
    assert "続けて" not in SkillInfo(name="farm", description="畑", version=1, verified=True,
                                      streak=1, last_failure="x").describe()
