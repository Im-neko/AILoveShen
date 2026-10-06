"""StepPicker（道具モードの 1 手をまず Jev が選ぶ、docs/design/34 §3）のテスト。"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import ToolChoice
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase
from ailoveshen.application.use_cases.step_picker import (
    ASK_GEMINI,
    PickedStep,
    StepPicker,
    StepToGemini,
)
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    Candidate,
    Goal,
    GoalStatus,
    SkillInfo,
    ToolCall,
    ToolOutcome,
)
from tests.unit.application.test_play_use_cases import HAVE_PLANKS, _judged, _obs, _session

CANDS = (Candidate("dig oak_log at 1,70,2", {"verb": "dig"}), Candidate("wait", {"verb": "wait"}))
# ブリッジは引数のない技にも、空の properties のスキーマを返す
NO_ARGS = {"type": "object", "properties": {}}
GOOD_SKILL = SkillInfo("chop_tree", "木を 1 本切る", 2, params=NO_ARGS, verified=True, successes=3)
PARAM_SKILL = SkillInfo(
    "mine",
    "石を掘る",
    1,
    params={"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]},
    verified=True,
)
OPTIONAL_SKILL = SkillInfo(
    "hunt",
    "動物を狩る",
    1,
    params={"type": "object", "properties": {"animal": {"type": "string"}}},
    verified=True,
)
NEW_SKILL = SkillInfo("farm", "畑を作る", 1, verified=False)


def _picker(answer=None, error=None, **kwargs):
    selector = AsyncMock()
    if error is not None:
        selector.select.side_effect = error
    else:
        selector.select.return_value = answer
    recorder = Mock()
    return StepPicker(selector, recorder=recorder, **kwargs), selector, recorder


def _outcome(ok=True, refused=False, call=None, result="done"):
    return ToolOutcome(
        call=call or ToolCall("do_suggestion", {"id": "wait"}),
        ok=ok,
        result=result,
        seconds=1.5,
        refused=refused,
    )


async def _pick(picker, skills=(), last_ok=True, candidates=CANDS):
    return await picker.pick({"goal": "have(planks, 4)"}, "pick one", candidates, list(skills), last_ok)


@pytest.mark.asyncio
async def test_jev_picks_a_candidate_and_it_runs_as_do_suggestion():
    picker, selector, recorder = _picker(ActionDecision("dig oak_log at 1,70,2", 0.8))
    picked = await _pick(picker)
    assert isinstance(picked, PickedStep)
    assert picked.call.name == "do_suggestion" and picked.call.args == {"id": "dig oak_log at 1,70,2"}
    offered = [c.action_id for c in selector.select.await_args.args[1]]
    assert offered == ["dig oak_log at 1,70,2", "wait", ASK_GEMINI]
    assert recorder.record.call_args.args[0]["chosen"] == "dig oak_log at 1,70,2"


@pytest.mark.asyncio
async def test_only_skills_that_worked_and_take_no_arguments_are_offered():
    picker, selector, _ = _picker(ActionDecision("run_skill chop_tree", 0.9))
    picked = await _pick(picker, skills=[GOOD_SKILL, PARAM_SKILL, NEW_SKILL, OPTIONAL_SKILL])
    offered = [c.action_id for c in selector.select.await_args.args[1]]
    assert "run_skill chop_tree" in offered and "run_skill hunt" in offered
    assert "run_skill mine" not in offered and "run_skill farm" not in offered
    assert picked.skill == GOOD_SKILL and picked.call.args == {"name": "chop_tree"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer,error,why",
    [
        (ActionDecision(ASK_GEMINI, 0.9), None, "ask the streamer"),
        (ActionDecision("wait", 0.3), None, "unsure"),
        (ActionDecision("fly", 0.9), None, "no usable answer"),
        (None, ActionSelectionError("down"), "did not answer"),
    ],
)
async def test_gemini_decides_when_jev_does_not(answer, error, why):
    picker, _, recorder = _picker(answer, error)
    picked = await _pick(picker)
    assert isinstance(picked, StepToGemini) and why in picked.why
    assert recorder.record.call_args.args[0]["to_gemini"]


@pytest.mark.asyncio
async def test_a_slow_jev_goes_to_gemini():
    selector = AsyncMock()

    async def slow(*a):
        await asyncio.sleep(1)

    selector.select.side_effect = slow
    picked = await StepPicker(selector, timeout_seconds=0.01).pick({}, "", CANDS, [], True)
    assert isinstance(picked, StepToGemini) and "TimeoutError" in picked.why


@pytest.mark.asyncio
async def test_after_a_failed_tool_or_with_nothing_to_offer_gemini_decides_without_asking_jev():
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    assert "last tool failed" in (await _pick(picker, last_ok=False)).why
    assert "no candidates" in (await _pick(picker, candidates=())).why
    selector.select.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_jev_steps_and_long_streaks_hand_over_to_gemini():
    picker, _, _ = _picker(ActionDecision("wait", 0.9), max_failures=2, gemini_every=3)
    picker.note(by_jev=True, outcome=_outcome(ok=False))
    picker.note(by_jev=True, outcome=_outcome(ok=False))
    assert "steps failed" in (await _pick(picker)).why
    picker.note(by_jev=False, outcome=_outcome())  # Gemini が選ぶと数え直す
    assert isinstance(await _pick(picker), PickedStep)
    for _ in range(3):
        picker.note(by_jev=True, outcome=_outcome())
    assert "in a row" in (await _pick(picker)).why


def _records(recorder):
    return [c.args[0] for c in recorder.record.call_args_list]


@pytest.mark.asyncio
async def test_an_unsure_answer_records_what_jev_leaned_to_and_the_probabilities():
    answer = ActionDecision(
        ASK_GEMINI, 0.4, {"dig oak_log at 1,70,2": 0.3, "wait": 0.1, ASK_GEMINI: 0.4}
    )
    picker, _, recorder = _picker(answer)
    assert isinstance(await _pick(picker), StepToGemini)
    (pick,) = _records(recorder)
    assert pick["event"] == "pick" and pick["step"] == 1 and pick["asked_jev"] is True
    assert pick["chosen"] is None and pick["jev_answer"] == ASK_GEMINI
    assert pick["top"] == "dig oak_log at 1,70,2" and pick["top_p"] == 0.3
    assert pick["probabilities"][ASK_GEMINI] == 0.4
    assert pick["jev_seconds"] >= 0 and "ask the streamer" in pick["to_gemini"]


@pytest.mark.asyncio
async def test_a_step_that_never_reaches_jev_is_recorded_with_its_reason():
    picker, selector, recorder = _picker(ActionDecision("wait", 0.9))
    await _pick(picker, last_ok=False)
    await _pick(picker, candidates=())
    picker.skip("have(planks, 4)", "nothing changed for 10 s")
    first, second, third = _records(recorder)
    assert first["asked_jev"] is False and "last tool failed" in first["to_gemini"]
    assert first["options"] == ["dig oak_log at 1,70,2", "wait", ASK_GEMINI]
    assert second["options"] == [ASK_GEMINI] and "no candidates" in second["to_gemini"]
    assert third["goal"] == "have(planks, 4)" and third["to_gemini"] == "nothing changed for 10 s"
    assert [r["step"] for r in (first, second, third)] == [1, 2, 3]
    selector.select.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_result_is_recorded_with_the_step_it_belongs_to():
    picker, _, recorder = _picker(ActionDecision("dig oak_log at 1,70,2", 0.8))
    picked = await _pick(picker)
    picker.note(
        by_jev=True, outcome=_outcome(ok=False, refused=True, call=picked.call, result="not ripe")
    )
    pick, result = _records(recorder)
    assert result["event"] == "result" and result["step"] == pick["step"]
    assert result["by"] == "jev" and result["option"] == "dig oak_log at 1,70,2"
    assert result["ok"] is False and result["refused"] is True and result["result"] == "not ripe"
    assert "in_options" not in result


@pytest.mark.asyncio
async def test_a_step_gemini_chose_says_whether_it_matches_jevs_top_option():
    answer = ActionDecision(ASK_GEMINI, 0.4, {"wait": 0.35, "dig oak_log at 1,70,2": 0.2})
    picker, _, recorder = _picker(answer)
    await _pick(picker)
    same = ToolCall("do_suggestion", {"id": "wait"})
    picker.note(by_jev=False, outcome=_outcome(call=same))
    await _pick(picker)
    other = ToolCall("goto", {"x": 1, "y": 70, "z": 2})
    picker.note(by_jev=False, outcome=_outcome(call=other))
    result_a, result_b = [r for r in _records(recorder) if r["event"] == "result"]
    assert result_a["by"] == "gemini" and result_a["in_options"] and result_a["is_top"]
    assert result_b["option"] is None and not result_b["in_options"] and not result_b["is_top"]


class TestJevFirstStep:
    """AdvancePlay の道具モード: Jev が選べば Gemini を呼ばない。"""

    @pytest.fixture
    def parts(self):
        bridge = AsyncMock()
        bridge.observe.return_value = _obs()
        bridge.set_goal.return_value = GoalStatus(met=False, remaining=3)
        bridge.check.side_effect = _judged()
        bridge.state.return_value = {"action": None}
        watcher = AsyncMock()
        watcher.run.side_effect = lambda call: ToolOutcome(call, True, "dug oak_log", 2.0)
        selector = AsyncMock()
        text_generator = AsyncMock()
        prompt_builder = Mock()
        prompt_builder.build_tool_prompt.return_value = "tool prompt"
        prompt_builder.build_action_context.return_value = ({"goal": "g"}, "pick")
        events = AsyncMock()
        store = Mock()
        store.load.return_value = None
        notes = Mock()
        notes.load.return_value = None
        use_case = AdvancePlayUseCase(
            bridge=bridge,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            action_selector=selector,
            event_publisher=events,
            conversation=Conversation(),
            objectives=ObjectiveKeeper(bridge=bridge, event_publisher=events, store=store),
            town=AsyncMock(),
            notes=NoteKeeper(notes),
            control="tools",
            tool_watcher=watcher,
            step_picker=StepPicker(selector),
        )
        return use_case, selector, text_generator, watcher

    @pytest.mark.asyncio
    async def test_jev_picks_the_step_and_gemini_is_not_called(self, parts):
        use_case, selector, text_generator, watcher = parts
        selector.select.return_value = ActionDecision("dig oak_log at 1,70,2", 0.9)
        session = _session(HAVE_PLANKS)
        during = []
        run = watcher.run.side_effect

        async def watched(call):
            during.append(session.intent)
            return run(call)

        watcher.run.side_effect = watched

        report = await use_case.execute(session)

        text_generator.choose_tool.assert_not_awaited()
        call = watcher.run.await_args.args[0]
        assert call.name == "do_suggestion" and call.args == {"id": "dig oak_log at 1,70,2"}
        assert report.decision.action_id == "dig oak_log at 1,70,2" and report.result.ok
        assert during == ["dig oak_log at 1,70,2"]
        assert session.intent == ""

    @pytest.mark.asyncio
    async def test_when_jev_asks_gemini_the_step_goes_to_gemini(self, parts):
        use_case, selector, text_generator, watcher = parts
        selector.select.return_value = ActionDecision(ASK_GEMINI, 0.9)
        text_generator.choose_tool.return_value = ToolChoice("wait", {"intent": "待つ"})

        await use_case.execute(_session(HAVE_PLANKS))

        text_generator.choose_tool.assert_awaited()
        assert watcher.run.await_args.args[0].name == "wait"


@pytest.mark.asyncio
async def test_a_stuck_record_ends_the_goal_for_gemini(monkeypatch):
    """行き詰まりの確認（34 §9）が「行き詰まっている」なら、次の切れ目で考え直す。"""
    from ailoveshen.application.use_cases.stuck_check import StuckCheck
    from ailoveshen.domain.value_objects import FastAnswer, FastVerdict

    bridge = AsyncMock()
    bridge.observe.return_value = _obs()
    from ailoveshen.domain.value_objects import ActionResult

    bridge.act.return_value = ActionResult("dig oak_log at 1,70,2", False, "no path", 1.0)
    judge = AsyncMock()
    judge.ask.return_value = FastVerdict({"stuck": FastAnswer("stuck", "failing", 0.9)}, 20)
    selector = AsyncMock()
    selector.select.return_value = ActionDecision("dig oak_log at 1,70,2", 0.9)
    store = Mock()
    store.load.return_value = None
    notes = Mock()
    notes.load.return_value = None
    events = AsyncMock()
    use_case = AdvancePlayUseCase(
        bridge=bridge,
        text_generator=AsyncMock(),
        prompt_builder=Mock(**{"build_action_context.return_value": ({}, "pick")}),
        action_selector=selector,
        event_publisher=events,
        conversation=Conversation(),
        objectives=ObjectiveKeeper(bridge=bridge, event_publisher=events, store=store),
        town=AsyncMock(),
        notes=NoteKeeper(notes),
        stuck_check=StuckCheck(judge, every_steps=2),
    )
    session = _session(HAVE_PLANKS)
    await use_case.execute(session)
    assert session.rethink_reason == ""
    await use_case.execute(session)
    assert "is stuck (failing" in session.rethink_reason
    assert "is reconsidered: Jev judged" in session.goal_end_reason(_obs())


@pytest.mark.asyncio
async def test_a_torch_is_placed_without_asking_jev_when_it_is_dark():
    """暗くて松明を持っている（ブリッジは暗いときだけこの手を出す）: 何も見えないので聞かずに置く。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9, "wait"))
    torch = Candidate("place a torch here", {"verb": "place_torch"})
    picked = await picker.pick({"goal": "x"}, "", CANDS + (torch,), [], True)
    assert isinstance(picked, PickedStep)
    assert picked.call.args == {"id": "place a torch here"}
    selector.select.assert_not_called()


