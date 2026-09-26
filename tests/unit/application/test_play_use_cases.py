"""StartPlayUseCase と AdvancePlayUseCase のテスト。"""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.mission_store import SavedPlan
from ailoveshen.application.use_cases.house import HOUSE_SCHEMA, HouseDesigner
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase, StartPlayUseCase
from ailoveshen.domain.entities import Conversation, MidGoalPlan, PlaySession
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalEndedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
)
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    Candidate,
    CharacterProfile,
    ConditionStatus,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    MessageType,
    MidGoal,
    Mission,
    Side,
)

VALID_DESIGN = {
    "name": "ひだまり",
    "concept": "明るい小屋",
    "width": 5,
    "depth": 6,
    "wall_height": 3,
    "door_side": "south",
    "door_offset": 2,
    "corner_pillars": False,
}
PLANKS = {
    "predicate": "have",
    "item": "planks",
    "count": 4,
    "serves": "current",
    "reason": "板材がない",
}
MISSION = Mission("生き延びながら家を建て、街にしていく")


def _blueprint() -> HouseBlueprint:
    return HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2)


def _obs(
    candidates=("dig oak_log at 1,70,2", "wait"),
    remaining=3,
    met=False,
    goal=True,
    **kwargs,
) -> GameObservation:
    return GameObservation(
        state={"self": {"health": 20}},
        candidates=tuple(Candidate(c, {"verb": c.split()[0]}) for c in candidates),
        health=20.0,
        food=20,
        goal=GoalStatus(met=met, remaining=remaining) if goal else None,
        **kwargs,
    )


HAVE_PLANKS = GoalSpec(GoalPredicate.HAVE, item="planks", count=4)
# 失敗の後の決定: 分析、対処、助言（docs/design/27）
RETRY_PLANKS = {
    **PLANKS,
    "diagnosis": "近くに木がない",
    "remedy": "retry",
    "advice": "Explore west.",
}
BUILT = GoalSpec(GoalPredicate.BUILT)
BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
SWORD = GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1)


def _plan(**kwargs) -> MidGoalPlan:
    """家（m1）、次にベッド（m2）。"""
    plan = MidGoalPlan(mission=MISSION, **kwargs)
    plan.add("自分の家を作る", (BUILT,))
    plan.add("夜に寝られるようにする", (BED,))
    return plan


def _session(goal: GoalSpec | None = None, plan: MidGoalPlan | None = None) -> PlaySession:
    session = PlaySession(blueprint=_blueprint(), plan=plan or _plan(), max_stalled_steps=2)
    if goal is not None:
        session.set_goal(Goal(goal, mid_goal_id="m1"), "day")
    return session


def _judged(met: set[GoalSpec] = frozenset()):
    """bridge.check: `met` の条件は満たし、ほかは満たさない。"""

    async def check(specs):
        return [ConditionStatus(s, s in met, (f"{s.describe()}: {s in met}",)) for s in specs]

    return check


@pytest.fixture
def text_generator():
    """構造化出力つきの LLM のモック。"""
    return AsyncMock()


@pytest.fixture
def prompt_builder():
    """ゲームのプロンプトビルダーのモック（同期メソッド）。"""
    builder = Mock()
    builder.build_house_design_prompt.return_value = "design prompt"
    builder.build_goal_prompt.return_value = "goal prompt"
    builder.build_goal_system.return_value = "goal system"
    builder.build_action_context.return_value = ({"goal": "g"}, "instructions")
    return builder


@pytest.fixture
def bridge():
    """Minecraft ブリッジのモック。"""
    b = AsyncMock()
    b.observe.return_value = _obs()
    b.set_goal.return_value = GoalStatus(met=False, remaining=3)
    b.act.side_effect = lambda action_id: ActionResult(action_id, True, "ok", 1.0)
    b.check.side_effect = _judged()
    return b


@pytest.fixture
def store():
    """ミッションストアのモック（まだ何も保存していない）。"""
    s = Mock()
    s.load.return_value = None
    return s


@pytest.fixture
def mid_goals(bridge, events, store):
    """モックの上で動く本物の MidGoalKeeper。"""
    return MidGoalKeeper(bridge=bridge, event_publisher=events, store=store)


@pytest.fixture
def selector():
    """行動選択のモック。"""
    s = AsyncMock()
    s.select.return_value = ActionDecision("dig oak_log at 1,70,2", 0.9)
    return s


@pytest.fixture
def events():
    """イベント発行のモック。"""
    return AsyncMock()


def _published(events, event_type):
    return [c.args[0] for c in events.publish.call_args_list if isinstance(c.args[0], event_type)]


