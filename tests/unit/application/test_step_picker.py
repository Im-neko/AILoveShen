"""StepPicker（道具モードの 1 手をまず Jev が選ぶ、docs/design/34 §3）のテスト。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import ToolChoice
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
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
    GoalStatus,
    SkillInfo,
    ToolOutcome,
)
from tests.unit.application.test_play_use_cases import HAVE_PLANKS, _judged, _obs, _session

CANDS = (Candidate("dig oak_log at 1,70,2", {"verb": "dig"}), Candidate("wait", {"verb": "wait"}))
GOOD_SKILL = SkillInfo("chop_tree", "木を 1 本切る", 2, verified=True, successes=3)
PARAM_SKILL = SkillInfo("mine", "石を掘る", 1, params={"count": {"type": "integer"}}, verified=True)
NEW_SKILL = SkillInfo("farm", "畑を作る", 1, verified=False)


def _picker(answer=None, error=None, **kwargs):
    selector = AsyncMock()
    if error is not None:
        selector.select.side_effect = error
    else:
        selector.select.return_value = answer
    recorder = Mock()
    return StepPicker(selector, recorder=recorder, **kwargs), selector, recorder


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
    picked = await _pick(picker, skills=[GOOD_SKILL, PARAM_SKILL, NEW_SKILL])
    offered = [c.action_id for c in selector.select.await_args.args[1]]
    assert "run_skill chop_tree" in offered
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
    picker.note(by_jev=True, ok=False)
    picker.note(by_jev=True, ok=False)
    assert "steps failed" in (await _pick(picker)).why
    picker.note(by_jev=False, ok=True)  # Gemini が選ぶと数え直す
    assert isinstance(await _pick(picker), PickedStep)
    for _ in range(3):
        picker.note(by_jev=True, ok=True)
    assert "in a row" in (await _pick(picker)).why


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
            mid_goals=MidGoalKeeper(bridge=bridge, event_publisher=events, store=store),
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

        report = await use_case.execute(session)

        text_generator.choose_tool.assert_not_awaited()
        call = watcher.run.await_args.args[0]
        assert call.name == "do_suggestion" and call.args == {"id": "dig oak_log at 1,70,2"}
        assert report.decision.action_id == "dig oak_log at 1,70,2" and report.result.ok
        assert session.intent == "dig oak_log at 1,70,2"

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
        mid_goals=MidGoalKeeper(bridge=bridge, event_publisher=events, store=store),
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
