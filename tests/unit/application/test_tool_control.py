"""道具での操作のテスト（docs/design/21_tool_control.md）: 道具の定義、見張り、道具のステップ。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import ToolChoice
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import MAX_QUERIES_PER_STEP, AdvancePlayUseCase
from ailoveshen.application.use_cases.tool_catalog import (
    CORE_TOOLS,
    groups_for,
    ToolCallError,
    is_query,
    parse_tool_call,
    tool_specs,
)
from ailoveshen.application.use_cases.watcher import PROGRESS, ToolWatcher, WatchPolicy
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import GameActionExecutedEvent
from ailoveshen.domain.value_objects import (
    FastAnswer,
    FastVerdict,
    GoalPredicate,
    GoalSpec,
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
                    "watch": ["wake: Is a hostile mob within 6 m?"],
                },
            )
        )
        assert call.args == {"x": 1, "y": 70, "z": 2}
        assert call.intent == "前の家のベッドを取りに行く"
        assert call.watch == (WatchQuestion("Is a hostile mob within 6 m?", WatchAction.WAKE),)
        assert call.describe() == "dig(x=1, y=70, z=2)"

    def test_the_action_copied_with_angle_brackets_from_the_prompt_is_read(self):
        call = parse_tool_call(
            ToolChoice("goto", {"x": 0, "y": 70, "z": 0, "intent": "i", "watch": ["<stop>: Is there a mob?"]})
        )
        assert call.watch == (WatchQuestion("Is there a mob?", WatchAction.STOP),)

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
                        "stop:",
                        "explode: q",
                        "no colon",
                        "stop: a",
                        "stop: b",
                        "stop: c",
                        "stop: d",
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
            objectives=ObjectiveKeeper(bridge=bridge, event_publisher=self.events, store=store),
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
        during = []
        run = watcher.run.side_effect

        async def watched(call):
            a = session.activity()  # 実況と返答も同じものを見る
            during.append((a.intent, a.action, a.acting))
            return run(call)

        watcher.run.side_effect = watched

        report = await use_case.execute(session)

        bridge.run_tool.assert_awaited_once_with("recipe_of", {"item": "stick"})
        assert watcher.run.await_args.args[0].name == "dig"
        assert report.decision.action_id == "dig(x=1, y=70, z=2)"
        assert report.result.ok
        assert during == [("原木を掘る", "ブロックを掘る", True)]
        # 終わったら消す（次を選んでいる間に、終わった行動を「これからする」と言わない）
        assert session.intent == "" and not session.activity().acting
        # 動作は次の手か小目標が変わるまで残す（手の間に述語の表示へ戻ってちらつかない）
        assert session.action == "ブロックを掘る"
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
            *[ToolChoice("find_blocks", {"block": f"block{i}"}) for i in range(MAX_QUERIES_PER_STEP)],
            ToolChoice("wait", {"intent": "待つ"}),
        ]
        await use_case.execute(_session(HAVE_PLANKS))

        offered = [
            {t.name for t in c.args[1]} for c in self.text_generator.choose_tool.call_args_list
        ]
        assert "find_blocks" in offered[0] and "find_blocks" not in offered[MAX_QUERIES_PER_STEP]
        assert watcher.run.await_args.args[0].name == "wait"

    @pytest.mark.asyncio
    async def test_the_same_lookup_twice_is_refused_and_only_actions_are_offered(
        self, use_case, bridge, watcher
    ):
        """食べ物がなくて find_foods を 5 回続け、動かなかった（2026-10-07 01:25Z）。答えは変わらない。"""
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("find_blocks", {"block": "stone"}),
            ToolChoice("find_blocks", {"block": "stone"}),
            ToolChoice("wait", {"intent": "待つ"}),
        ]
        await use_case.execute(_session(HAVE_PLANKS))

        assert bridge.run_tool.await_count == 1  # 2 回目は送らない
        offered = [
            {t.name for t in c.args[1]} for c in self.text_generator.choose_tool.call_args_list
        ]
        assert "find_blocks" in offered[1] and "find_blocks" not in offered[2]
        recent = self.prompt_builder.build_tool_prompt.call_args.args[3]
        assert recent[1].refused and "answer has not changed" in recent[1].result
        assert watcher.run.await_args.args[0].name == "wait"

    @pytest.mark.asyncio
    async def test_a_lookup_with_other_arguments_is_still_answered(self, use_case, bridge, watcher):
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("recipe_of", {"item": "stick"}),
            ToolChoice("recipe_of", {"item": "planks"}),
            ToolChoice("wait", {"intent": "待つ"}),
        ]
        await use_case.execute(_session(HAVE_PLANKS))

        assert bridge.run_tool.await_count == 2

    @pytest.mark.asyncio
    async def test_a_search_is_answered_in_python_from_the_bridges_list(
        self, use_case, bridge, watcher
    ):
        """search_facility はブリッジの道具に送らず、施設の一覧から選んで返す（docs/design/47 §4）。"""
        bridge.facilities.return_value = [
            {"name": "home_farm", "kind": "farm", "status": "done", "distance_m": 12,
             "direction": "north", "contents": {"crops": {"wheat": {"planted": 9, "ripe": 3}}}},
        ]
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("search_facility", {"query": "wheat farm"}),
            ToolChoice("wait", {"intent": "待つ"}),
        ]
        await use_case.execute(_session(HAVE_PLANKS))

        bridge.run_tool.assert_not_awaited()
        recent = self.prompt_builder.build_tool_prompt.call_args.args[3]
        assert recent[0].call.name == "search_facility" and recent[0].ok
        assert "home_farm" in recent[0].result
        assert watcher.run.await_args.args[0].name == "wait"

    @pytest.mark.asyncio
    async def test_a_tool_outside_the_core_comes_after_search_abilities_and_stays_for_the_small_goal(
        self, use_case, bridge, watcher
    ):
        """道具は毎回渡すものだけ。search_abilities が引いた項目の道具が、その小目標のあいだ足される（設計書 169）。"""
        from ailoveshen.application.use_cases.search import WorldSearch, KeySearch

        use_case._search = WorldSearch(
            bridge,
            KeySearch(None),
            [{"key": "make_farm", "title": "畑を作る", "words": "farm 畑", "when": "畑", "how": "till", "tools": ["till", "sow", "craft"]}],
        )
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("search_abilities", {"query": "make_farm"}),
            *[ToolChoice("wait", {"intent": "待つ"}) for _ in range(3)],
        ]
        await use_case.execute(_session(HAVE_PLANKS))
        offered = [{t.name for t in c.args[1]} for c in self.text_generator.choose_tool.call_args_list]
        assert {"till", "sow"}.isdisjoint(offered[0]) and {"till", "sow"} <= offered[1]
        recent = self.prompt_builder.build_tool_prompt.call_args.args[3]
        assert "tools you can call now: till, sow" in recent[0].result  # 道具に足したことを返す（craft は元からある）

        await use_case.execute(_session(HAVE_PLANKS))  # 同じ小目標の次の手にも残る
        assert {"till", "sow"} <= {t.name for t in self.text_generator.choose_tool.call_args.args[1]}
        await use_case.execute(_session(GoalSpec(GoalPredicate.THROUGH_NIGHT)))  # 小目標が変われば空
        assert {"till", "sow"}.isdisjoint({t.name for t in self.text_generator.choose_tool.call_args.args[1]})

    @pytest.mark.asyncio
    async def test_the_advisor_is_asked_with_what_she_saw_and_its_answer_comes_back_as_a_tool_record(
        self, use_case, bridge, watcher
    ):
        """ask_advisor はブリッジの道具に送らず、迷った時のプロンプトと質問を advisor に渡す（docs/design/143）。"""
        advisor = AsyncMock()
        advisor.ask.return_value = (True, "先に原木を切る")
        use_case._advisor = advisor
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("ask_advisor", {"question": "木が見つからない。どうする？"}),
            ToolChoice("wait", {"intent": "待つ"}),
        ]
        await use_case.execute(_session(HAVE_PLANKS))

        advisor.ask.assert_awaited_once_with("木が見つからない。どうする？", "tool prompt")
        bridge.run_tool.assert_not_awaited()
        recent = self.prompt_builder.build_tool_prompt.call_args.args[3]
        assert recent[0].call.name == "ask_advisor" and recent[0].ok
        assert recent[0].result == "先に原木を切る"
        offered = [t.name for t in self.text_generator.choose_tool.call_args_list[0].args[1]]
        assert "ask_advisor" in offered

    @pytest.mark.asyncio
    async def test_without_an_advisor_the_tool_is_not_offered(self, use_case):
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})]
        await use_case.execute(_session(HAVE_PLANKS))

        offered = [t.name for t in self.text_generator.choose_tool.call_args_list[0].args[1]]
        assert "ask_advisor" not in offered

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

    @pytest.mark.asyncio
    async def test_the_step_waits_for_the_reflex_instead_of_asking_the_model(
        self, use_case, bridge, monkeypatch
    ):
        """反射が動いている間は道具が断られる。選ばせて 1 手を無駄にせず、終わるまで待つ。"""
        import ailoveshen.application.use_cases.play as play

        monkeypatch.setattr(play, "REFLEX_POLL_SECONDS", 0.001)
        quiet = {"action": None, "surroundings": {}, "mobs": [], "reflex": False}
        bridge.state.side_effect = [
            {**quiet, "reflex": True},
            {**quiet, "reflex": True},
            quiet,
            quiet,
        ]
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})]

        await use_case.execute(_session(HAVE_PLANKS))

        assert bridge.state.await_count == 4  # 待つ間に 3 回、選ぶときに 1 回
        self.text_generator.choose_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_reflex_that_never_ends_is_released_after_the_limit(self, use_case, bridge, monkeypatch):
        """反射が終わらないなら、待ち続けず手放して選ばせる（止まっている検知に届かなくなる）。"""
        import ailoveshen.application.use_cases.play as play

        monkeypatch.setattr(play, "REFLEX_POLL_SECONDS", 0.001)
        monkeypatch.setattr(play, "REFLEX_WAIT_LIMIT_SECONDS", 0.005)
        busy = {"action": None, "surroundings": {}, "mobs": [], "reflex": True}
        quiet = {**busy, "reflex": False}
        bridge.state.side_effect = [busy] * 20 + [quiet] * 5
        bridge.release_reflex = AsyncMock(return_value=True)
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})]

        await use_case.execute(_session(HAVE_PLANKS))

        bridge.release_reflex.assert_awaited_once_with(play.REFLEX_RELEASE_SECONDS)
        self.text_generator.choose_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pickup_is_not_offered_when_nothing_lies_on_the_ground(self, use_case, bridge):
        bridge.state.return_value = {"action": None, "surroundings": {}, "mobs": [], "pickup_ready": False}
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})]
        await use_case.execute(_session(HAVE_PLANKS))
        offered = {t.name for t in self.text_generator.choose_tool.call_args.args[1]}
        assert "pickup" not in offered and "dig" in offered

        bridge.state.return_value = {"action": None, "surroundings": {}, "mobs": [], "pickup_ready": True}
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})]
        await use_case.execute(_session(HAVE_PLANKS))
        offered = {t.name for t in self.text_generator.choose_tool.call_args.args[1]}
        assert "pickup" in offered

    @pytest.mark.asyncio
    async def test_experiment_tools_are_offered_only_once_the_step_is_stuck(self, use_case, bridge):
        bridge.state.return_value = {"action": None, "surroundings": {}, "mobs": []}
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})]
        await use_case.execute(_session(HAVE_PLANKS))
        offered = {t.name for t in self.text_generator.choose_tool.call_args.args[1]}
        assert "inspect" not in offered and "diagnostics" not in offered

        stuck = _session(HAVE_PLANKS)
        stuck.consecutive_failures = 2
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})]
        await use_case.execute(stuck)
        offered = {t.name: t for t in self.text_generator.choose_tool.call_args.args[1]}
        assert {"inspect", "diagnostics"} <= offered.keys()
        assert "face" in offered["use_item"].parameters["properties"]

    @pytest.mark.asyncio
    async def test_a_failing_small_goal_makes_the_model_think_and_try_another_way(self, use_case):
        session = _session(HAVE_PLANKS)
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})] * 2
        await use_case.execute(session)
        session.consecutive_failures = 2
        await use_case.execute(session)

        purposes = [c.kwargs["purpose"] for c in self.text_generator.choose_tool.call_args_list]
        assert purposes[-1] == "tool_stuck"
        failing = [c.kwargs["failing"] for c in self.prompt_builder.build_tool_prompt.call_args_list]
        assert failing == [0, 2]

    @pytest.mark.asyncio
    async def test_the_same_refusal_over_and_over_is_named_and_then_the_tool_is_not_offered(self, use_case):
        """座標の数字を変えて同じ理由で断られ続ける（dig の z=285 が 8 回）: 理由を示し、続けば道具を出さない。"""
        from ailoveshen.domain.value_objects import ToolCall, ToolOutcome

        def refused(x: int) -> ToolOutcome:
            return ToolOutcome(
                call=ToolCall("dig", {"x": x, "y": 77, "z": 285}),
                ok=False,
                result=f"refused: the block at {x},77,285 is 575.7m away (at most 32m)",
                seconds=0.0,
                refused=True,
            )

        session = _session(HAVE_PLANKS)
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})] * 2
        use_case._recent_tools.extend(refused(x) for x in (54, 55, 56))
        await use_case.execute(session)
        kw = self.prompt_builder.build_tool_prompt.call_args.kwargs
        assert (kw["same_cause"].tool, kw["same_cause"].times, kw["same_cause"].withheld) == ("dig", 3, False)
        offered = {t.name for t in self.text_generator.choose_tool.call_args.args[1]}
        assert "dig" in offered

        use_case._recent_tools.clear()
        use_case._recent_tools.extend(refused(x) for x in (54, 55, 56, 57))
        await use_case.execute(session)
        assert self.prompt_builder.build_tool_prompt.call_args.kwargs["same_cause"].withheld
        offered = {t.name for t in self.text_generator.choose_tool.call_args.args[1]}
        assert "dig" not in offered and "goto" in offered

    @pytest.mark.asyncio
    async def test_when_nothing_changes_the_model_chooses_again_with_the_record_and_jev_is_skipped(
        self, use_case, monkeypatch
    ):
        from ailoveshen.application.use_cases.stagnation import Stagnation
        from ailoveshen.application.use_cases.step_picker import StepToGemini

        monkeypatch.setattr(use_case, "_stagnation", Stagnation(10))
        use_case._step_picker = Mock()
        use_case._step_picker.pick = AsyncMock()
        use_case._step_picker.note = Mock()
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})] * 3
        session = _session(HAVE_PLANKS)
        self.prompt_builder.build_action_context.return_value = ({}, "")
        use_case._step_picker.pick.return_value = StepToGemini("test: not sure")
        for _ in range(3):
            _, result = await use_case._act_with_tools(session, _obs())
            use_case._stagnation.record(f"dig → ok: {result.result}", 8)  # execute が手ごとにすること

        purposes = [c.kwargs["purpose"] for c in self.text_generator.choose_tool.call_args_list]
        assert purposes[-1] == "tool_stuck" and purposes[0] != "tool_stuck"
        assert use_case._step_picker.pick.await_count == 2  # 3 回目は Jev に聞かない
        kw = self.prompt_builder.build_tool_prompt.call_args.kwargs
        assert kw["stagnant_seconds"] == 16 and len(kw["stagnant_actions"]) == 2
        assert session.activity().stagnant_seconds == 16

    @pytest.mark.asyncio
    async def test_a_repeated_pattern_goes_to_the_llm_with_the_pattern_and_jev_is_skipped(
        self, use_case, bridge
    ):
        from ailoveshen.application.use_cases.step_picker import StepToGemini

        bridge.skills = AsyncMock(return_value=[])
        use_case._skills = Mock()
        use_case._step_picker = Mock()
        use_case._step_picker.pick = AsyncMock(return_value=StepToGemini("test: not sure"))
        use_case._step_picker.note = Mock()
        self.prompt_builder.build_action_context.return_value = ({}, "")
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})] * 2
        session = _session(HAVE_PLANKS)

        await use_case._act_with_tools(session, _obs())
        assert self.prompt_builder.build_tool_prompt.call_args.kwargs["repeated"] == ()
        for verb in ["till", "sow"] * 3:
            use_case._repetition.record(verb, True)
        await use_case._act_with_tools(session, _obs())

        kw = self.prompt_builder.build_tool_prompt.call_args.kwargs
        assert kw["repeated"] == ("till", "sow") and kw["repeated_times"] == 3
        assert use_case._step_picker.pick.await_count == 1  # 2 回目は Jev に聞かない

    @pytest.mark.asyncio
    async def test_jev_still_chooses_after_a_refusal_but_not_after_a_real_failure(self, use_case):
        """断られた手は台帳で次から出ないので、決まりきった断りで LLM に回さず Jev が残りから選ぶ。"""
        from ailoveshen.application.use_cases.step_picker import StepToGemini

        use_case._step_picker = Mock()
        use_case._step_picker.pick = AsyncMock(return_value=StepToGemini("test"))
        use_case._step_picker.note = Mock()
        self.prompt_builder.build_action_context.return_value = ({}, "")
        session = _session(HAVE_PLANKS)
        call = ToolCall("dig", {"x": 1, "y": 2, "z": 3}, intent="掘る")
        obs = _obs()
        self.text_generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})] * 2

        use_case._recent_tools.append(ToolOutcome(call, False, "refused: will not dig", 0.0, refused=True))
        await use_case._act_with_tools(session, obs)
        assert use_case._step_picker.pick.await_args.args[-1] is True

        use_case._recent_tools.append(ToolOutcome(call, False, "failed: No path", 1.0))
        await use_case._act_with_tools(session, obs)
        assert use_case._step_picker.pick.await_args.args[-1] is False

    @pytest.mark.asyncio
    async def test_a_step_the_llm_chose_is_remembered_and_offered_to_jev_next_time(self, use_case, watcher):
        """設計書 52: LLM が選んで持ち物が変わった手は帳に入り、次の同じ状況で Jev の選択肢に出る。"""
        from dataclasses import replace

        from ailoveshen.application.use_cases.learned_candidates import LearnedCandidates
        from ailoveshen.application.use_cases.step_picker import PickedStep, StepToGemini

        class Store:
            def load(self):
                return None

            def save(self, learned, next_id):
                pass

        book = LearnedCandidates(Store())
        use_case._learned = book
        use_case._step_picker = Mock()
        use_case._step_picker.pick = AsyncMock(return_value=StepToGemini("test"))
        use_case._step_picker.note = Mock()
        self.prompt_builder.build_action_context.return_value = ({}, "")
        session = _session(HAVE_PLANKS)
        before = replace(_obs(), state={"inventory": {"stick": 2}})
        after = replace(_obs(), state={"inventory": {"stick": 2, "wooden_hoe": 1}})
        self.text_generator.choose_tool.side_effect = [
            ToolChoice("craft", {"item": "wooden_hoe", "intent": "クワを作る"}),
            ToolChoice("wait", {"intent": "待つ"}),
        ]

        await use_case._act_with_tools(session, before)
        assert book.learned == ()  # まだ効いたか分からない
        await use_case._act_with_tools(session, after)
        (move,) = book.learned
        assert move.tool == "craft" and move.args == {"item": "wooden_hoe"}
        # 同じ状況の 2 手目: 覚えた手を Jev に渡す
        assert use_case._step_picker.pick.await_args.kwargs["learned"] == [move]

        # Jev が選んだら、その道具をそのまま実行し、効いたか数える
        call = ToolCall("craft", {"item": "wooden_hoe"}, intent="クワを作る")
        use_case._step_picker.pick = AsyncMock(
            return_value=PickedStep(call, "learned craft(item=wooden_hoe)", 0.9, learned=move)
        )
        await use_case._act_with_tools(session, after)
        assert watcher.run.await_args.args[0] == call
        await use_case._act_with_tools(session, after)  # 持ち物は変わらなかった
        assert book.learned[0].failed == 1 and book.learned[0].worked == 1

    def test_tools_control_needs_a_watcher(self, bridge):
        with pytest.raises(ValueError, match="needs a tool_watcher"):
            AdvancePlayUseCase(
                bridge=bridge,
                text_generator=AsyncMock(),
                prompt_builder=Mock(),
                action_selector=AsyncMock(),
                event_publisher=AsyncMock(),
                conversation=Conversation(),
                objectives=Mock(),
                town=AsyncMock(),
                notes=Mock(),
                control="tools",
            )


def test_move_furniture_is_an_action_tool_with_an_optional_target():
    specs = {t.name: t for t in tool_specs(groups=["build"])}
    move = specs["move_furniture"].parameters
    assert move["required"] == ["intent", "x", "y", "z"]
    assert {"to_x", "to_y", "to_z"} <= set(move["properties"])


def test_the_tool_prompt_asks_for_another_way_only_while_the_small_goal_keeps_failing():
    from ailoveshen.domain.value_objects import Activity
    from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
        GamePromptTemplateBuilder,
    )

    builder = GamePromptTemplateBuilder()
    state = {"surroundings": {}, "mobs": []}
    assert "行き詰まっている" not in builder.build_tool_prompt(Activity(), state, (), (), failing=1)
    stuck = builder.build_tool_prompt(Activity(), state, (), (), failing=3)
    assert "行き詰まっている（続けて 3 回失敗）" in stuck
    assert "同じ道具を同じ引数" in stuck
    # 状況が動かないときは、その間の手と結果を見せる
    still = builder.build_tool_prompt(
        Activity(), state, (), (), stagnant_seconds=12.4, stagnant_actions=["goto(x=1, y=2, z=3) (1.00) → ok: arrived"]
    )
    assert "12 秒、位置・持ち物・目標の進み具合が変わらない" in still
    assert "- goto(x=1, y=2, z=3) (1.00) → ok: arrived" in still
    assert "行き詰まっている" not in builder.build_tool_prompt(Activity(), state, (), ())


def test_the_tool_prompt_lists_the_moves_the_bridge_refused():
    from ailoveshen.domain.value_objects import Activity
    from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
        GamePromptTemplateBuilder,
    )

    builder = GamePromptTemplateBuilder()
    refusal = {"tool": "dig", "x": -73, "y": 65, "z": -151, "reason": "will not dig oak_log at -73,65,-151: it is part of the current home", "minutes_ago": 2}
    prompt = builder.build_tool_prompt(Activity(), {"surroundings": {}, "mobs": [], "refusals": [refusal]}, (), ())
    assert "しばらく使えない手" in prompt
    assert "- dig (x=-73 y=65 z=-151): will not dig oak_log at -73,65,-151: it is part of the current home（2 分前）" in prompt
    assert "しばらく使えない手" not in builder.build_tool_prompt(Activity(), {"surroundings": {}, "mobs": [], "refusals": []}, (), ())


class TestExperimentTools:
    """行き詰まったときだけ出す実験の道具（設計書 85）。"""

    def test_inspect_and_diagnostics_are_offered_only_when_stuck(self):
        plain = {t.name for t in tool_specs()}
        stuck = {t.name for t in tool_specs(experiment=True)}
        assert {"inspect", "diagnostics"}.isdisjoint(plain)
        assert {"inspect", "diagnostics"} <= stuck
        assert is_query("inspect") and is_query("diagnostics")

    def test_use_item_takes_a_bare_hand_a_face_and_sneak_only_when_stuck(self):
        def use_item(experiment):
            return next(
                t for t in tool_specs(experiment=experiment, groups=["farm"]) if t.name == "use_item"
            )

        plain, stuck = use_item(False), use_item(True)
        assert {"face", "sneak", "target"}.isdisjoint(plain.parameters["properties"])
        assert "item" in plain.parameters["required"]
        assert {"face", "sneak", "target"} <= stuck.parameters["properties"].keys()
        assert "item" not in stuck.parameters["required"]  # 素手で押せる
        assert "intent" in stuck.parameters["required"]

    def test_a_door_click_with_the_home_door_alias_can_be_learned_but_one_with_coordinates_cannot(self):
        from ailoveshen.application.use_cases.learned_candidates import learnable

        alias = ToolCall("use_item", {"target": "home_door", "face": "south", "sneak": False}, intent="x")
        coords = ToolCall("use_item", {"x": 2, "y": 70, "z": 4}, intent="x")
        assert learnable(alias)
        assert not learnable(coords)



class TestToolGroups:
    """毎回渡す道具と、小目標・状態で足す道具の組（設計書 169）。"""

    def test_only_the_core_tools_are_offered_by_default(self):
        names = {t.name for t in tool_specs()}
        assert names == CORE_TOOLS - {"ask_advisor", "look_screen"}
        assert {"till", "build_next", "smelt", "deposit", "recipe_of", "write_skill", "inspect"}.isdisjoint(names)

    def test_a_goal_adds_its_group(self):
        farm = groups_for(GoalSpec(GoalPredicate.FARMED, item="wheat", count=9))
        assert farm == {"farm"}
        assert {t.name for t in tool_specs(groups=farm)} >= {"till", "sow", "plant", "water", "use_item"}
        assert groups_for(GoalSpec(GoalPredicate.FACILITY, name="wheat_farm_1")) == {"farm", "build"}
        assert groups_for(GoalSpec(GoalPredicate.STORED, item="cobblestone", count=64)) == {"store"}
        assert groups_for(GoalSpec(GoalPredicate.HAVE, item="iron_ingot", count=2)) == {"cook"}
        assert groups_for(GoalSpec(GoalPredicate.HAVE, item="oak_log", count=4)) == frozenset()
        assert groups_for(None) == frozenset()

    def test_the_state_adds_its_group(self):
        spec = GoalSpec(GoalPredicate.HAVE, item="oak_log", count=4)
        assert groups_for(spec, {"mobs": [{"hostile": True}]}) == {"fight"}
        assert groups_for(spec, {"mobs": [{"hostile": False}]}) == frozenset()
        assert groups_for(spec, {"self": {"in_water": True}}) == {"water"}
        assert groups_for(spec, {"time": "night"}) == {"night"}
        assert groups_for(spec, {"pickup_ready": True}) == {"store"}
        assert groups_for(spec, {"needs": ["hungry: food 6/20"]}) == {"cook"}

    def test_skill_tools_follow_what_the_skills_are_for(self):
        assert "run_skill" not in {t.name for t in tool_specs()}
        assert "run_skill" in {t.name for t in tool_specs(skills=True)}
        assert "write_skill" not in {t.name for t in tool_specs(skills=True)}  # 書くのは行き詰まり・繰り返しのとき
        assert "write_skill" in {t.name for t in tool_specs(skills=True, write_skill=True)}