class TestStartPlay:
    """StartPlayUseCase のテスト。"""

    def _use_case(self, text_generator, prompt_builder, bridge, events, store=None, plan=None):
        if store is None:
            store = Mock()
            store.load.return_value = None
        return StartPlayUseCase(
            bridge=bridge,
            event_publisher=events,
            designer=HouseDesigner(text_generator, prompt_builder, CharacterProfile()),
            plan=plan or _plan(),
            store=store,
            town=AsyncMock(),
            notes=Mock(),
            max_steps_per_goal=7,
            max_stalled_steps=5,
        )

    @pytest.mark.asyncio
    async def test_design_is_sent_to_bridge(self, text_generator, prompt_builder, bridge, events):
        """正しい設計ならセッションが始まり、その設計がブリッジに送られる。"""
        text_generator.generate_json.return_value = VALID_DESIGN

        project = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert project.blueprint.name == "ひだまり"
        assert project.blueprint.door_side == Side.SOUTH
        assert project.max_steps_per_goal == 7
        assert project.max_stalled_steps == 5
        assert text_generator.generate_json.call_args.args[1] is HOUSE_SCHEMA
        bridge.set_build_plan.assert_awaited_once_with(project.blueprint)
        assert prompt_builder.build_house_design_prompt.call_args.kwargs["site_note"] == ""
        assert _published(events, HouseDesignedEvent)[0].name == "ひだまり"

    @pytest.mark.asyncio
    async def test_invalid_design_is_retried_with_error(
        self, text_generator, prompt_builder, bridge, events
    ):
        """通らない設計は、検証エラーをつけて差し戻す。"""
        text_generator.generate_json.side_effect = [{**VALID_DESIGN, "width": 9}, VALID_DESIGN]

        project = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert project.blueprint.width == 5
        second = prompt_builder.build_house_design_prompt.call_args_list[1]
        assert "width" in second.kwargs["previous_error"]

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(
        self, text_generator, prompt_builder, bridge, events
    ):
        """不正な設計が続いたら、設計を送らずに TextGenerationError を出す。"""
        text_generator.generate_json.return_value = {"name": "x"}

        with pytest.raises(TextGenerationError, match="no valid house design"):
            await self._use_case(text_generator, prompt_builder, bridge, events).execute()
        bridge.set_build_plan.assert_not_called()

    @pytest.mark.asyncio
    async def test_built_home_is_used_without_designing(
        self, text_generator, prompt_builder, bridge, events
    ):
        """前の実行で建てた家は残す。設計もせず、建てる計画も送らない。"""
        bridge.observe.return_value = _obs(has_plan=True, house_complete=True, has_home=True)

        session = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert session.blueprint is None  # 設計は残っていない（設計を保存する前に建てた家）
        assert session.completion_announced
        text_generator.generate_json.assert_not_called()
        bridge.set_build_plan.assert_not_called()
        assert _published(events, HouseDesignedEvent) == []

    @pytest.mark.asyncio
    async def test_built_home_keeps_its_design(
        self, text_generator, prompt_builder, bridge, events
    ):
        """ブリッジが持っている家の設計が戻る（名前もなくならない）。"""
        home = _obs(has_plan=True, house_complete=True, has_home=True)
        home.state["home"] = {"name": "ひだまり", "design": VALID_DESIGN}
        bridge.observe.return_value = home

        session = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert session.blueprint.name == "ひだまり"
        assert session.blueprint.door_side == Side.SOUTH
        text_generator.generate_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_restart_during_the_move_keeps_the_new_house(
        self, text_generator, prompt_builder, bridge, events
    ):
        """引っ越しの途中の再起動: 建てている引っ越し先の家の設計で続け、完成はまだ言っていない。"""
        moving = _obs(has_plan=True, house_complete=False, has_home=True)
        moving.state["home"] = {"name": "小屋", "design": {**VALID_DESIGN, "name": "小屋"}}
        moving.state["build"] = {"design": {**VALID_DESIGN, "name": "石の家"}, "complete": False}
        bridge.observe.return_value = moving

        session = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert session.blueprint.name == "石の家"
        assert not session.completion_announced
        bridge.set_build_plan.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_plan_comes_from_the_configuration_and_is_saved(
        self, text_generator, prompt_builder, bridge, events, store
    ):
        """何も保存していなければ、設定の中目標から始める（保存もする）。"""
        text_generator.generate_json.return_value = VALID_DESIGN
        plan = _plan()

        session = await self._use_case(
            text_generator, prompt_builder, bridge, events, store, plan
        ).execute()

        assert session.plan is plan
        assert [g.id for g in session.plan.pending] == ["m1", "m2"]
        store.save.assert_called_once_with(plan)

    @pytest.mark.asyncio
    async def test_saved_plan_carries_on_for_the_same_mission(
        self, text_generator, prompt_builder, bridge, events, store
    ):
        """中目標は再起動をまたいで続く。"""
        text_generator.generate_json.return_value = VALID_DESIGN
        store.load.return_value = SavedPlan(
            mission=MISSION,
            pending=(MidGoal("m7", "剣", (SWORD,), requested_by="neko", steps=5),),
            finished=(),
            next_id=8,
        )

        session = await self._use_case(
            text_generator, prompt_builder, bridge, events, store
        ).execute()

        assert [(g.id, g.requested_by, g.steps) for g in session.plan.pending] == [
            ("m7", "neko", 5)
        ]
        assert session.plan.next_id == 8

    @pytest.mark.asyncio
    async def test_saved_plan_for_another_mission_is_not_used(
        self, text_generator, prompt_builder, bridge, events, store
    ):
        """大目標が変わったら、設定の中目標から始め直す。"""
        text_generator.generate_json.return_value = VALID_DESIGN
        store.load.return_value = SavedPlan(
            mission=Mission("村を作る"),
            pending=(MidGoal("m7", "剣", (SWORD,)),),
            finished=(),
            next_id=8,
        )

        session = await self._use_case(
            text_generator, prompt_builder, bridge, events, store
        ).execute()

        assert [g.id for g in session.plan.pending] == ["m1", "m2"]
        store.save.assert_called_once()