@pytest.mark.asyncio
async def test_things_dropped_at_death_are_fetched_before_anything_else_without_asking_jev():
    """死んで落とした持ち物は 5 分で消える: 暗くて松明を置く手より先に、聞かずに取りに戻る。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9, "wait"))
    torch = Candidate("place a torch here", {"verb": "place_torch"})
    recover = Candidate("recover your things where you died (30m E, 1 min ago)", {"verb": "recover_items"})
    picked = await picker.pick({"goal": "x"}, "", CANDS + (torch, recover), [], True)
    assert isinstance(picked, PickedStep)
    assert picked.option_id.startswith("recover your things")
    selector.select.assert_not_called()


RECOVER = Candidate("recover your things where you died at 1,60,2", {"verb": "recover_items"})


@pytest.mark.asyncio
@pytest.mark.parametrize("last_ok", [False, True])
async def test_things_dropped_at_death_are_fetched_even_after_a_failed_tool(last_ok):
    """死んだ直前の手は体力切れで止まった失敗になる: それで Gemini に回って取りに戻らなかった。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9, "wait"))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (RECOVER,), [], last_ok)
    assert isinstance(picked, PickedStep)
    assert picked.option_id == RECOVER.action_id
    selector.select.assert_not_called()


@pytest.mark.asyncio
async def test_things_dropped_at_death_are_fetched_after_jev_failed_or_chose_too_often():
    picker, selector, _ = _picker(ActionDecision("wait", 0.9, "wait"), max_failures=1, gemini_every=1)
    for _ in range(2):
        picker.note(by_jev=True, outcome=_outcome(ok=False))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (RECOVER,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == RECOVER.action_id
    selector.select.assert_not_called()


def test_recovery_offers_only_the_trip_back_and_nothing_else():
    picker, _, _ = _picker(ActionDecision("wait", 0.9, "wait"))
    torch = Candidate("place a torch here", {"verb": "place_torch"})
    assert picker.recovery({"goal": "x"}, CANDS + (torch,)) is None
    picked = picker.recovery({"goal": "x"}, CANDS + (torch, RECOVER))
    assert picked is not None and picked.call.args == {"id": RECOVER.action_id}


class TestRecoverBeforeDeciding:
    """死んだ直後は、小目標を決め直す前に持ち物を取りに戻る（決め直しに数十秒かかる）。"""

    @pytest.fixture
    def parts(self):
        bridge = AsyncMock()
        bridge.set_goal.return_value = GoalStatus(met=False, remaining=3)
        bridge.check.side_effect = _judged()
        bridge.state.return_value = {"action": None}
        watcher = AsyncMock()
        watcher.run.side_effect = lambda call: ToolOutcome(call, True, "recovered 16 items", 2.0)
        text_generator = AsyncMock()
        prompt_builder = Mock()
        prompt_builder.build_action_context.return_value = ({"goal": "g"}, "pick")
        events = AsyncMock()
        store = Mock()
        store.load.return_value = None
        notes = Mock()
        notes.load.return_value = None
        use_case = AdvancePlayUseCase(
            bridge=bridge,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            action_selector=AsyncMock(),
            event_publisher=events,
            conversation=Conversation(),
            objectives=ObjectiveKeeper(bridge=bridge, event_publisher=events, store=store),
            town=AsyncMock(),
            notes=NoteKeeper(notes),
            control="tools",
            tool_watcher=watcher,
            step_picker=StepPicker(AsyncMock()),
        )
        return use_case, bridge, text_generator, watcher

    @staticmethod
    def _obs_died(candidates):
        obs = _obs(candidates=candidates)
        return replace(
            obs,
            state={**obs.state, "deaths": 1},
            candidates=tuple(
                Candidate(c, {"verb": "recover_items" if c.startswith("recover") else c.split()[0]})
                for c in candidates
            ),
        )

    @pytest.mark.asyncio
    async def test_the_things_are_fetched_first_and_the_goal_is_decided_after(self, parts):
        use_case, bridge, text_generator, watcher = parts
        session = _session()
        session.observe(replace(_obs(), state={"deaths": 0}))
        session.set_goal(Goal(HAVE_PLANKS, objective_id="m1"), "day")  # 決めたときは死んだ数 0
        bridge.observe.return_value = self._obs_died((RECOVER.action_id, "wait"))

        report = await use_case.execute(session)

        assert watcher.run.await_args.args[0].args == {"id": RECOVER.action_id}
        assert report.result.ok and not report.goal_changed
        assert session.steps_in_goal == 0  # 決めていない小目標のステップには入れない
        assert session.intent == ""
        assert text_generator.mock_calls == []  # 小目標を決める LLM の呼び出しはまだない

    @pytest.mark.asyncio
    async def test_without_a_trip_back_the_goal_is_decided_as_usual(self, parts):
        use_case, bridge, text_generator, watcher = parts
        session = _session()
        session.observe(replace(_obs(), state={"deaths": 0}))
        session.set_goal(Goal(HAVE_PLANKS, objective_id="m1"), "day")
        bridge.observe.return_value = self._obs_died(("wait",))

        assert await use_case._recover_first(session, bridge.observe.return_value) is None
        watcher.run.assert_not_awaited()


PLUG = Candidate("plug the hole at 0,64,0 where oak_log was dug: water runs into it", {"verb": "plug_hole"})


@pytest.mark.asyncio
@pytest.mark.parametrize("last_ok", [False, True])
async def test_a_hole_that_water_runs_into_is_plugged_without_asking_jev(last_ok):
    """畑の縁の原木を掘って水が流れ出した: 直前の手が失敗していても、聞かずに埋める。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9, "wait"))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (PLUG,), [], last_ok)
    assert isinstance(picked, PickedStep) and picked.option_id == PLUG.action_id
    selector.select.assert_not_called()


@pytest.mark.asyncio
async def test_the_trip_back_for_the_things_comes_before_plugging():
    picker, _, _ = _picker(ActionDecision("wait", 0.9, "wait"))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (PLUG, RECOVER), [], True)
    assert picked.option_id == RECOVER.action_id


LEAVE = Candidate("climb out of the hole", {"verb": "leave_burrow"})


@pytest.mark.asyncio
async def test_a_hole_left_in_the_morning_is_climbed_out_of_without_asking_jev():
    """朝も塞いだ穴の中にいる: 隣の dig を選ばせず、先に出る。失敗した直後は LLM が見る（土がなくて出られない、など）。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9, "wait"))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (LEAVE,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == LEAVE.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (LEAVE,), [], False)
    assert not isinstance(failed, PickedStep)


