"""道具での操作のテスト（docs/design/21_tool_control.md）: 道具の定義、見張り、道具のステップ。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import ToolChoice
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase
from ailoveshen.application.use_cases.tool_catalog import (
    ToolCallError,
    parse_tool_call,
    tool_specs,
)
from ailoveshen.application.use_cases.watcher import PROGRESS, ToolWatcher, WatchPolicy
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import GameActionExecutedEvent
from ailoveshen.domain.value_objects import (
    FastAnswer,
    FastVerdict,
    GoalStatus,
    ToolCall,
    ToolOutcome,
    WatchAction,
    WatchQuestion,
)
from tests.unit.application.test_play_use_cases import HAVE_PLANKS, _judged, _obs, _session


class TestToolCatalog:
    """道具の定義と、呼び出しの検証。"""

    def test_action_tools_require_an_intent_and_accept_watch_questions(self):
        specs = {t.name: t for t in tool_specs()}
        dig = specs["dig"].parameters
        assert dig["required"] == ["intent", "x", "y", "z"]
        assert dig["properties"]["watch"]["maxItems"] == 3
        assert "intent" not in specs["find_blocks"].parameters["properties"]

    def test_parse_splits_intent_and_watch_from_the_args(self):
        call = parse_tool_call(
            ToolChoice(
                "dig",
                {
                    "x": 1,
                    "y": 70,
                    "z": 2,
                    "intent": "前の家のベッドを取りに行く",
                    "watch": [{"question": "Is a hostile mob within 6 m?", "on_yes": "wake"}],
                },
            )
        )
        assert call.args == {"x": 1, "y": 70, "z": 2}
        assert call.intent == "前の家のベッドを取りに行く"
        assert call.watch == (WatchQuestion("Is a hostile mob within 6 m?", WatchAction.WAKE),)
        assert call.describe() == "dig(x=1, y=70, z=2)"

    def test_unusable_watch_questions_are_dropped_not_the_call(self):
        call = parse_tool_call(
            ToolChoice(
                "goto",
                {
                    "x": 0,
                    "y": 70,
                    "z": 0,
                    "intent": "i",
                    "watch": [
                        {"question": "", "on_yes": "stop"},
                        {"question": "q", "on_yes": "explode"},
                        {"question": "a", "on_yes": "stop"},
                        {"question": "b", "on_yes": "stop"},
                        {"question": "c", "on_yes": "stop"},
                        {"question": "d", "on_yes": "stop"},
                    ],
                },
            )
        )
        assert [w.question for w in call.watch] == ["a", "b", "c"]

    def test_unknown_tools_and_missing_intents_are_errors(self):
        with pytest.raises(ToolCallError, match="unknown tool"):
            parse_tool_call(ToolChoice("teleport", {}))
        with pytest.raises(ToolCallError, match="needs an intent"):
            parse_tool_call(ToolChoice("dig", {"x": 1, "y": 2, "z": 3}))
        # 調べものには intent は要らない
        assert parse_tool_call(ToolChoice("recipe_of", {"item": "stick"})).args == {"item": "stick"}


class FakeBridge:
    """道具を実行し、中断されるまで（または finish() まで）終わらないブリッジ。"""

    def __init__(self, state=None):
        self._done = asyncio.Event()
        self.result = (True, "done", 1.0, False)
        self.aborts: list[str] = []
        self._state = state if state is not None else {"action": {"verb": "dig"}}

    async def run_tool(self, name, args):
        await self._done.wait()
        return self.result

    async def state(self):
        return self._state

    async def abort(self, reason):
        self.aborts.append(reason)
        self.result = (False, f"failed: {reason}", 1.0, False)
        self._done.set()
        return True

    def finish(self):
        self._done.set()


def _judge(*ticks):
    """ティックごとの答え（名前 -> (はいか, 確信度)）を順に返す。尽きたら最後を繰り返す。"""
    judge = AsyncMock()
    answers = [
        FastVerdict(
            answers={n: FastAnswer(n, v, c) for n, (v, c) in t.items()},
            elapsed_ms=5,
            input_tokens=100,
        )
        for t in ticks
    ]

    async def ask(state, questions):
        judge.asked.append([q.name for q in questions])
        return answers[min(len(judge.asked) - 1, len(answers) - 1)]

    judge.asked = []
    judge.ask.side_effect = ask
    return judge


FAST = WatchPolicy(interval_seconds=0.01, grace_seconds=0.0)
WAKE = ToolCall(
    "dig",
    {"x": 1, "y": 70, "z": 2},
    intent="掘る",
    watch=(WatchQuestion("Has a hostile mob come within 6 m?", WatchAction.WAKE),),
)


class TestToolWatcher:
    """見張り: 質問はまとめて 1 回で聞き、「はい」が続いたら決まったことだけを起こす。"""

    @pytest.mark.asyncio
    async def test_a_gemini_question_answered_yes_twice_wakes_gemini(self):
        bridge = FakeBridge()
        judge = _judge({PROGRESS: (True, 0.9), "watch_0": (True, 0.8)})
        recorder = Mock()

        outcome = await ToolWatcher(bridge, judge, recorder, FAST).run(WAKE)

        assert bridge.aborts == ["woke up: Has a hostile mob come within 6 m? (0.80)"]
        assert not outcome.ok
        assert outcome.stopped_by == bridge.aborts[0]
        assert judge.asked[0] == [PROGRESS, "watch_0"]  # 1 回にまとめる
        assert len(judge.asked) == 2  # 2 回続いてから
        assert recorder.record.call_args.args[0]["fired"] == bridge.aborts[0]
        assert recorder.record.call_args.args[0]["input_tokens"] == 100

    @pytest.mark.asyncio
    async def test_a_single_yes_or_a_weak_yes_does_not_act(self):
        bridge = FakeBridge()
        judge = _judge(
            {"watch_0": (True, 0.9)},
            {"watch_0": (False, 0.9)},
            {"watch_0": (True, 0.6)},
            {"watch_0": (True, 0.65)},
        )
        watcher = ToolWatcher(bridge, judge, None, FAST)
        task = asyncio.ensure_future(watcher.run(WAKE))
        while len(judge.asked) < 6:
            await asyncio.sleep(0.005)
        bridge.finish()

        outcome = await task

        assert bridge.aborts == []
        assert outcome.ok and outcome.stopped_by is None

    @pytest.mark.asyncio
    async def test_the_progress_question_only_records_until_it_may_act(self):
        """コードの 1 問は、規則に勝つまで記録だけ（19 §13 の 4）。"""
        call = ToolCall("goto", {"x": 0, "y": 70, "z": 0}, intent="歩く")
        bridge = FakeBridge()
        judge = _judge({PROGRESS: (False, 0.95)})
        recorder = Mock()
        task = asyncio.ensure_future(ToolWatcher(bridge, judge, recorder, FAST).run(call))
        while len(judge.asked) < 4:
            await asyncio.sleep(0.005)
        bridge.finish()
        assert (await task).ok
        assert bridge.aborts == []
        assert recorder.record.call_args.args[0]["streaks"][PROGRESS] >= 2

        acting = WatchPolicy(interval_seconds=0.01, grace_seconds=0.0, act_on_progress=True)
        bridge = FakeBridge()
        outcome = await ToolWatcher(bridge, _judge({PROGRESS: (False, 0.95)}), None, acting).run(
            call
        )
        assert outcome.stopped_by == "stopped by watch: not making progress (0.95)"

    @pytest.mark.asyncio
    async def test_no_questions_in_the_grace_period_or_when_nothing_runs(self):
        bridge = FakeBridge(state={"action": None})
        judge = _judge({"watch_0": (True, 0.9)})
        watcher = ToolWatcher(bridge, judge, None, FAST)
        task = asyncio.ensure_future(watcher.run(WAKE))
        await asyncio.sleep(0.05)
        bridge.finish()
        await task
        assert judge.asked == []

        bridge = FakeBridge()
        slow = WatchPolicy(interval_seconds=0.01, grace_seconds=60.0)
        task = asyncio.ensure_future(ToolWatcher(bridge, judge, None, slow).run(WAKE))
        await asyncio.sleep(0.05)
        bridge.finish()
        await task
        assert judge.asked == []

    @pytest.mark.asyncio
    async def test_a_failing_judge_does_not_stop_the_tool(self):
        from ailoveshen.domain.exceptions import ActionSelectionError

        bridge = FakeBridge()
        judge = AsyncMock()
        judge.ask.side_effect = ActionSelectionError("Jev down")
        task = asyncio.ensure_future(ToolWatcher(bridge, judge, None, FAST).run(WAKE))
        await asyncio.sleep(0.05)
        bridge.finish()
        outcome = await task
        assert outcome.ok and bridge.aborts == []


class TestToolStep:
    """control: tools の 1 ステップ: LLM が道具を呼び、見張りつきで実行する。"""

    @pytest.fixture
    def bridge(self):
        b = AsyncMock()
        b.observe.return_value = _obs()
        b.set_goal.return_value = GoalStatus(met=False, remaining=3)
        b.check.side_effect = _judged()
        b.state.return_value = {"action": None, "surroundings": {}, "mobs": []}
        b.run_tool.return_value = (True, '{"crafting": []}', 0.0, False)
        return b

    @pytest.fixture
    def watcher(self):
        w = AsyncMock()
        w.run.side_effect = lambda call: ToolOutcome(call, True, "dug oak_log", 2.0)
        return w

    @pytest.fixture
    def use_case(self, bridge, watcher):
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
            mid_goals=MidGoalKeeper(bridge=bridge, event_publisher=self.events, store=store),
            town=AsyncMock(),
            notes=NoteKeeper(note_store),
            control="tools",
            tool_watcher=watcher,
        )

    @pytest.mark.asyncio
    async def test_a_lookup_then_an_action_in_one_step(self, use_case, bridge, watcher):
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("recipe_of", {"item": "stick"}),
            ToolChoice("dig", {"x": 1, "y": 70, "z": 2, "intent": "原木を掘る"}),
        ]
        session = _session(HAVE_PLANKS)

        report = await use_case.execute(session)

        bridge.run_tool.assert_awaited_once_with("recipe_of", {"item": "stick"})
        assert watcher.run.await_args.args[0].name == "dig"
        assert report.decision.action_id == "dig(x=1, y=70, z=2)"
        assert report.result.ok
        assert session.intent == "原木を掘る"
        assert session.activity().intent == "原木を掘る"  # 実況と返答も同じものを見る
        # 2 回目のプロンプトは調べた結果を見る
        recent = self.prompt_builder.build_tool_prompt.call_args.args[3]
        assert [o.call.name for o in recent] == ["recipe_of"]
        executed = [
            c.args[0]
            for c in self.events.publish.call_args_list
            if isinstance(c.args[0], GameActionExecutedEvent)
        ]
        assert executed[0].action_id == "dig(x=1, y=70, z=2)"
        bridge.act.assert_not_awaited()  # 候補は使わない

    @pytest.mark.asyncio
    async def test_after_too_many_lookups_only_actions_are_offered(self, use_case, watcher):
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("recipe_of", {"item": "stick"}),
            ToolChoice("recipe_of", {"item": "stick"}),
            ToolChoice("recipe_of", {"item": "stick"}),
            ToolChoice("wait", {"intent": "待つ"}),
        ]
        await use_case.execute(_session(HAVE_PLANKS))

        offered = [
            {t.name for t in c.args[1]} for c in self.text_generator.choose_tool.call_args_list
        ]
        assert "recipe_of" in offered[0] and "recipe_of" not in offered[3]
        assert watcher.run.await_args.args[0].name == "wait"

    @pytest.mark.asyncio
    async def test_an_invalid_call_fails_the_step_and_the_next_call_thinks_harder(
        self, use_case, watcher
    ):
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("dig", {"x": 1, "y": 70, "z": 2}),  # intent がない
            ToolChoice("dig", {"x": 1, "y": 70, "z": 2, "intent": "掘る"}),
        ]
        session = _session(HAVE_PLANKS)

        first = await use_case.execute(session)
        await use_case.execute(session)

        assert not first.result.ok
        assert "needs an intent" in first.result.result
        watcher.run.assert_awaited_once()
        purposes = [c.kwargs["purpose"] for c in self.text_generator.choose_tool.call_args_list]
        assert purposes == ["tool", "tool_after_failure"]

    def test_tools_control_needs_a_watcher(self, bridge):
        with pytest.raises(ValueError, match="needs a tool_watcher"):
            AdvancePlayUseCase(
                bridge=bridge,
                text_generator=AsyncMock(),
                prompt_builder=Mock(),
                action_selector=AsyncMock(),
                event_publisher=AsyncMock(),
                conversation=Conversation(),
                mid_goals=Mock(),
                town=AsyncMock(),
                notes=Mock(),
                control="tools",
            )