class TestAdvancePlay:
    """AdvancePlayUseCase のテスト。"""

    @pytest.fixture
    def conversation(self):
        return Conversation()

    @pytest.fixture
    def town(self):
        """街の準備のモック（ここでは何もしない）。"""
        return AsyncMock()

    @pytest.fixture
    def note_store(self):
        """メモの保存のモック（保存したものはない）。"""
        store = Mock()
        store.load.return_value = None
        return store

    @pytest.fixture
    def notes(self, note_store):
        return NoteKeeper(note_store)

    @pytest.fixture
    def use_case(
        self,
        bridge,
        text_generator,
        prompt_builder,
        selector,
        events,
        conversation,
        mid_goals,
        town,
        notes,
    ):
        return AdvancePlayUseCase(
            bridge=bridge,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            action_selector=selector,
            event_publisher=events,
            conversation=conversation,
            mid_goals=mid_goals,
            town=town,
            notes=notes,
        )

    @pytest.mark.asyncio
    async def test_the_chests_keep_what_the_mid_goals_store(self, use_case, text_generator, bridge):
        """蓄える中目標の stored() の条件は小目標と一緒に送り、チェストから取り出させない。"""
        text_generator.generate_json.return_value = PLANKS
        plan = _plan()
        stock = GoalSpec(GoalPredicate.STORED, item="food", count=10)
        plan.add("食料を蓄える", (stock,))

        await use_case.execute(_session(plan=plan))

        bridge.set_goal.assert_awaited_once_with(HAVE_PLANKS, (stock,))

    @pytest.mark.asyncio
    async def test_the_town_advances_after_the_judgement_before_the_decision(
        self, use_case, text_generator, bridge, town
    ):
        """済んだ中目標（調査）を判定してから街の準備を進め、そのあとの見え方で決める。"""
        text_generator.generate_json.return_value = PLANKS
        session = _session()
        order = []

        async def check(specs):
            order.append("judge")
            return await _judged()(specs)

        bridge.check.side_effect = check
        town.advance.side_effect = lambda s, obs: order.append("town")

        await use_case.execute(session)

        town.advance.assert_awaited_once()
        assert town.advance.call_args.args[0] is session
        assert order.index("town") > order.index("judge")

    @pytest.mark.asyncio
    async def test_first_step_sets_goal_then_acts(
        self, use_case, text_generator, bridge, selector, prompt_builder, events
    ):
        """LLM が小目標を決め、ブリッジが受け、行動選択が選び、ブリッジが実行する。"""
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        report = await use_case.execute(session)

        assert report.goal_changed
        assert session.goal.spec == HAVE_PLANKS
        bridge.set_goal.assert_awaited_once_with(HAVE_PLANKS, ())
        assert bridge.observe.await_count == 2  # 小目標を決めたあと、もう一度: その小目標の候補
        state, candidates, instructions = selector.select.call_args.args
        assert state == {"goal": "g"} and instructions == "instructions"
        assert [c.action_id for c in candidates] == ["dig oak_log at 1,70,2", "wait"]
        prompt_builder.build_action_context.assert_called_once()
        bridge.act.assert_awaited_once_with("dig oak_log at 1,70,2")
        assert session.steps_in_goal == 1
        assert _published(events, GoalSetEvent)[0].goal == "have(planks, 4)"
        assert _published(events, GameActionExecutedEvent)[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_predicates_offered_follow_the_house(self, use_case, text_generator, bridge):
        """家・拠点・チェスト・明かりの小目標は、当てはまるときだけ出す。"""
        text_generator.generate_json.return_value = PLANKS

        await use_case.execute(_session())
        schema = text_generator.generate_json.call_args.args[1]
        assert schema["properties"]["predicate"]["enum"] == ["have", "explored"]

        bridge.observe.return_value = _obs(goal=False, has_plan=True)
        await use_case.execute(_session())
        schema = text_generator.generate_json.call_args.args[1]
        assert schema["properties"]["predicate"]["enum"] == ["built", "have", "explored"]

        bridge.observe.return_value = _obs(
            goal=False, has_plan=True, house_complete=True, has_home=True
        )
        await use_case.execute(_session())
        schema = text_generator.generate_json.call_args.args[1]
        assert schema["properties"]["predicate"]["enum"] == [
            "have",
            "at_home",
            "through_night",
            "cleared",
            "placed",
            "stored",
            "lit",
            "planted",
            "farmed",
            "explored",
        ]

        # 入口の掃除は昼の小目標。夜は湧き続ける
        bridge.observe.return_value = _obs(
            goal=False, has_plan=True, house_complete=True, has_home=True, time_phase="night"
        )
        await use_case.execute(_session())
        schema = text_generator.generate_json.call_args.args[1]
        assert "cleared" not in schema["properties"]["predicate"]["enum"]

    @pytest.mark.asyncio
    async def test_goal_rejected_by_bridge_is_retried_with_reason(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """ブリッジが断った理由は LLM に返す。"""
        text_generator.generate_json.side_effect = [
            {**PLANKS, "item": "unobtainium", "count": 1},
            PLANKS,
        ]
        bridge.set_goal.side_effect = [
            GoalRejectedError("unknown item: unobtainium"),
            GoalStatus(False, 3),
        ]
        session = _session()

        await use_case.execute(session)

        assert session.goal.spec == HAVE_PLANKS
        second = prompt_builder.build_goal_prompt.call_args_list[1]
        assert "unobtainium" in second.kwargs["previous_error"]

    @pytest.mark.asyncio
    async def test_malformed_or_unoffered_goal_is_retried(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """引数の足りない小目標や、出していない小目標は差し戻す。"""
        text_generator.generate_json.side_effect = [
            {"predicate": "have", "item": "planks", "serves": "current", "reason": ""},
            {"predicate": "at_home", "serves": "survival", "reason": ""},
            PLANKS,
        ]

        session = _session()
        await use_case.execute(session)

        errors = [
            c.kwargs["previous_error"] for c in prompt_builder.build_goal_prompt.call_args_list
        ]
        assert "count" in errors[1]
        assert "not one of the goals offered" in errors[2]
        bridge.set_goal.assert_awaited_once_with(HAVE_PLANKS, ())

    @pytest.mark.asyncio
    async def test_gives_up_after_max_goal_attempts(self, use_case, text_generator, bridge):
        """断られ続けたら、行動せずに TextGenerationError を出す。"""
        text_generator.generate_json.return_value = {
            "predicate": "have",
            "serves": "current",
            "reason": "",
        }

        with pytest.raises(TextGenerationError, match="no acceptable goal"):
            await use_case.execute(_session())
        bridge.act.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_candidate_skips_selector(self, use_case, selector, bridge):
        """候補が 1 つだけなら、モデルを呼ばずにそれを選ぶ。"""
        bridge.observe.return_value = _obs(("wait inside",))

        report = await use_case.execute(_session(HAVE_PLANKS))

        selector.select.assert_not_called()
        assert report.decision == ActionDecision("wait inside", 1.0)
        assert not report.goal_changed

    @pytest.mark.asyncio
    async def test_met_goal_is_replaced_and_remembered(
        self, use_case, text_generator, bridge, prompt_builder, events
    ):
        """達成した小目標は終わる（実況し、覚える）。LLM は終わった理由とともに見る。"""
        bridge.observe.return_value = _obs(met=True, remaining=0)
        text_generator.generate_json.return_value = {
            "predicate": "explored",
            "distance": 30,
            "serves": "current",
            "reason": "",
        }
        session = _session(HAVE_PLANKS)

        report = await use_case.execute(session)

        assert report.goal_changed
        assert session.goal.spec.predicate == GoalPredicate.EXPLORED
        kwargs = prompt_builder.build_goal_prompt.call_args.kwargs
        assert kwargs["goal_ended_because"] == "goal have(planks, 4) is met"
        assert kwargs["activity"].goal.spec == HAVE_PLANKS
        assert session.recent_goals[0].goal.spec == HAVE_PLANKS
        assert session.recent_goals[0].met
        ended = _published(events, GoalEndedEvent)[0]
        assert (ended.goal, ended.met) == ("have(planks, 4)", True)

    @pytest.mark.asyncio
    async def test_stalled_goal_is_replaced(self, use_case, text_generator, bridge, prompt_builder):
        """残りの作業が減らなくなった小目標は stalled で終わる。"""
        text_generator.generate_json.return_value = RETRY_PLANKS
        session = _session(HAVE_PLANKS)  # max_stalled_steps=2。残りは 3 のまま

        assert not (await use_case.execute(session)).goal_changed  # 作業量の最初の読み取り
        assert not (await use_case.execute(session)).goal_changed  # 進まない: 1
        report = await use_case.execute(session)  # 進まない: 2

        assert report.goal_changed
        reason = prompt_builder.build_goal_prompt.call_args.kwargs["goal_ended_because"]
        assert reason == "goal have(planks, 4) stalled (no progress in 2 steps)"

    @pytest.mark.asyncio
    async def test_completion_is_announced_once_and_play_goes_on(self, use_case, bridge, events):
        """完了したらイベントを 1 つ発行し、セッションは手を進め続ける。"""
        bridge.observe.return_value = _obs(has_plan=True, house_complete=True)
        session = _session(HAVE_PLANKS)

        first = await use_case.execute(session)
        await use_case.execute(session)

        assert first.house_complete
        assert len(_published(events, HouseCompletedEvent)) == 1
        assert bridge.act.await_count == 2

    @pytest.mark.asyncio
    async def test_busy_bridge_skips_the_step(self, use_case, text_generator, bridge):
        """ブリッジの反射が動いている間は、何も決めず何も実行しない。"""
        bridge.observe.return_value = replace(_obs(), busy=True)

        report = await use_case.execute(_session())

        assert report.waiting
        text_generator.generate_json.assert_not_called()
        bridge.act.assert_not_called()

    @pytest.mark.asyncio
    async def test_goal_decision_sees_what_was_said(
        self, use_case, text_generator, prompt_builder, conversation
    ):
        """小目標の決定に会話（配信者が言ったこと、頼まれたこと）が渡る。"""
        conversation.add_viewer_message("家まだ？", "neko")
        conversation.add_streamer_message("もうすぐ完成するよ", MessageType.RESPONSE)
        text_generator.generate_json.return_value = PLANKS

        await use_case.execute(_session())

        messages = prompt_builder.build_goal_prompt.call_args.kwargs["recent_messages"]
        assert [m.content for m in messages] == ["家まだ？", "もうすぐ完成するよ"]

    @pytest.mark.asyncio
    async def test_goal_decision_sees_only_the_last_commentary(
        self, use_case, text_generator, prompt_builder, conversation
    ):
        """実況は直近の 3 件だけ。チャットと返答は残す（26 §4）。"""
        conversation.add_viewer_message("家まだ？", "neko")
        for i in range(5):
            conversation.add_streamer_message(f"実況{i}", MessageType.COMMENTARY)
        text_generator.generate_json.return_value = PLANKS

        await use_case.execute(_session())

        messages = prompt_builder.build_goal_prompt.call_args.kwargs["recent_messages"]
        assert [m.content for m in messages] == ["家まだ？", "実況2", "実況3", "実況4"]
        kwargs = text_generator.generate_json.call_args.kwargs
        assert kwargs["system_instruction"] == "goal system"

    @pytest.mark.asyncio
    async def test_small_goal_serves_the_top_mid_goal(self, use_case, text_generator, events):
        """小目標は今取り組む中目標のために決まり、そのことを示す。"""
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        await use_case.execute(session)

        assert session.goal.mid_goal_id == "m1"
        assert _published(events, GoalSetEvent)[0].mid_goal == "自分の家を作る"

    @pytest.mark.asyncio
    async def test_new_goal_is_seen_with_its_own_status(self, use_case, text_generator, bridge):
        """小目標の決定を語る実況が見るのは、終わった小目標でなく新しい小目標の状態。"""
        bridge.set_goal.return_value = GoalStatus(False, 5, lines=("have 4 planks (0/4)",))
        seen = []

        async def publish(event):
            if isinstance(event, GoalSetEvent):
                seen.append(session.activity().observation.goal.lines)

        use_case._event_publisher.publish.side_effect = publish
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        await use_case.execute(session)

        assert seen == [("have 4 planks (0/4)",)]

    @pytest.mark.asyncio
    async def test_mid_goals_are_judged_before_deciding(
        self, use_case, text_generator, bridge, prompt_builder, events, store
    ):
        """もう済んでいる中目標（前の実行で建てた家）は、最初に完了にする。"""
        bridge.check.side_effect = _judged({BUILT})
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        await use_case.execute(session)

        assert [g.id for g in session.plan.pending] == ["m2"]
        assert _published(events, MidGoalCompletedEvent)[0].title == "自分の家を作る"
        activity = prompt_builder.build_goal_prompt.call_args.kwargs["activity"]
        assert [g.id for g in activity.mid_goals if g.state.value == "pending"] == ["m2"]
        assert session.goal.mid_goal_id == "m2"
        assert session.plan.get("m2").progress == ("placed(bed, home): False",)
        store.save.assert_called()

    @pytest.mark.asyncio
    async def test_survival_goal_serves_no_mid_goal(self, use_case, text_generator, events):
        """生存のための小目標は、どの中目標のためでもない。"""
        text_generator.generate_json.return_value = {
            "predicate": "have",
            "item": "food",
            "count": 4,
            "serves": "survival",
            "reason": "お腹が空いた",
        }
        session = _session()

        await use_case.execute(session)

        assert session.goal.mid_goal_id is None
        assert _published(events, GoalSetEvent)[0].mid_goal == ""

    @pytest.mark.asyncio
    async def test_only_survival_goals_may_skip_the_mid_goals(
        self, use_case, text_generator, prompt_builder
    ):
        """生存に関係ない小目標は、一番上の中目標のためでなければならない。"""
        text_generator.generate_json.side_effect = [{**PLANKS, "serves": "survival"}, PLANKS]
        session = _session()

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "not a survival goal" in error
        assert session.goal.mid_goal_id == "m1"

    @pytest.mark.asyncio
    async def test_no_mid_goal_left_needs_one_added(self, use_case, text_generator, prompt_builder):
        """リストが空なら、小目標は中目標を足すか、生存のためのものでなければならない。"""
        text_generator.generate_json.side_effect = [
            PLANKS,
            {
                **PLANKS,
                "plan_changes": [
                    {
                        "op": "add",
                        "title": "剣を持つ",
                        "conditions": [{"predicate": "have", "item": "wooden_sword", "count": 1}],
                        "reason": "身を守る",
                    }
                ],
            },
        ]
        session = _session(plan=MidGoalPlan(mission=MISSION))

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "no mid goal left" in error
        assert session.plan.current.title == "剣を持つ"
        assert session.goal.mid_goal_id == session.plan.current.id

    @pytest.mark.asyncio
    async def test_plan_changes_are_applied_with_the_goal(
        self, use_case, text_generator, prompt_builder, events, store
    ):
        """配信者自身の編集: 足す（言う）、動かす、そして小目標は一番上のために。"""
        text_generator.generate_json.return_value = {
            **PLANKS,
            "plan_changes": [
                {
                    "op": "add",
                    "title": "剣を持つ",
                    "conditions": [{"predicate": "have", "item": "wooden_sword", "count": 1}],
                    "position": 3,
                    "reason": "夜に備える",
                },
                {"op": "move", "id": "m2", "position": 1, "reason": "夜が近い"},
            ],
        }
        session = _session()

        await use_case.execute(session)

        assert [g.title for g in session.plan.pending] == [
            "夜に寝られるようにする",
            "自分の家を作る",
            "剣を持つ",
        ]
        assert session.goal.mid_goal_id == "m2"
        added = _published(events, MidGoalAddedEvent)[0]
        assert (added.title, added.position, added.requested_by) == ("剣を持つ", 3, "")
        schema = text_generator.generate_json.call_args.args[1]
        change = schema["properties"]["plan_changes"]["items"]
        assert change["properties"]["id"]["enum"] == ["m1", "m2"]
        store.save.assert_called_with(session.plan)

    @pytest.mark.asyncio
    async def test_unusable_edit_is_retried_and_nothing_is_applied(
        self, use_case, text_generator, prompt_builder, bridge
    ):
        """規則を破る編集は理由をつけて差し戻し、計画は変えない。"""
        text_generator.generate_json.side_effect = [
            {
                **PLANKS,
                "plan_changes": [
                    {"op": "move", "id": "m2", "position": 1, "reason": "先に"},
                    {"op": "drop", "id": "m1", "reason": ""},
                ],
            },
            PLANKS,
        ]
        session = _session()

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "needs a reason" in error
        assert [g.id for g in session.plan.pending] == ["m1", "m2"]

    @pytest.mark.asyncio
    async def test_new_mid_goal_the_bridge_cannot_judge_is_retried(
        self, use_case, text_generator, prompt_builder, bridge
    ):
        """足す中目標の条件は、先にブリッジが確かめる。"""
        judged = _judged()

        async def check(specs):
            if any(s.item == "diamond" for s in specs):
                raise GoalRejectedError("conditions rejected: unknown item diamond")
            return await judged(specs)

        bridge.check.side_effect = check
        add_diamond = {
            "op": "add",
            "title": "ダイヤ",
            "conditions": [{"predicate": "have", "item": "diamond", "count": 1}],
            "reason": "欲しい",
        }
        text_generator.generate_json.side_effect = [
            {**PLANKS, "plan_changes": [add_diamond]},
            PLANKS,
        ]
        session = _session()

        await use_case.execute(session)

        assert (
            "unknown item diamond"
            in (prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"])
        )
        assert len(session.plan.pending) == 2

    @pytest.mark.asyncio
    async def test_request_accepted_during_a_goal_decision_is_kept(self, use_case, text_generator):
        """LLM が決めている間に足された視聴者の中目標は、LLM の編集で上書きされない。"""
        session = _session()

        async def decide(*args, **kwargs):
            # その間にコメントへの返答が頼みを受ける
            session.plan.add("剣", (SWORD,), requested_by="neko", position=1)
            return {
                **PLANKS,
                "plan_changes": [
                    {
                        "op": "add",
                        "title": "食料",
                        "conditions": [{"predicate": "have", "item": "food", "count": 8}],
                        "reason": "空腹",
                    }
                ],
            }

        text_generator.generate_json.side_effect = decide

        await use_case.execute(session)

        assert [g.title for g in session.plan.pending] == [
            "自分の家を作る",
            "剣",
            "夜に寝られるようにする",
            "食料",
        ]

    @pytest.mark.asyncio
    async def test_viewer_mid_goal_over_budget_is_dropped_and_told(
        self, use_case, text_generator, bridge, events, store
    ):
        """ステップの予算を超えた頼みはやめ（言う）、その小目標は次のステップで終わる。"""
        plan = MidGoalPlan(mission=MISSION, viewer_budget=1)
        request = plan.add("剣", (SWORD,), requested_by="neko")
        session = PlaySession(blueprint=_blueprint(), plan=plan)
        session.set_goal(Goal(HAVE_PLANKS, mid_goal_id=request.id), "day")

        await use_case.execute(session)

        dropped = _published(events, MidGoalDroppedEvent)[0]
        assert (dropped.title, dropped.requested_by) == ("剣", "neko")
        assert "over the budget" in dropped.reason
        assert "served has ended" in session.goal_end_reason(bridge.observe.return_value)
        store.save.assert_called_with(plan)


LOG = GoalSpec(GoalPredicate.HAVE, item="log", count=12)
STEPS_DECISION = {
    **PLANKS,
    "steps": [
        {"predicate": "have", "item": "log", "count": 12, "reason": "原木を集める"},
        {"predicate": "have", "item": "planks", "count": 4, "reason": "板材にする"},
        {"predicate": "built", "reason": "家を建てる"},
    ],
}


class FakeJev:
    """IFastJudge の代わり: 決めた選択肢（A, B, ...）を決めた確信度で返す。"""

    def __init__(self, choice="A", confidence=0.9):
        self.choice, self.confidence = choice, confidence
        self.asked = []

    async def ask(self, state, questions):
        from ailoveshen.domain.value_objects import FastAnswer, FastVerdict

        self.asked.append((state, questions))
        return FastVerdict({"next_goal": FastAnswer("next_goal", self.choice, self.confidence)}, 5)

    async def close(self):
        pass


class TestStepsChosenByJev:
    """小目標は Gemini が書いた手順から Jev が選ぶ（docs/design/26）。"""

    @pytest.fixture
    def make(self, bridge, text_generator, prompt_builder, selector, events, mid_goals):
        from ailoveshen.application.use_cases.goal_chooser import GoalChooser

        def make(jev=None, clock=lambda: 0.0):
            notes_store = Mock()
            notes_store.load.return_value = None
            chooser = GoalChooser(jev or FakeJev(), bridge) if jev is not False else None
            return AdvancePlayUseCase(
                bridge=bridge,
                text_generator=text_generator,
                prompt_builder=prompt_builder,
                action_selector=selector,
                event_publisher=events,
                conversation=Conversation(),
                mid_goals=mid_goals,
                town=AsyncMock(),
                notes=NoteKeeper(notes_store),
                chooser=chooser,
                clock=clock,
            )

        return make

    @pytest.mark.asyncio
    async def test_gemini_writes_the_steps_once_then_jev_picks_the_next(
        self, make, text_generator, bridge
    ):
        text_generator.generate_json.return_value = STEPS_DECISION
        use_case = make(FakeJev("B"))
        session = _session()

        await use_case.execute(session)  # 手順がない: Gemini が書く
        assert [s.spec for s in session.plan.current.plan_steps] == [LOG, HAVE_PLANKS, BUILT]
        assert text_generator.generate_json.await_count == 1

        bridge.observe.return_value = _obs(met=True)  # 小目標が済んだ → Jev
        bridge.check.side_effect = _judged({LOG})  # 原木は済んでいる
        await use_case.execute(session)
        assert text_generator.generate_json.await_count == 1  # Gemini は呼ばない
        # 済んだ手順は出さない: A=板材、B=建てる
        assert session.goal.spec == BUILT
        assert session.goal.mid_goal_id == "m1"
        assert session.goal.reason == "家を建てる"

    @pytest.mark.asyncio
    async def test_gemini_must_write_steps_when_the_top_has_none(
        self, make, text_generator, prompt_builder
    ):
        text_generator.generate_json.side_effect = [PLANKS, STEPS_DECISION]
        await make().execute(_session())
        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "write the steps" in error

    @pytest.mark.asyncio
    async def test_failure_low_confidence_and_used_up_steps_go_to_gemini(
        self, make, text_generator, bridge
    ):
        text_generator.generate_json.return_value = STEPS_DECISION
        session = _session()
        use_case = make(FakeJev("A", confidence=0.1))
        await use_case.execute(session)
        bridge.observe.return_value = _obs(met=True)
        await use_case.execute(session)  # 自信がない → Gemini
        assert text_generator.generate_json.await_count == 2

        confident = make(FakeJev("A"))
        bridge.check.side_effect = _judged({LOG, HAVE_PLANKS, BUILT})  # 手順は全部済んだ
        await confident.execute(session)
        assert text_generator.generate_json.await_count == 3

    @pytest.mark.asyncio
    async def test_each_gemini_decision_reviews_the_steps_as_they_are_now(
        self, make, text_generator, bridge, prompt_builder
    ):
        """Gemini が決めるたびに、手順の今の判定を見せ、見直し（review）を毎回書かせる（31）。"""
        text_generator.generate_json.return_value = {
            **STEPS_DECISION,
            "review": "原木はもう 12 本あるので板材から",
        }
        session = _session()
        use_case = make(FakeJev("A", confidence=0.1))
        await use_case.execute(session)  # 手順がない: 判定するものもない
        assert prompt_builder.build_goal_prompt.call_args.kwargs["step_status"] == ()

        bridge.observe.return_value = _obs(met=True)
        bridge.check.side_effect = _judged({LOG})
        await use_case.execute(session)  # 自信がない → Gemini。手順の今の判定を見せる

        status = prompt_builder.build_goal_prompt.call_args.kwargs["step_status"]
        assert [(st.spec, st.met) for st in status] == [
            (LOG, True),
            (HAVE_PLANKS, False),
            (BUILT, False),
        ]
        schema = text_generator.generate_json.call_args.args[1]
        assert list(schema["properties"])[0] == "review"
        assert "review" in schema["required"]
        assert session.goal.review == "原木はもう 12 本あるので板材から"

    def test_which_boundaries_go_to_gemini(self, make):
        from ailoveshen.domain.value_objects import PlannedStep

        session = _session()
        assert "no steps yet" in make()._needs_gemini(session, "goal have(log, 12) is met")
        session.plan.set_steps("m1", (PlannedStep(LOG, "原木"),))
        assert make()._needs_gemini(session, "goal have(log, 12) is met") is None
        assert make()._needs_gemini(session, "the time of day changed from day to dusk") is None
        assert "did not work out" in make()._needs_gemini(session, "goal x is stuck (actions)")
        assert "did not work out" in make()._needs_gemini(session, "goal x is reconsidered: y")
        assert "decided by Gemini" in make(jev=False)._needs_gemini(session, "goal x is met")
        # 定期の見直し: 中目標が変わっていなければ 3 倍まで待つ
        now = [0.0]
        use_case = make(clock=lambda: now[0])
        use_case._last_gemini_at = 0.0
        use_case._last_plan = tuple(g.id for g in session.plan.pending)
        now[0] = 21 * 60
        assert use_case._needs_gemini(session, "goal x is met") is None
        now[0] = 61 * 60
        assert "periodic" in use_case._needs_gemini(session, "goal x is met")
        use_case._last_plan = ()
        now[0] = 21 * 60
        assert "periodic" in use_case._needs_gemini(session, "goal x is met")

    @pytest.mark.asyncio
    async def test_at_night_the_survival_goal_serves_no_mid_goal(
        self, make, text_generator, bridge
    ):
        from ailoveshen.application.use_cases.goal_chooser import GoalChooser

        text_generator.generate_json.return_value = STEPS_DECISION
        session = _session()
        await make().execute(session)
        night = _obs(met=True, time_phase="night", has_home=True, inside_home=True)
        chooser = GoalChooser(FakeJev("D"), bridge)  # A 原木 B 板材 C 建てる D 夜を越す
        picked = await chooser.choose(session, night, "goal x is met")
        assert picked.spec == GoalSpec(GoalPredicate.THROUGH_NIGHT)
        assert picked.mid_goal_id is None

    @pytest.mark.asyncio
    async def test_a_survival_goal_already_met_is_not_offered(self, make, text_generator, bridge):
        """夕方にもう家の中なら at_home は出さない（選ぶとすぐ済んで、また選ぶことになる）。"""
        from ailoveshen.application.use_cases.goal_chooser import GoalChooser

        text_generator.generate_json.return_value = STEPS_DECISION
        session = _session()
        await make().execute(session)
        dusk = _obs(met=True, time_phase="dusk", has_home=True, inside_home=True)
        bridge.check.side_effect = _judged({LOG, HAVE_PLANKS, GoalSpec(GoalPredicate.AT_HOME)})
        jev = FakeJev("A")
        picked = await GoalChooser(jev, bridge).choose(session, dusk, "goal x is met")
        # 残りは「建てる」だけ: Jev を呼ばずにそれ
        assert picked.spec == BUILT
        assert jev.asked == []


def test_steps_must_be_conditions_the_world_can_judge():
    from ailoveshen.application.use_cases.goal_vocabulary import parse_decision

    decision = parse_decision(STEPS_DECISION)
    assert [s.reason for s in decision.steps] == ["原木を集める", "板材にする", "家を建てる"]
    with pytest.raises(ValueError, match="step 1: explored cannot be a step"):
        parse_decision({**PLANKS, "steps": [{"predicate": "explored", "distance": 30}]})


class TestFailureDiagnosis:
    """進まないときは原因を分析してから、必要なら目標を変える（docs/design/27）。"""

    conversation = TestAdvancePlay.conversation
    town = TestAdvancePlay.town
    note_store = TestAdvancePlay.note_store
    notes = TestAdvancePlay.notes
    use_case = TestAdvancePlay.use_case

    async def _stall(self, use_case, session):
        # max_stalled_steps=2: 最初の読み取り、進まない 1、進まない 2 で終わる
        for _ in range(3):
            report = await use_case.execute(session)
        return report

    @pytest.mark.asyncio
    async def test_the_record_and_options_go_to_gemini_with_a_diagnosis_first(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        bridge.observe.return_value = _obs(
            candidates=("go to pig seen at -21,-267", "explore east")
        )
        text_generator.generate_json.return_value = RETRY_PLANKS
        session = _session(HAVE_PLANKS)

        assert (await self._stall(use_case, session)).goal_changed

        kwargs = prompt_builder.build_goal_prompt.call_args.kwargs
        assert len(kwargs["failure_record"]) == 2  # この小目標の間の行動
        assert "(0.90) → ok" in kwargs["failure_record"][0]
        assert [c.action_id for c in kwargs["offered"]][0] == "go to pig seen at -21,-267"
        assert kwargs["retry_allowed"]
        schema = text_generator.generate_json.call_args.args[1]
        # 分析してから、今の状態で見直す（31）
        assert list(schema["properties"])[:4] == ["diagnosis", "remedy", "advice", "review"]
        assert session.goal.diagnosis == "近くに木がない"
        assert session.goal.advice == "Explore west."

    @pytest.mark.asyncio
    async def test_a_goal_that_is_met_is_not_diagnosed(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        text_generator.generate_json.return_value = PLANKS
        bridge.observe.return_value = _obs(met=True)
        await use_case.execute(_session(HAVE_PLANKS))
        kwargs = prompt_builder.build_goal_prompt.call_args.kwargs
        assert kwargs["failure_record"] == ()
        schema = text_generator.generate_json.call_args.args[1]
        assert "diagnosis" not in schema["properties"]

    @pytest.mark.asyncio
    async def test_retry_needs_advice_and_change_needs_another_goal(
        self, use_case, text_generator, prompt_builder
    ):
        no_advice = {**RETRY_PLANKS, "advice": ""}
        # 数だけ変えても同じ小目標: change とは言えない
        disguised = {**RETRY_PLANKS, "remedy": "change", "count": 2}
        text_generator.generate_json.side_effect = [no_advice, disguised, RETRY_PLANKS]

        await self._stall(use_case, _session(HAVE_PLANKS))

        errors = [
            c.kwargs["previous_error"] for c in prompt_builder.build_goal_prompt.call_args_list
        ]
        assert "retry needs advice" in errors[1]
        assert "only the number differs" in errors[2]

    @pytest.mark.asyncio
    async def test_the_same_goal_failing_twice_must_change(
        self, use_case, text_generator, prompt_builder
    ):
        text_generator.generate_json.side_effect = [
            RETRY_PLANKS,  # 1 回目の失敗の後: やり直す
            RETRY_PLANKS,  # 2 回目の失敗の後: やり直しは選べない
            {
                **PLANKS,
                "predicate": "explored",
                "distance": 40,
                "serves": "current",
                "diagnosis": "木がない",
                "remedy": "change",
            },
        ]
        session = _session(HAVE_PLANKS)
        await self._stall(use_case, session)
        await self._stall(use_case, session)

        kwargs = prompt_builder.build_goal_prompt.call_args.kwargs
        assert not kwargs["retry_allowed"]
        assert "2 times in a row" in kwargs["previous_error"]
        schema = text_generator.generate_json.call_args.args[1]
        assert schema["properties"]["remedy"]["enum"] == ["change"]
        assert session.goal.spec.predicate == GoalPredicate.EXPLORED


def test_same_kind_ignores_the_numbers():
    food = GoalSpec(GoalPredicate.HAVE, item="food", count=2)
    assert food.same_kind(GoalSpec(GoalPredicate.HAVE, item="food", count=1))
    assert not food.same_kind(GoalSpec(GoalPredicate.HAVE, item="log", count=2))


class TestDeath:
    """死んでリスポーンしたら、小目標を必ず選び直す（場所も持ち物も変わる）。"""

    conversation = TestAdvancePlay.conversation
    town = TestAdvancePlay.town
    note_store = TestAdvancePlay.note_store
    notes = TestAdvancePlay.notes
    use_case = TestAdvancePlay.use_case

    @staticmethod
    def _with_deaths(n, **kwargs):
        obs = _obs(**kwargs)
        return replace(obs, state={**obs.state, "deaths": n})

    def test_a_death_cuts_the_goal_even_if_it_looks_met(self):
        session = _session()
        session.observe(self._with_deaths(0))
        session.set_goal(Goal(HAVE_PLANKS, mid_goal_id="m1"), "day")
        assert session.goal_end_reason(self._with_deaths(0)) == ""
        reason = session.goal_end_reason(self._with_deaths(1, met=True))
        assert "died and respawned" in reason
        # ブリッジを再起動すると数は 0 に戻る: 死んだとはみなさない
        assert session.goal_end_reason(self._with_deaths(0)) == ""

    @pytest.mark.asyncio
    async def test_after_a_death_gemini_decides_with_the_screen(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        text_generator.generate_json.return_value = PLANKS
        bridge.observe.return_value = self._with_deaths(0)
        session = _session(HAVE_PLANKS)
        session.observe(self._with_deaths(0))
        session.set_goal(Goal(HAVE_PLANKS, mid_goal_id="m1"), "day")

        bridge.observe.return_value = self._with_deaths(1)
        report = await use_case.execute(session)

        assert report.goal_changed
        reason = prompt_builder.build_goal_prompt.call_args.kwargs["goal_ended_because"]
        assert "died and respawned" in reason
        assert text_generator.generate_json.call_args.kwargs["purpose"] == "goal_after_failure"
        # 死んだことは失敗の分析（行き詰まった・進まない）ではない
        assert prompt_builder.build_goal_prompt.call_args.kwargs["failure_record"] == ()
        assert session.deaths_at_goal == 1  # 新しい小目標は、今の数から数える