CLIMB = Candidate("climb out of the closed space by piling up dirt or cobblestone", {"verb": "climb_out", "urgent": True})
CLIMB_AT_NIGHT = Candidate("climb out of the closed space by piling up dirt or cobblestone", {"verb": "climb_out"})


@pytest.mark.asyncio
async def test_shut_in_by_day_the_streamer_climbs_out_without_asking_jev_but_at_night_it_is_only_an_option():
    """閉じ込められて（昼・積む物あり: urgent）なら先に出る。失敗した直後は LLM が見る。夜（urgent なし）は普通の選択肢。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (CLIMB,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == CLIMB.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (CLIMB,), [], False)
    assert not isinstance(failed, PickedStep)
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    await picker.pick({"goal": "x"}, "", CANDS + (CLIMB_AT_NIGHT,), [], True)
    selector.select.assert_called_once()


DIG_OUT = Candidate("dig out of the closed space through the wall", {"verb": "dig_out", "urgent": True})


@pytest.mark.asyncio
async def test_shut_in_without_dirt_the_streamer_digs_out_without_asking_jev():
    """閉じ込められて積む物がない（urgent の dig_out: 水の中、または昼の小部屋）なら、聞かずに壁を掘って出る（設計書 90 §5.1）。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (DIG_OUT,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == DIG_OUT.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (DIG_OUT,), [], False)
    assert not isinstance(failed, PickedStep)
    # 失敗のあと（urgent を外した候補）は、普通の選択肢
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    quiet = Candidate(DIG_OUT.action_id, {"verb": "dig_out"})
    await picker.pick({"goal": "x"}, "", CANDS + (quiet,), [], True)
    selector.select.assert_called_once()


CLIMB_DOWN = Candidate("climb down the pillar you stand on by digging it away", {"verb": "climb_down", "urgent": True})


@pytest.mark.asyncio
async def test_on_a_pillar_by_day_the_streamer_climbs_down_without_asking_jev():
    """自分で積んだ柱の頂上（昼: urgent）なら、聞かずに掘り下げて降りる。失敗した直後は LLM が見る。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (CLIMB_DOWN,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == CLIMB_DOWN.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (CLIMB_DOWN,), [], False)
    assert not isinstance(failed, PickedStep)


TOSS = Candidate("throw away the spare items to free inventory slots", {"verb": "toss"})


@pytest.mark.asyncio
async def test_spare_items_are_thrown_away_without_asking_jev_when_the_inventory_is_full():
    """持ち物がいっぱい（ブリッジが余りがあるときだけ出す）: 拾えない・掘っても入らないので、先に空ける。失敗した直後は LLM が見る（toss の道具もある）。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9, "wait"))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (TOSS,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == TOSS.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (TOSS,), [], False)
    assert not isinstance(failed, PickedStep)


HUNGRY_EAT = Candidate("eat beef", {"verb": "eat", "item": "beef", "urgent": True})
EAT = Candidate("eat bread", {"verb": "eat", "item": "bread"})


@pytest.mark.asyncio
async def test_a_starving_streamer_eats_without_asking_jev_but_only_when_the_bridge_says_urgent():
    """飢えそう（urgent）なら、何より先に食べる。失敗した直後は LLM が見る。urgent でない食べる手は普通の選択肢。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (HUNGRY_EAT,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == HUNGRY_EAT.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (HUNGRY_EAT,), [], False)
    assert not isinstance(failed, PickedStep)
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    await picker.pick({"goal": "x"}, "", CANDS + (EAT,), [], True)
    selector.select.assert_called_once()


BURROW = Candidate("dig into the ground here and seal yourself in: your health is critical", {"verb": "dig_in", "urgent": True})
TIDY = Candidate("put the things you do not need now in the home's chest", {"verb": "tidy", "kinds": 3})
WALL_IN = Candidate("wall yourself in with the blocks you carry: your health is critical", {"verb": "wall_in", "urgent": True})
WALL_IN_OPTION = Candidate("wall yourself in with dirt here and wait until morning", {"verb": "wall_in"})
REACH_LAND = Candidate("swim to the nearest land at 10,63,0", {"verb": "reach_land", "urgent": True})


@pytest.mark.asyncio
async def test_a_critical_streamer_where_no_hole_can_be_dug_walls_in_without_asking_jev():
    """縦穴が掘れない所（砂浜・石・水のそば）で体力が危険なら、持っているブロックで囲む（urgent）。夜の選択肢としての囲みは普通の選択肢。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (WALL_IN,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == WALL_IN.action_id
    selector.select.assert_not_called()
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    await picker.pick({"goal": "x"}, "", CANDS + (WALL_IN_OPTION,), [], True)
    selector.select.assert_called_once()


@pytest.mark.asyncio
async def test_a_streamer_in_the_water_swims_to_land_without_asking_jev():
    """水の中（経路探索器が動かない）では、何を選んでも進まない。陸が見えていれば（urgent）聞かずに泳ぐ。失敗した直後は LLM が見る。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (REACH_LAND,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == REACH_LAND.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (REACH_LAND,), [], False)
    assert not isinstance(failed, PickedStep)


@pytest.mark.asyncio
async def test_a_critical_streamer_far_from_home_digs_in_without_asking_jev():
    """体力が危険で家が遠い（ブリッジが urgent を付けた穴掘り）なら、何より先に穴に入る。失敗した直後は LLM が見る。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (BURROW,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == BURROW.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (BURROW,), [], False)
    assert not isinstance(failed, PickedStep)


@pytest.mark.asyncio
async def test_things_not_needed_are_put_in_the_chest_without_asking_jev():
    """家の中で今使わない物を持っていれば、聞かずに戻す（死ぬと持ち物は全部落ちて 5 分で消える）。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (TIDY,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == TIDY.action_id
    selector.select.assert_not_called()


STASH_ALL = Candidate(
    "put everything but the weapon, armor, torches and what the goal uses in the home's chest",
    {"verb": "stash", "urgent": True},
)
STASH_BEFORE_FIGHT = Candidate("put your items in the home's chest before fighting (keep the weapon)", {"verb": "stash"})


@pytest.mark.asyncio
async def test_when_nothing_can_heal_the_streamer_the_things_are_put_away_without_asking_jev():
    """回復の手段がなく家にいる（ブリッジが urgent を付けた預け入れ）なら、聞かずに預ける。夜の戦いの前の預け入れは選ばない。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (STASH_ALL,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == STASH_ALL.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (STASH_ALL,), [], False)
    assert not isinstance(failed, PickedStep)
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    await picker.pick({"goal": "x"}, "", CANDS + (STASH_BEFORE_FIGHT,), [], True)
    selector.select.assert_called_once()


WINDFALL_NOW = Candidate(
    "collect iron_ore at 3,70,0 (seen nearby; the goal does not need it)",
    {"verb": "dig", "windfall": "now"},
)
WINDFALL_TRIP = Candidate(
    "go to the iron_ore seen at 30,70,0 and mine it (the goal does not need it)",
    {"verb": "goto_pos", "windfall": "trip"},
)


@pytest.mark.asyncio
async def test_an_ore_found_nearby_is_mined_without_asking_jev_but_a_trip_is_only_offered():
    """近くで見つけた目標外の鉱石（windfall: now）は聞かずに採る。覚えている場所への行きは候補に出すだけ。"""
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    picked = await picker.pick({"goal": "x"}, "", CANDS + (WINDFALL_NOW,), [], True)
    assert isinstance(picked, PickedStep) and picked.option_id == WINDFALL_NOW.action_id
    selector.select.assert_not_called()
    failed = await picker.pick({"goal": "x"}, "", CANDS + (WINDFALL_NOW,), [], False)
    assert not isinstance(failed, PickedStep)
    picker, selector, _ = _picker(ActionDecision("wait", 0.9))
    await picker.pick({"goal": "x"}, "", CANDS + (WINDFALL_TRIP,), [], True)
    selector.select.assert_called_once()


@pytest.mark.asyncio
async def test_detours_are_not_taken_without_asking_more_often_than_the_goals_own_steps():
    """聞かずに採る寄り道は、最初の 2 手のあとは目標の手と同じ数まで。小目標が変わると数え直す。

    8 分間 30 手のうち 24 手が寄り道で、目標の手は一度も選ばれなかった（2026-10-01）。
    """
    picker, selector, _ = _picker(ActionDecision("dig oak_log at 1,70,2", 0.9))
    state = {"goal": "have(raw_iron, 3)"}
    options = CANDS + (WINDFALL_NOW,)
    for _ in range(2):
        picked = await picker.pick(state, "", options, [], True)
        assert picked.option_id == WINDFALL_NOW.action_id
        picker.note(True, ToolOutcome(ToolCall("do_suggestion", {"id": picked.option_id}), True, "ok", 1.0))
    selector.select.assert_not_called()
    # 3 手目は聞く（Jev が目標の手を選ぶ）
    picked = await picker.pick(state, "", options, [], True)
    assert picked.option_id == "dig oak_log at 1,70,2"
    selector.select.assert_called_once()
    picker.note(True, ToolOutcome(ToolCall("do_suggestion", {"id": picked.option_id}), True, "ok", 1.0))
    # 目標の手が 1 つ増えたので、寄り道がもう 1 回できる
    picked = await picker.pick(state, "", options, [], True)
    assert picked.option_id == WINDFALL_NOW.action_id
    # 小目標が変われば数え直す
    picker.note(True, ToolOutcome(ToolCall("do_suggestion", {"id": picked.option_id}), True, "ok", 1.0))
    picked = await picker.pick({"goal": "have(torch, 16)"}, "", options, [], True)
    assert picked.option_id == WINDFALL_NOW.action_id
