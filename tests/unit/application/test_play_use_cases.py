"""StartPlayUseCase と AdvancePlayUseCase のテスト。"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import CACHE_BREAK
from ailoveshen.application.ports.output.mission_store import SavedPlan
from ailoveshen.application.use_cases.house import (
    PROVISIONAL_HOUSE_SCHEMA,
    HouseDesigner,
)
from ailoveshen.application.use_cases.goal_vocabulary import goal_schema
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase, StartPlayUseCase
from ailoveshen.domain.entities import Conversation, ObjectivePlan, PlaySession
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalEndedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
    ObjectiveAddedEvent,
    ObjectiveCompletedEvent,
    ObjectiveDroppedEvent,
)
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    BlockKind,
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
    Objective,
    Mission,
    PlaybookStep,
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
# 死んだあとの決定: 分析、対処（やり直しは選べない）、教訓（設計書 105）
DIED_PLANKS = {
    "predicate": "have",
    "item": "wooden_sword",
    "count": 1,
    "serves": "current",
    "reason": "死んだので、先に身を守る剣を作る",
    "diagnosis": "夜に外で長い作業を続けて囲まれた",
    "remedy": "change",
    "lesson": "夜は外の作業を止めて家に入る",
}
BUILT = GoalSpec(GoalPredicate.BUILT)
BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
SWORD = GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1)


def _plan(**kwargs) -> ObjectivePlan:
    """家（m1）、次にベッド（m2）。"""
    plan = ObjectivePlan(mission=MISSION, **kwargs)
    plan.add("自分の家を作る", (BUILT,))
    plan.add("夜に寝られるようにする", (BED,))
    return plan


def _session(goal: GoalSpec | None = None, plan: ObjectivePlan | None = None) -> PlaySession:
    session = PlaySession(blueprint=_blueprint(), plan=plan or _plan(), max_stalled_steps=2)
    if goal is not None:
        session.set_goal(Goal(goal, objective_id="m1"), "day")
    return session


def _judged(met: set[GoalSpec] = frozenset()):
    """bridge.check: `met` の条件は満たし、ほかは満たさない。"""

    async def check(specs, materials=False):
        return [ConditionStatus(s, s in met, (f"{s.describe()}: {s in met}",)) for s in specs]

    return check


COBBLE = GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=3)
COBBLES = {**PLANKS, "item": "cobblestone", "count": 3}


def _judged_impossible(impossible: set[GoalSpec]):
    """bridge.check: `impossible` の条件は手に入る手立てがなく、ほかは進められる（どれも済んでいない）。"""

    async def check(specs, materials=False):
        return [
            ConditionStatus(s, False, impossible=("no way to get it",) if s in impossible else ())
            for s in specs
        ]

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
def objectives(bridge, events, store):
    """モックの上で動く本物の ObjectiveKeeper。"""
    return ObjectiveKeeper(bridge=bridge, event_publisher=events, store=store)


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
    async def test_the_provisional_hut_can_be_a_dirt_hut(
        self, text_generator, prompt_builder, bridge, events
    ):
        """仮の小屋は土の壁も選べる（設計書 133）: 壁と屋根が土、ドアだけ板材で作る。"""
        text_generator.generate_json.return_value = {**VALID_DESIGN, "wall_material": "dirt"}

        project = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert project.blueprint.wall_material == BlockKind.DIRT
        assert project.blueprint.material_counts() == {BlockKind.DIRT: 55, BlockKind.DOOR: 1}

    @pytest.mark.asyncio
    async def test_design_is_sent_to_bridge(self, text_generator, prompt_builder, bridge, events):
        """正しい設計ならセッションが始まり、その設計がブリッジに送られる。"""
        # LLM が大きい家（7x7、壁 4、柱つき、ドアの位置は壁の外）を書いても、最初の家は仮の小屋に決まる（設計書 109）
        text_generator.generate_json.return_value = {
            **VALID_DESIGN,
            "width": 7,
            "depth": 7,
            "wall_height": 4,
            "corner_pillars": True,
            "door_offset": 6,
        }

        project = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert project.blueprint.name == "ひだまり"
        assert project.blueprint.door_side == Side.SOUTH
        assert (project.blueprint.width, project.blueprint.depth, project.blueprint.wall_height) == (5, 5, 2)
        assert project.blueprint.corner_pillars is False and project.blueprint.door_offset == 3
        assert project.blueprint.wall_material == BlockKind.PLANKS  # 材料を書かなければ板材
        assert project.max_steps_per_goal == 7
        assert project.max_stalled_steps == 5
        assert text_generator.generate_json.call_args.args[1] is PROVISIONAL_HOUSE_SCHEMA
        bridge.set_build_plan.assert_awaited_once_with(project.blueprint)
        kwargs = prompt_builder.build_house_design_prompt.call_args.kwargs
        assert kwargs["site_note"] == "" and kwargs["provisional"] is True
        assert _published(events, HouseDesignedEvent)[0].name == "ひだまり"

    def _resumable(self, bridge, label="have(planks, 4)", store_saved=None):
        from ailoveshen.application.ports.output.goal_store import SavedGoal

        saved = store_saved or SavedGoal(Goal(HAVE_PLANKS, "板材", "m1"), "day", 6, 1, 2, 3, 0)
        goal_store = Mock()
        goal_store.load.return_value = saved
        bridge.observe.return_value = replace(
            _obs(has_plan=True, house_complete=True, has_home=True),
            goal=GoalStatus(met=False, remaining=3, spec_label=label),
        )
        return goal_store

    @pytest.mark.asyncio
    async def test_the_saved_small_goal_is_resumed_when_the_bridge_still_follows_it(
        self, text_generator, prompt_builder, bridge, events
    ):
        goal_store = self._resumable(bridge)
        use_case = self._use_case(text_generator, prompt_builder, bridge, events)
        use_case._goal_store = goal_store

        session = await use_case.execute()

        assert session.goal.spec == HAVE_PLANKS
        assert (session.goal_phase, session.steps_in_goal) == ("day", 6)
        assert (session.consecutive_failures, session.stalled_steps) == (1, 2)
        assert session.least_remaining == 3

    @pytest.mark.asyncio
    async def test_the_saved_small_goal_is_dropped_when_the_bridge_follows_another(
        self, text_generator, prompt_builder, bridge, events
    ):
        goal_store = self._resumable(bridge, label="explored(40)")
        use_case = self._use_case(text_generator, prompt_builder, bridge, events)
        use_case._goal_store = goal_store

        assert (await use_case.execute()).goal is None

    @pytest.mark.asyncio
    async def test_invalid_design_is_retried_with_error(
        self, text_generator, prompt_builder, bridge, events
    ):
        """通らない設計は、検証エラーをつけて差し戻す。"""
        text_generator.generate_json.side_effect = [{**VALID_DESIGN, "door_side": "up"}, VALID_DESIGN]

        project = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert project.blueprint.width == 5
        second = prompt_builder.build_house_design_prompt.call_args_list[1]
        assert "door_side" in second.kwargs["previous_error"] or "up" in second.kwargs["previous_error"]

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
        """何も保存していなければ、設定の大目標から始める（保存もする）。"""
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
        """大目標は再起動をまたいで続く。"""
        text_generator.generate_json.return_value = VALID_DESIGN
        store.load.return_value = SavedPlan(
            mission=MISSION,
            pending=(Objective("m7", "剣", (SWORD,), requested_by="neko", budget_used=5),),
            finished=(),
            next_id=8,
        )

        session = await self._use_case(
            text_generator, prompt_builder, bridge, events, store
        ).execute()

        assert [(g.id, g.requested_by, g.budget_used) for g in session.plan.pending] == [
            ("m7", "neko", 5)
        ]
        assert session.plan.next_id == 8

    @pytest.mark.asyncio
    async def test_saved_plan_carries_on_with_the_mission_written_after_the_first(
        self, text_generator, prompt_builder, bridge, events, store
    ):
        """達成して書いた次の最終目標と足した定石の段は、設定の最終目標が同じなら再起動で戻る。"""
        text_generator.generate_json.return_value = VALID_DESIGN
        step = PlaybookStep(name="armor", title="鉄の防具", conditions=(SWORD,), keep=True)
        store.load.return_value = SavedPlan(
            mission=Mission("鉄の装備で遠くを探索する"),
            origin=MISSION,
            pending=(),
            finished=(),
            next_id=8,
            missions_done=(MISSION.text,),
            extra_playbook=(step,),
        )

        session = await self._use_case(
            text_generator, prompt_builder, bridge, events, store
        ).execute()

        assert session.plan.mission == Mission("鉄の装備で遠くを探索する")
        assert session.plan.missions_done == (MISSION.text,)
        assert session.plan.extra_playbook == (step,)
        assert session.plan.origin == MISSION

    @pytest.mark.asyncio
    async def test_saved_plan_for_another_mission_is_not_used(
        self, text_generator, prompt_builder, bridge, events, store
    ):
        """最終目標が変わったら、設定の大目標から始め直す。"""
        text_generator.generate_json.return_value = VALID_DESIGN
        store.load.return_value = SavedPlan(
            mission=Mission("村を作る"),
            pending=(Objective("m7", "剣", (SWORD,)),),
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
        objectives,
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
            objectives=objectives,
            town=town,
            notes=notes,
        )

    @pytest.mark.asyncio
    async def test_while_a_slow_decision_runs_she_eats_when_starving(
        self, use_case, bridge, monkeypatch
    ):
        """空腹 0 のまま 6 分近く決定を待って立っていた（2026-09-28）: 待つ間に食べる。ほかはしない。"""
        import asyncio

        from ailoveshen.application.use_cases import play

        monkeypatch.setattr(play, "MEANWHILE_SECONDS", 0.01)
        hungry = replace(_obs(candidates=("eat bread", "go home")), food=3)
        full = replace(_obs(candidates=("eat bread", "go home")), food=18)
        seen = []
        bridge.observe.side_effect = lambda: seen.append(1) or (hungry if len(seen) == 1 else full)

        async def slow():
            await asyncio.sleep(0.05)

        await use_case._while_deciding(slow())

        assert [c.args[0] for c in bridge.act.await_args_list] == ["eat bread"]

    @pytest.mark.asyncio
    async def test_it_announces_that_it_started_thinking(self, use_case, events):
        """決定に何分もかかり、その間は無音だった: 考え始めたことを知らせる（失敗の後かも添える）。"""
        from ailoveshen.domain.events import GoalDecidingEvent

        async def quick():
            return None

        await use_case._while_deciding(quick())
        await use_case._while_deciding(quick(), after_failure=True)

        assert [e.after_failure for e in _published(events, GoalDecidingEvent)] == [False, True]

    @pytest.mark.asyncio
    async def test_it_announces_that_it_stopped_thinking_even_when_the_decision_fails(
        self, use_case, events
    ):
        """考えが終わったことも知らせる（決定が失敗しても。ボードの「考え中」が残らないように）。"""
        from ailoveshen.domain.events import GoalDecidedEvent, GoalDecidingEvent

        async def broken():
            raise RuntimeError("no answer")

        with pytest.raises(RuntimeError):
            await use_case._while_deciding(broken())

        kinds = [
            type(c.args[0])
            for c in events.publish.call_args_list
            if isinstance(c.args[0], (GoalDecidingEvent, GoalDecidedEvent))
        ]
        assert kinds == [GoalDecidingEvent, GoalDecidedEvent]

    @pytest.mark.asyncio
    async def test_the_chests_keep_what_the_objectives_store(self, use_case, text_generator, bridge):
        """蓄える大目標の stored() の条件は小目標と一緒に送り、チェストから取り出させない。"""
        text_generator.generate_json.return_value = PLANKS
        plan = _plan()
        stock = GoalSpec(GoalPredicate.STORED, item="food", count=10)
        plan.add("食料を蓄える", (stock,))

        await use_case.execute(_session(plan=plan))

        # 集める大目標の物は、そばで取れたら一緒に取る（also）
        bridge.set_goal.assert_awaited_once_with(HAVE_PLANKS, (stock,), (stock,), together=(), level_areas=())

    @pytest.mark.asyncio
    async def test_the_town_advances_after_the_judgement_before_the_decision(
        self, use_case, text_generator, bridge, town
    ):
        """済んだ大目標（調査）を判定してから街の準備を進め、そのあとの見え方で決める。"""
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
        bridge.set_goal.assert_awaited_once_with(HAVE_PLANKS, (), (), together=(), level_areas=())
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
    async def test_the_small_goal_and_its_counts_are_saved_every_step(
        self, use_case, text_generator
    ):
        """再起動のあとに続きから追えるよう、小目標を決めたときと 1 手ごとに保存する（設計書 63）。"""
        text_generator.generate_json.return_value = PLANKS
        use_case._goal_store = Mock()
        session = _session()

        await use_case.execute(session)

        saved = use_case._goal_store.save.call_args.args[0]
        assert saved.goal.spec == HAVE_PLANKS
        assert saved.steps_in_goal == 1  # 1 手のあとの数え
        assert use_case._goal_store.save.call_count == 2  # 決めたとき、1 手のあと

    @pytest.mark.asyncio
    async def test_predicates_offered_follow_the_house(self, use_case, text_generator, bridge, prompt_builder):
        """家・拠点・チェスト・明かりの小目標は、当てはまるときだけ出す（本文の「今使える小目標」。スキーマは変えない: 設計書 177）。"""
        text_generator.generate_json.return_value = PLANKS

        await use_case.execute(_session())
        offered = [p.value for p in prompt_builder.build_goal_prompt.call_args.kwargs["predicates"]]
        assert offered == ["have", "through_night", "explored", "found"]

        bridge.observe.return_value = _obs(goal=False, has_plan=True)
        await use_case.execute(_session())
        offered = [p.value for p in prompt_builder.build_goal_prompt.call_args.kwargs["predicates"]]
        assert offered == [
            "built",
            "have",
            "through_night",
            "explored",
            "found",
        ]

        bridge.observe.return_value = _obs(
            goal=False, has_plan=True, house_complete=True, has_home=True
        )
        await use_case.execute(_session())
        offered = [p.value for p in prompt_builder.build_goal_prompt.call_args.kwargs["predicates"]]
        assert offered == [
            "have",
            "at_home",
            "through_night",
            "cleared",
            "placed",
            "stored",
            "lit",
            "planted",
            "farmed",
            "leveled",
            "paved",
            "explored",
            "found",
        ]

        # 夜も入口の掃除を選べる（武器と体力はブリッジが確かめ、持ち物は先にチェストへ。2026-09-27）
        bridge.observe.return_value = _obs(
            goal=False, has_plan=True, house_complete=True, has_home=True, time_phase="night"
        )
        await use_case.execute(_session())
        offered = [p.value for p in prompt_builder.build_goal_prompt.call_args.kwargs["predicates"]]
        assert "cleared" in offered

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
        bridge.set_goal.assert_awaited_once_with(HAVE_PLANKS, (), (), together=(), level_areas=())

    @pytest.mark.asyncio
    async def test_a_bundle_member_that_is_not_offered_is_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """家がなくて出していない planted を束に入れた決定は差し戻す（植林場がなく、進まなかった）。"""
        bundled = {**PLANKS, "together": [{"predicate": "planted", "item": "sapling", "count": 4}]}
        text_generator.generate_json.side_effect = [bundled, PLANKS]
        session = _session()

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "planted in together is not one of the goals offered" in error
        assert session.goal.spec == HAVE_PLANKS

    @pytest.mark.asyncio
    async def test_a_goal_already_met_is_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """決めた時点で済んでいる小目標は差し戻す（2026-09-27: かまどを持っているのに have(furnace)）。"""
        text_generator.generate_json.side_effect = [PLANKS, {**PLANKS, "count": 8}]
        bridge.set_goal.side_effect = [GoalStatus(True, 0), GoalStatus(False, 4)]
        session = _session()

        await use_case.execute(session)

        assert session.goal.spec.count == 8
        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "have(planks, 4) is already met now" in error

    @pytest.mark.asyncio
    async def test_a_goal_already_met_says_what_is_carried_and_that_have_is_a_total(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """11 個持っていて have(planks, 4)（「あと 4 個」の取り違え）: 持っている数と書き直し方を添える。"""
        text_generator.generate_json.side_effect = [PLANKS, {**PLANKS, "count": 16}]
        bridge.observe.return_value = replace(
            _obs(), state={"self": {"health": 20}, "inventory": {"planks": 11}}
        )
        bridge.set_goal.side_effect = [GoalStatus(True, 0), GoalStatus(False, 5)]

        await use_case.execute(_session())

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "you already carry 11 planks" in error
        assert "n in total, not n more" in error

    @pytest.mark.asyncio
    async def test_farmed_already_met_says_harvesting_is_have(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """小麦が実っていて farmed(wheat, 64) を選んだ（植わっている数は済み）: 収穫は have(wheat, n) と添える。"""
        farmed = {"predicate": "farmed", "item": "wheat", "count": 64, "serves": "current"}
        text_generator.generate_json.side_effect = [farmed, PLANKS]
        bridge.observe.return_value = _obs(has_home=True)
        bridge.set_goal.side_effect = [GoalStatus(True, 0), GoalStatus(False, 4)]

        await use_case.execute(_session())

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "farmed(wheat, 64) is already met now" in error
        assert "to harvest the ripe wheat, choose have(wheat, n)" in error

    @pytest.mark.asyncio
    async def test_a_goal_that_cannot_progress_is_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """進める手がない小目標（畑がないのに farmed など）は差し戻す（設計書 47 §15）。"""
        text_generator.generate_json.side_effect = [PLANKS, {**PLANKS, "count": 8}]
        bridge.set_goal.side_effect = [
            GoalStatus(False, 4, dead_end=("there is no farm for wheat",)),
            GoalStatus(False, 8),
        ]
        session = _session()

        await use_case.execute(session)

        assert session.goal.spec.count == 8
        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "cannot progress now: there is no farm for wheat" in error

    @pytest.mark.asyncio
    async def test_jobs_before_one_already_met_are_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """中目標は順番どおり: 後の中目標が済んでいるのに前が残っていれば、前は要らない中目標。"""
        stone = {"predicate": "have", "item": "cobblestone", "count": 8, "reason": "石"}
        planks = {"predicate": "have", "item": "planks", "count": 4, "reason": "板"}
        text_generator.generate_json.side_effect = [
            {**PLANKS, "jobs": [stone, planks]},
            {**PLANKS, "jobs": [planks, stone]},
        ]
        bridge.check.side_effect = _judged({HAVE_PLANKS})
        session = _session()

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "job 2 have(planks, 4) is already met now" in error
        assert "have(cobblestone, 8)" in error
        assert [s.spec.item for s in session.plan.current.jobs] == ["planks", "cobblestone"]

    @pytest.mark.asyncio
    async def test_a_job_that_only_gathers_a_raw_material_is_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """中目標が素材の採集そのもの（小目標と同じ文になる）なら、作るものを中目標にさせる（設計書 50 §14）。"""
        stone = {"predicate": "have", "item": "cobblestone", "count": 8, "reason": "石"}
        planks = {"predicate": "have", "item": "planks", "count": 4, "reason": "板"}
        text_generator.generate_json.side_effect = [
            {**PLANKS, "jobs": [stone]},
            {**PLANKS, "jobs": [planks]},
        ]

        async def check(specs):
            return [
                ConditionStatus(s, False, gather=s.item == "cobblestone") for s in specs
            ]

        bridge.check.side_effect = check
        session = _session()

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "have(cobblestone, 8) is only gathering a raw material" in error
        assert [s.spec.item for s in session.plan.current.jobs] == ["planks"]

    @pytest.mark.asyncio
    async def test_a_review_that_mistakes_the_time_is_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """実測（2026-09-27）: 夕方に「夜が明けたので外に出られる」と書いて外の中目標を決めた。"""
        bridge.observe.return_value = _obs(goal=False, time_phase="dusk")
        text_generator.generate_json.side_effect = [
            {**PLANKS, "review": "夜が明けたので外に出られる状態になった。"},
            {**PLANKS, "review": "夜になる前に、夜が明けたら使う板材を作っておく。"},
        ]
        session = _session()

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "the review says '夜が明け', but it is dusk now" in error
        assert text_generator.generate_json.await_count == 2

    @pytest.mark.asyncio
    async def test_through_night_by_day_is_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """実測（2026-10-04 10:06）: 昼なのに through_night を選び、畑の中に穴を掘って潜った。"""
        bridge.observe.return_value = _obs(goal=False, time_phase="day")
        text_generator.generate_json.side_effect = [
            {**PLANKS, "predicate": "through_night", "reason": "家に入れないので安全を確保する"},
            PLANKS,
        ]
        await use_case.execute(_session())

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "it is day now, not night" in error

    @pytest.mark.asyncio
    async def test_through_night_by_day_is_sent_back_on_the_last_attempt_too(
        self, use_case, text_generator, bridge
    ):
        """実測（2026-10-10 16:33）: 最後の試行で通った昼の through_night が、夕方まで残った。"""
        bridge.observe.return_value = _obs(goal=False, time_phase="day")
        text_generator.generate_json.return_value = {
            **PLANKS,
            "predicate": "through_night",
            "reason": "夜が近いので家で過ごす",
        }

        with pytest.raises(TextGenerationError, match="not night"):
            await use_case.execute(_session())

        assert text_generator.generate_json.await_count == 3
        bridge.act.assert_not_called()

    @pytest.mark.asyncio
    async def test_through_night_underground_is_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """実測（2026-10-10 19:00）: 家に帰れない地下で through_night を選び、穴に閉じこもって約 8 分待った。"""
        bridge.observe.return_value = _obs(goal=False, time_phase="night", underground=True)
        text_generator.generate_json.side_effect = [
            {**PLANKS, "predicate": "through_night", "reason": "夜を安全に越すのが先"},
            PLANKS,
        ]
        await use_case.execute(_session())

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "deep underground" in error

    @pytest.mark.asyncio
    async def test_a_reason_that_mistakes_the_time_is_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        bridge.observe.return_value = _obs(goal=False, time_phase="day")
        text_generator.generate_json.side_effect = [
            {**PLANKS, "reason": "夜になったので板を作る"},
            PLANKS,
        ]
        await use_case.execute(_session())

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "the reason says '夜になった'" in error

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
    async def test_a_rejected_decision_at_night_says_how_to_spend_the_night(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """2026-10-07 00:42Z: 夜の差し戻しが時間帯に触れず、進められない小目標を選び続けた。"""
        bridge.observe.return_value = _obs(
            goal=False, time_phase="night", has_home=True, inside_home=True
        )
        text_generator.generate_json.side_effect = [PLANKS, PLANKS]
        bridge.set_goal.side_effect = [
            GoalStatus(False, 4, dead_end=("no way to get planks",)),
            GoalStatus(False, 4),
        ]
        await use_case.execute(_session())

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "no way to get planks" in error
        assert "It is night now" in error
        assert "choose through_night" in error

    @pytest.mark.asyncio
    async def test_a_rejected_night_goal_gets_no_night_note(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """夜を越す小目標（through_night / at_home / cleared）を選んだ差し戻しには、言い添えない。"""
        bridge.observe.return_value = _obs(
            goal=False, time_phase="night", has_home=True, inside_home=True
        )
        text_generator.generate_json.side_effect = [
            {**PLANKS, "predicate": "at_home", "reason": "家にいる"},
            PLANKS,
        ]
        bridge.set_goal.side_effect = [GoalStatus(True, 0), GoalStatus(False, 4)]
        await use_case.execute(_session())

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "It is night now" not in error

    @pytest.mark.asyncio
    async def test_decisions_that_fail_all_night_become_through_night(
        self, use_case, text_generator, bridge
    ):
        """夜のあいだ決定が決まらず、行動が 1 つもないまま夜が明けた（2026-10-07 00:42〜00:52Z）。"""
        bridge.observe.return_value = _obs(
            goal=False, time_phase="night", has_home=True, inside_home=True
        )
        text_generator.generate_json.return_value = PLANKS
        spec_seen: list = []

        async def set_goal(spec, *args, **kwargs):
            spec_seen.append(spec)
            if spec.predicate == GoalPredicate.THROUGH_NIGHT:
                return GoalStatus(False, 1)
            return GoalStatus(False, 4, dead_end=("no way to get planks",))

        bridge.set_goal.side_effect = set_goal
        session = _session()

        await use_case.execute(session)

        assert session.goal.spec.predicate == GoalPredicate.THROUGH_NIGHT
        assert session.goal.objective_id is None
        assert "no other small goal could be decided" in session.goal.reason
        assert text_generator.generate_json.await_count == 3

    @pytest.mark.asyncio
    async def test_decisions_that_fail_by_day_still_raise(self, use_case, text_generator, bridge):
        """昼は夜を越す小目標にしない（今までどおり決定の失敗）。"""
        bridge.observe.return_value = _obs(goal=False, time_phase="day")
        text_generator.generate_json.return_value = PLANKS
        bridge.set_goal.return_value = GoalStatus(False, 4, dead_end=("no way to get planks",))

        with pytest.raises(TextGenerationError, match="no acceptable goal"):
            await use_case.execute(_session())

    @pytest.mark.asyncio
    async def test_a_dead_end_says_which_jobs_can_progress(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """進められない小目標の差し戻しに、いま進められる中目標を添える（2026-10-07 昼: 丸石を選び続けた）。"""
        from ailoveshen.domain.value_objects import Job

        bridge.observe.return_value = _obs(goal=False, time_phase="day")
        text_generator.generate_json.side_effect = [COBBLES, COBBLES]
        bridge.set_goal.side_effect = [
            GoalStatus(False, 4, dead_end=("no way to get cobblestone",)),
            GoalStatus(False, 4),
        ]
        session = _session()
        session.plan.set_jobs("m1", (Job(COBBLE, "丸石"), Job(HAVE_PLANKS, "板材")))
        bridge.check.side_effect = _judged_impossible({COBBLE})

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args_list[1].kwargs["previous_error"]
        assert "Jobs that can progress now: have(planks, 4)" in error
        assert "cobblestone" not in error.split("Jobs that can progress now")[1]

    @pytest.mark.asyncio
    async def test_decisions_that_fail_by_day_become_a_job_that_can_progress(
        self, use_case, text_generator, bridge
    ):
        """昼も決定が決まらなければ、いま進められる中目標の次の一歩にする（行動 0 件で回り続けない）。"""
        from ailoveshen.domain.value_objects import Job

        bridge.observe.return_value = _obs(goal=False, time_phase="day")
        text_generator.generate_json.return_value = COBBLES
        spec_seen: list = []

        async def set_goal(spec, *args, **kwargs):
            spec_seen.append(spec)
            if spec == HAVE_PLANKS:
                return GoalStatus(False, 2)
            return GoalStatus(False, 4, dead_end=("no way to get cobblestone",))

        bridge.set_goal.side_effect = set_goal
        session = _session()
        session.plan.set_jobs("m1", (Job(COBBLE, "丸石"), Job(HAVE_PLANKS, "板材")))
        bridge.check.side_effect = _judged_impossible({COBBLE})

        await use_case.execute(session)

        assert session.goal.spec == HAVE_PLANKS
        assert session.goal.objective_id == "m1"
        assert "can progress now" in session.goal.reason
        assert text_generator.generate_json.await_count == 3

    @pytest.mark.asyncio
    async def test_decisions_that_fail_by_day_with_no_job_that_can_progress_still_raise(
        self, use_case, text_generator, bridge
    ):
        """進められる中目標がなければ、作り出さずに決定の失敗のまま。"""
        from ailoveshen.domain.value_objects import Job

        bridge.observe.return_value = _obs(goal=False, time_phase="day")
        text_generator.generate_json.return_value = COBBLES
        bridge.set_goal.return_value = GoalStatus(False, 4, dead_end=("no way to get cobblestone",))
        session = _session()
        session.plan.set_jobs("m1", (Job(COBBLE, "丸石"),))
        bridge.check.side_effect = _judged_impossible({COBBLE})

        with pytest.raises(TextGenerationError, match="no acceptable goal"):
            await use_case.execute(session)

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
        args = text_generator.generate_json.call_args.args
        assert args[0].startswith(f"goal system{CACHE_BREAK}")  # 決め方の規則は本文の先頭に置く（設計書 175）

    @pytest.mark.asyncio
    async def test_small_goal_serves_the_top_objective(self, use_case, text_generator, events):
        """小目標は今取り組む大目標のために決まり、そのことを示す。"""
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        await use_case.execute(session)

        assert session.goal.objective_id == "m1"
        assert _published(events, GoalSetEvent)[0].objective == "自分の家を作る"

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
    async def test_objectives_are_judged_before_deciding(
        self, use_case, text_generator, bridge, prompt_builder, events, store
    ):
        """もう済んでいる大目標（前の実行で建てた家）は、最初に完了にする。"""
        bridge.check.side_effect = _judged({BUILT})
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        await use_case.execute(session)

        assert [g.id for g in session.plan.pending] == ["m2"]
        assert _published(events, ObjectiveCompletedEvent)[0].title == "自分の家を作る"
        activity = prompt_builder.build_goal_prompt.call_args.kwargs["activity"]
        assert [g.id for g in activity.objectives if g.state.value == "pending"] == ["m2"]
        assert session.goal.objective_id == "m2"
        assert session.plan.get("m2").progress == ("placed(bed, home): False",)
        store.save.assert_called()

    @pytest.mark.asyncio
    async def test_survival_goal_serves_no_objective(self, use_case, text_generator, events):
        """生存のための小目標は、どの大目標のためでもない。"""
        text_generator.generate_json.return_value = {
            "predicate": "have",
            "item": "food",
            "count": 4,
            "serves": "survival",
            "reason": "お腹が空いた",
        }
        session = _session()

        await use_case.execute(session)

        assert session.goal.objective_id is None
        assert _published(events, GoalSetEvent)[0].objective == ""

    @pytest.mark.asyncio
    async def test_only_survival_goals_may_skip_the_objectives(
        self, use_case, text_generator, prompt_builder
    ):
        """生存に関係ない小目標は、一番上の大目標のためでなければならない。"""
        text_generator.generate_json.side_effect = [{**PLANKS, "serves": "survival"}, PLANKS]
        session = _session()

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "not a survival goal" in error
        assert session.goal.objective_id == "m1"

    @pytest.mark.asyncio
    async def test_no_objective_left_needs_one_added(self, use_case, text_generator, prompt_builder):
        """リストが空なら、小目標は大目標を足すか、生存のためのものでなければならない。"""
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
        session = _session(plan=ObjectivePlan(mission=MISSION))

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "no objective left" in error
        assert session.plan.current.title == "剣を持つ"
        assert session.goal.objective_id == session.plan.current.id

    @pytest.mark.asyncio
    async def test_the_last_attempt_drops_edits_the_plan_refuses_and_keeps_the_goal(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """定石の大目標を動かす move を書き続けても、最後の試行で捨てて決定は通す（2026-10-03: 夜の野外で 4 分以上決まらなかった）。"""
        plan = ObjectivePlan(
            mission=MISSION, playbook=(PlaybookStep("bed", "寝床", (BED,), "夜に寝る"),)
        )
        plan.add("寝床", (BED,), playbook="bed")
        plan.add("松明", (GoalSpec(GoalPredicate.HAVE, item="torch", count=8),))
        text_generator.generate_json.return_value = {
            **PLANKS,
            "plan_changes": [{"op": "move", "id": "m1", "position": 1, "reason": "夜が近い"}],
        }
        session = _session(plan=plan)

        await use_case.execute(session)

        assert text_generator.generate_json.await_count == 3
        errors = [
            c.kwargs["previous_error"] for c in prompt_builder.build_goal_prompt.call_args_list
        ]
        assert "cannot be moved" in errors[1] and "cannot be moved" in errors[2]
        assert session.goal.spec == HAVE_PLANKS
        assert [g.title for g in session.plan.pending] == ["寝床", "松明"]

    @pytest.mark.asyncio
    async def test_a_death_while_deciding_does_not_act_on_the_goal_written_before_it(
        self, use_case, text_generator, bridge, selector
    ):
        """決めている間に死んだら、その目標では動かない（2026-10-03: 死んだ直後に 301 m 歩いた）。"""
        text_generator.generate_json.return_value = PLANKS
        before = replace(_obs(("dig oak_log", "wait inside")), state={"self": {}, "deaths": 0})
        after = replace(before, state={"self": {}, "deaths": 1})
        bridge.observe.side_effect = [before, after]

        report = await use_case.execute(_session())

        assert report.waiting and report.goal_changed
        selector.select.assert_not_called()
        bridge.act.assert_not_called()

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
        assert session.goal.objective_id == "m2"
        added = _published(events, ObjectiveAddedEvent)[0]
        assert (added.title, added.position, added.requested_by) == ("剣を持つ", 3, "")
        schema = text_generator.generate_json.call_args.args[1]
        assert schema == goal_schema()  # 大目標の ID はスキーマに入れない（設計書 177）
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
    async def test_new_objective_the_bridge_cannot_judge_is_retried(
        self, use_case, text_generator, prompt_builder, bridge
    ):
        """足す大目標の条件は、先にブリッジが確かめる。"""
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
        """LLM が決めている間に足された視聴者の大目標は、LLM の編集で上書きされない。"""
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
    async def test_a_point_made_during_a_goal_decision_is_kept_for_the_next_boundary(
        self, use_case, text_generator, bridge
    ):
        """決めている間に来たコメントの指摘は、新しい小目標で消さない（その決定は見ていない）。"""
        session = _session(HAVE_PLANKS)
        session.request_rethink("on screen: a creeper")
        bridge.observe.return_value = _obs(met=True)

        async def decide(*args, **kwargs):
            session.request_rethink("neko pointed out in chat: a tree is right there")
            return PLANKS

        text_generator.generate_json.side_effect = decide

        await use_case.execute(session)

        assert session.rethink_reason == "neko pointed out in chat: a tree is right there"

    @pytest.mark.asyncio
    async def test_a_rethink_during_an_action_stops_it(self, use_case, bridge, monkeypatch):
        """行動は時間で止まらないので、実行中に考え直しを頼まれたらその行動を止める（38）。"""
        monkeypatch.setattr("ailoveshen.application.use_cases.play.RETHINK_POLL_SECONDS", 0.01)
        session = _session(HAVE_PLANKS)
        stopped = asyncio.Event()
        reasons: list[str] = []

        async def act(action_id):
            session.request_rethink("neko pointed out in chat: a tree is right there")
            await stopped.wait()
            return ActionResult(action_id, False, f"failed: {reasons[0]}", 3.0)

        async def abort(reason):
            reasons.append(reason)
            stopped.set()
            return True

        bridge.act.side_effect = act
        bridge.abort.side_effect = abort

        report = await use_case.execute(session)

        assert reasons == ["rethink: neko pointed out in chat: a tree is right there"]
        assert report.result.cut_by_attack
        # 理由は次の切れ目で使うので残る
        assert session.rethink_reason == "neko pointed out in chat: a tree is right there"

    def test_a_rethink_waits_while_sheltering_through_the_night_and_reaches_the_morning_decision(self):
        """夜を越している間は、コメントへの約束の考え直しで夜越しを切らない（朝の決定に伝える）。"""
        night = GoalSpec(GoalPredicate.THROUGH_NIGHT)
        session = _session()
        session.set_goal(Goal(night), "night")
        session.request_rethink('you told neko you would do "自分の家を作る" now')
        assert session.goal_end_reason(_obs(time_phase="night")) == ""
        assert session.defers_rethink(_obs(time_phase="night"))
        morning = session.goal_end_reason(_obs(time_phase="dawn"))
        assert "the night has passed (dawn)" in morning and 'you told neko you would do' in morning
        # 夜を越す目標でなければ、今までどおり考え直す
        other = _session(HAVE_PLANKS)
        other.request_rethink("neko pointed out in chat: a tree is right there")
        assert "is reconsidered" in other.goal_end_reason(_obs(time_phase="night"))

    @pytest.mark.asyncio
    async def test_a_rethink_does_not_stop_the_wait_in_the_shelter(self, use_case, bridge, monkeypatch):
        monkeypatch.setattr("ailoveshen.application.use_cases.play.RETHINK_POLL_SECONDS", 0.01)
        session = _session()
        session.set_goal(Goal(GoalSpec(GoalPredicate.THROUGH_NIGHT)), "night")
        session.observe(_obs(time_phase="night"))
        bridge.observe.return_value = _obs(time_phase="night")
        aborted: list[str] = []

        async def act(action_id):
            session.request_rethink("you told neko you would do something now")
            await asyncio.sleep(0.1)
            return ActionResult(action_id, True, "waited", 1.0)

        async def abort(reason):
            aborted.append(reason)
            return True

        bridge.act.side_effect = act
        bridge.abort.side_effect = abort
        await use_case.execute(session)
        assert aborted == []

    @pytest.mark.asyncio
    async def test_a_rethink_before_the_action_starts_stops_it_once_it_runs(
        self, use_case, bridge, monkeypatch
    ):
        """まだ行動が始まっていなければ（止めるものがない）、始まるまで止め続ける。"""
        monkeypatch.setattr("ailoveshen.application.use_cases.play.RETHINK_POLL_SECONDS", 0.01)
        session = _session(HAVE_PLANKS)
        running = asyncio.Event()
        stopped = asyncio.Event()
        calls: list[bool] = []

        async def act(action_id):
            session.request_rethink("on screen: the tree is behind the house")
            await asyncio.sleep(0.05)
            running.set()
            await stopped.wait()
            return ActionResult(action_id, False, "failed: rethink: on screen", 3.0)

        async def abort(reason):
            calls.append(running.is_set())
            if running.is_set():
                stopped.set()
            return running.is_set()

        bridge.act.side_effect = act
        bridge.abort.side_effect = abort

        await use_case.execute(session)

        assert calls[0] is False and calls[-1] is True
        assert calls.count(True) == 1

    @pytest.mark.asyncio
    async def test_viewer_objective_over_budget_is_dropped_and_told(
        self, use_case, text_generator, bridge, events, store
    ):
        """ステップの予算を超えた頼みはやめ（言う）、その小目標は次のステップで終わる。"""
        plan = ObjectivePlan(mission=MISSION, viewer_budget=1)
        request = plan.add("剣", (SWORD,), requested_by="neko")
        session = PlaySession(blueprint=_blueprint(), plan=plan)
        session.set_goal(Goal(HAVE_PLANKS, objective_id=request.id), "day")

        await use_case.execute(session)

        dropped = _published(events, ObjectiveDroppedEvent)[0]
        assert (dropped.title, dropped.requested_by) == ("剣", "neko")
        assert "over the budget" in dropped.reason
        assert "served has ended" in session.goal_end_reason(bridge.observe.return_value)
        store.save.assert_called_with(plan)


LOG = GoalSpec(GoalPredicate.HAVE, item="log", count=12)
STEPS_DECISION = {
    **PLANKS,
    "jobs": [
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


class TestJobsChosenByJev:
    """小目標は Gemini が書いた中目標から Jev が選ぶ（docs/design/26）。"""

    @pytest.fixture
    def make(self, bridge, text_generator, prompt_builder, selector, events, objectives):
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
                objectives=objectives,
                town=AsyncMock(),
                notes=NoteKeeper(notes_store),
                chooser=chooser,
                clock=clock,
            )

        return make

    @pytest.mark.asyncio
    async def test_gemini_writes_the_jobs_once_then_jev_picks_the_next(
        self, make, text_generator, bridge
    ):
        text_generator.generate_json.return_value = STEPS_DECISION
        use_case = make(FakeJev("B"))
        session = _session()

        await use_case.execute(session)  # 中目標がない: Gemini が書く
        assert [s.spec for s in session.plan.current.jobs] == [LOG, HAVE_PLANKS, BUILT]
        assert text_generator.generate_json.await_count == 1

        bridge.observe.return_value = _obs(met=True)  # 小目標が済んだ → Jev
        bridge.check.side_effect = _judged({LOG})  # 原木は済んでいる
        await use_case.execute(session)
        assert text_generator.generate_json.await_count == 1  # Gemini は呼ばない
        # 済んだ中目標は出さない: A=板材、B=建てる
        assert session.goal.spec == BUILT
        assert session.goal.objective_id == "m1"
        assert session.goal.reason == "家を建てる"

    @pytest.mark.asyncio
    async def test_gemini_must_write_jobs_when_the_top_has_none(
        self, make, text_generator, prompt_builder
    ):
        text_generator.generate_json.side_effect = [PLANKS, STEPS_DECISION]
        await make().execute(_session())
        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "write the jobs" in error

    @pytest.mark.asyncio
    async def test_a_survival_goal_needs_no_jobs_written(
        self, make, text_generator, bridge
    ):
        """夜に家へ戻るなど身を守る小目標は、大目標に中目標がなくても差し戻さない。"""
        bridge.observe.return_value = _obs(
            goal=False, has_plan=True, house_complete=True, has_home=True, time_phase="night"
        )
        text_generator.generate_json.side_effect = [
            {"predicate": "at_home", "serves": "survival", "reason": "夜だから帰る"}
        ]
        session = _session()
        await make().execute(session)
        assert text_generator.generate_json.await_count == 1
        assert session.goal.spec.predicate == GoalPredicate.AT_HOME

    @pytest.mark.asyncio
    async def test_failure_low_confidence_and_used_up_jobs_go_to_gemini(
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
        bridge.check.side_effect = _judged({LOG, HAVE_PLANKS, BUILT})  # 中目標は全部済んだ
        await confident.execute(session)
        assert text_generator.generate_json.await_count == 3

    @pytest.mark.asyncio
    async def test_each_gemini_decision_reviews_the_jobs_as_they_are_now(
        self, make, text_generator, bridge, prompt_builder
    ):
        """Gemini が決めるたびに、中目標の今の判定を見せ、見直し（review）を毎回書かせる（31）。"""
        text_generator.generate_json.return_value = {
            **STEPS_DECISION,
            "review": "原木はもう 12 本あるので板材から",
        }
        session = _session()
        use_case = make(FakeJev("A", confidence=0.1))
        await use_case.execute(session)  # 中目標がない: 判定するものもない
        assert prompt_builder.build_goal_prompt.call_args.kwargs["job_status"] == ()

        bridge.observe.return_value = _obs(met=True)
        bridge.check.side_effect = _judged({LOG})
        await use_case.execute(session)  # 自信がない → Gemini。中目標の今の判定を見せる

        status = prompt_builder.build_goal_prompt.call_args.kwargs["job_status"]
        assert [(st.spec, st.met) for st in status] == [
            (LOG, True),
            (HAVE_PLANKS, False),
            (BUILT, False),
        ]
        schema = text_generator.generate_json.call_args.args[1]
        assert list(schema["properties"])[0] == "review"
        assert "review" in schema["required"]
        assert session.goal.review == "原木はもう 12 本あるので板材から"

    @pytest.mark.asyncio
    async def test_an_objective_that_does_not_progress_goes_back_to_gemini(
        self, bridge, text_generator, prompt_builder, selector, events, objectives
    ):
        """小目標が済んでも大目標の数が変わらないまま続けば、Jev ではなく Gemini が考え直す。"""
        from ailoveshen.application.use_cases.goal_chooser import GoalChooser

        notes_store = Mock()
        notes_store.load.return_value = None
        use_case = AdvancePlayUseCase(
            bridge=bridge,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            action_selector=selector,
            event_publisher=events,
            conversation=Conversation(),
            objectives=objectives,
            town=AsyncMock(),
            notes=NoteKeeper(notes_store),
            chooser=GoalChooser(FakeJev("A"), bridge),
            objective_stall_steps=4,
        )
        text_generator.generate_json.return_value = STEPS_DECISION
        bridge.check.side_effect = _judged()
        session = _session()
        await use_case.execute(session)  # 中目標を書く
        assert text_generator.generate_json.await_count == 1

        for remaining in (5, 4, 3, 2):  # 小目標は進むが、大目標の数は変わらない
            bridge.observe.return_value = _obs(remaining=remaining)
            await use_case.execute(session)
        bridge.observe.return_value = _obs(met=True)
        text_generator.generate_json.return_value = {
            "predicate": "explored",
            "distance": 120,
            "serves": "current",
            "reason": "近くに木がないので遠くへ",
            "diagnosis": "まわりに木がない",
            "remedy": "change",
            "review": "近くの木は切り尽くした",
        }
        await use_case.execute(session)

        assert text_generator.generate_json.await_count == 2
        reason = prompt_builder.build_goal_prompt.call_args.kwargs["goal_ended_because"]
        assert "has made no progress in 5 actions" in reason
        assert session.goal.spec.predicate == GoalPredicate.EXPLORED
        assert prompt_builder.build_goal_prompt.call_args.kwargs["retry_allowed"] is False

    def test_which_boundaries_go_to_gemini(self, make):
        from ailoveshen.domain.value_objects import Job

        session = _session()
        assert "no jobs yet" in make()._needs_gemini(session, "goal have(log, 12) is met")
        session.plan.set_jobs("m1", (Job(LOG, "原木"),))
        assert make()._needs_gemini(session, "goal have(log, 12) is met") is None
        assert make()._needs_gemini(session, "the time of day changed from day to dusk") is None
        assert "did not work out" in make()._needs_gemini(session, "goal x is stuck (actions)")
        assert "did not work out" in make()._needs_gemini(session, "goal x is reconsidered: y")
        assert "decided by Gemini" in make(jev=False)._needs_gemini(session, "goal x is met")
        # 定期の見直し: 大目標が変わっていなければ 3 倍まで待つ
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

    def test_a_build_that_could_not_be_designed_is_left_out_for_a_while(self, make):
        now = [0.0]
        use_case = make(clock=lambda: now[0])
        use_case._undesignable["storehouse"] = 600.0
        assert use_case._undesignable_now() == ["storehouse"]
        now[0] = 601.0
        assert use_case._undesignable_now() == []

    def test_after_a_first_failure_jev_may_route_it(self, make):
        """失敗の後の振り分け（docs/design/34 §7）: 1 回目は Jev に、同じ種類が 2 回続いたら Gemini。"""
        from ailoveshen.domain.value_objects import GoalOutcome, Job

        session = _session()
        session.plan.set_jobs("m1", (Job(LOG, "原木"),))
        use_case = make()
        use_case._failure_routing = "jev"
        stuck = "goal have(planks, 4) is stuck (3 failed actions)"
        assert use_case._needs_gemini(session, stuck) is None
        failed = Goal(spec=HAVE_PLANKS, reason="r")
        session._recent_goals.extend([GoalOutcome(failed, stuck), GoalOutcome(failed, stuck)])
        assert "2 times in a row" in use_case._needs_gemini(session, stuck)
        # 種類が交互でも、3 回続けて失敗したら Gemini
        session._recent_goals.clear()
        other = Goal(spec=LOG, reason="r")
        session._recent_goals.extend(
            [GoalOutcome(failed, stuck), GoalOutcome(other, stuck), GoalOutcome(failed, stuck)]
        )
        assert "3 small goals in a row" in use_case._needs_gemini(session, stuck)
        assert "did not work out" in use_case._needs_gemini(session, "goal x is reconsidered: y")

    @pytest.mark.asyncio
    async def test_after_a_failure_jev_picks_another_job_or_hands_it_to_gemini(self, bridge):
        from ailoveshen.application.use_cases.goal_chooser import (
            ASK_STREAMER,
            ChosenGoal,
            GoalChooser,
            NotChosen,
        )
        from ailoveshen.domain.value_objects import Job

        session = _session()
        session.plan.set_jobs(
            "m1", (Job(LOG, "原木"), Job(HAVE_PLANKS, "板材"), Job(BUILT, "建てる"))
        )
        bridge.check.side_effect = _judged()
        failed_planks = GoalSpec(GoalPredicate.HAVE, item="planks", count=8)  # 数だけ違う: 同じ種類
        jev = FakeJev("A")
        picked = await GoalChooser(jev, bridge).choose(session, _obs(), "stuck", avoid=failed_planks)
        assert isinstance(picked, ChosenGoal) and picked.spec == LOG
        criteria = jev.asked[0][1][0].criteria
        assert ASK_STREAMER in criteria and not any("planks" in v for v in criteria.values())

        bridge.check.side_effect = _judged()
        rethink = await GoalChooser(FakeJev(ASK_STREAMER), bridge).choose(
            session, _obs(), "stuck", avoid=failed_planks
        )
        assert isinstance(rethink, NotChosen) and "rethink" in rethink.why

    @pytest.mark.asyncio
    async def test_at_night_the_survival_goal_serves_no_objective(
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
        assert picked.objective_id is None

    @pytest.mark.asyncio
    async def test_survival_goals_are_not_sent_to_the_bridge_as_conditions(
        self, make, text_generator, bridge
    ):
        """ブリッジは through_night / at_home を条件として判定しない（送ると判定全体が断られ、夜はいつも
        Gemini に回った: 2026-09-29 から 90 回）。実物と同じに、条件でない述語を含むと断るものにする。"""
        from ailoveshen.application.use_cases.goal_chooser import GoalChooser
        from ailoveshen.domain.exceptions import GoalRejectedError
        from ailoveshen.domain.value_objects import CONDITION_PREDICATES

        text_generator.generate_json.return_value = STEPS_DECISION
        session = _session()
        await make().execute(session)

        async def check(specs):
            for s in specs:
                if s.predicate not in CONDITION_PREDICATES:
                    raise GoalRejectedError(f"conditions rejected: {s.predicate.value} cannot be a condition")
            return [ConditionStatus(s, False, ()) for s in specs]

        bridge.check.side_effect = check
        night = _obs(met=True, time_phase="night", has_home=True, inside_home=True)
        picked = await GoalChooser(FakeJev("D"), bridge).choose(session, night, "goal x is met")
        assert picked.spec == GoalSpec(GoalPredicate.THROUGH_NIGHT)  # 判定できないで Gemini には回らない

    @pytest.mark.asyncio
    async def test_a_survival_goal_already_met_is_not_offered(self, make, text_generator, bridge):
        """夕方にもう家の中なら at_home は出さない（選ぶとすぐ済む）。代わりに、夜が明けるまで家で過ごす（外の作業には出ない）。"""
        from ailoveshen.application.use_cases.goal_chooser import GoalChooser

        text_generator.generate_json.return_value = STEPS_DECISION
        session = _session()
        await make().execute(session)
        dusk = _obs(met=True, time_phase="dusk", has_home=True, inside_home=True)
        bridge.check.side_effect = _judged({LOG, HAVE_PLANKS, GoalSpec(GoalPredicate.AT_HOME)})
        jev = FakeJev("A")
        picked = await GoalChooser(jev, bridge).choose(session, dusk, "goal x is met")
        # 夕方にもう家の中: 外の作業（建てる）は選ばず、家で夜を過ごす。Jev には聞かない
        # （2026-09-30 18:08: 済みの at_home が差し戻され続け、外の作業が通って死んだ）
        assert picked.spec == GoalSpec(GoalPredicate.THROUGH_NIGHT)
        assert jev.asked == []


def test_jobs_must_be_conditions_the_world_can_judge():
    from ailoveshen.application.use_cases.goal_vocabulary import parse_decision

    decision = parse_decision(STEPS_DECISION)
    assert [s.reason for s in decision.jobs] == ["原木を集める", "板材にする", "家を建てる"]
    with pytest.raises(ValueError, match="job 1: explored cannot be a job"):
        parse_decision({**PLANKS, "jobs": [{"predicate": "explored", "distance": 30}]})


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
    async def test_the_same_goal_needs_advice_whatever_the_remedy_says(
        self, use_case, text_generator, prompt_builder
    ):
        no_advice = {**RETRY_PLANKS, "advice": ""}
        # 数だけ変えても同じ小目標: 札が change でも、助言が要る
        disguised = {**RETRY_PLANKS, "remedy": "change", "count": 2, "advice": ""}
        text_generator.generate_json.side_effect = [no_advice, disguised, RETRY_PLANKS]

        await self._stall(use_case, _session(HAVE_PLANKS))

        errors = [
            c.kwargs["previous_error"] for c in prompt_builder.build_goal_prompt.call_args_list
        ]
        assert "retry needs advice" in errors[1]
        assert "retry needs advice" in errors[2]

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
        assert text_generator.generate_json.call_args.args[1] == goal_schema(after_failure=True)
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
        session.set_goal(Goal(HAVE_PLANKS, objective_id="m1"), "day")
        assert session.goal_end_reason(self._with_deaths(0)) == ""
        reason = session.goal_end_reason(self._with_deaths(1, met=True))
        assert "died and respawned" in reason
        # ブリッジを再起動すると数は 0 に戻る: 死んだとはみなさない
        assert session.goal_end_reason(self._with_deaths(0)) == ""

    @pytest.mark.asyncio
    async def test_after_a_death_gemini_decides_with_the_screen(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        text_generator.generate_json.return_value = DIED_PLANKS
        bridge.observe.return_value = self._with_deaths(0)
        session = _session(HAVE_PLANKS)
        session.observe(self._with_deaths(0))
        session.set_goal(Goal(HAVE_PLANKS, objective_id="m1"), "day")

        bridge.observe.return_value = self._with_deaths(1)
        report = await use_case.execute(session)

        assert report.goal_changed
        reason = prompt_builder.build_goal_prompt.call_args.kwargs["goal_ended_because"]
        assert "died and respawned" in reason
        assert text_generator.generate_json.call_args.kwargs["purpose"] == "goal_after_failure"
        # 死んだことは行き詰まった失敗ではないので、行動の記録はないが、分析と教訓は書かせる（設計書 105）
        assert prompt_builder.build_goal_prompt.call_args.kwargs["failure_record"] == ()
        assert prompt_builder.build_goal_prompt.call_args.kwargs["retry_allowed"] is False
        assert text_generator.generate_json.call_args.args[1] == goal_schema(after_failure=True)
        assert session.deaths_at_goal == 1  # 新しい小目標は、今の数から数える

    @pytest.mark.asyncio
    async def test_a_death_without_a_lesson_is_sent_back(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """死んだあとの決定は、原因の分析と教訓を書くまで差し戻される。"""
        text_generator.generate_json.return_value = PLANKS
        bridge.observe.return_value = self._with_deaths(0)
        session = _session(HAVE_PLANKS)
        session.observe(self._with_deaths(0))
        session.set_goal(Goal(HAVE_PLANKS, objective_id="m1"), "day")
        bridge.observe.return_value = self._with_deaths(1)
        with pytest.raises(TextGenerationError, match="diagnosis first"):
            await use_case.execute(session)

    @pytest.mark.asyncio
    async def test_the_death_report_is_shown_and_the_lesson_keeps_the_situation_before_the_death(
        self, bridge, text_generator, prompt_builder, selector, events, conversation, objectives,
        town, notes,
    ):
        from ailoveshen.application.use_cases.lessons import LessonBook

        store = Mock()
        store.load.return_value = None
        book = LessonBook(store, mode="rules")
        use_case = AdvancePlayUseCase(
            bridge=bridge, text_generator=text_generator, prompt_builder=prompt_builder,
            action_selector=selector, event_publisher=events, conversation=conversation,
            objectives=objectives, town=town, notes=notes, lessons=book,
        )
        text_generator.generate_json.return_value = {
            **DIED_PLANKS,
            "lesson": "夜に家の外で長い作業を続けず、暗くなる前に帰る",
            "lesson_when": {"text": "夕方から夜に外で作業しているとき"},
        }
        bridge.observe.return_value = self._with_deaths(0)
        session = _session(HAVE_PLANKS)
        session.observe(self._with_deaths(0))
        session.set_goal(Goal(HAVE_PLANKS, objective_id="m1"), "day")
        # 復活した後の様子は、昼の家の中。ブリッジの記録は、夜の外で死んだと言う
        died = self._with_deaths(1, time_phase="day")
        last_death = {
            "phase": "night", "place": "outside", "from_home_m": 48, "seconds": 38,
            "health_before": 20, "damage": {"zombie": 10, "skeleton": 3}, "hits": 6,
            "doing": ["level the ground", "the reflex"],
        }
        bridge.observe.return_value = replace(died, state={**died.state, "last_death": last_death})
        await use_case.execute(session)

        shown = prompt_builder.build_goal_prompt.call_args.kwargs["death_report"]
        assert "夜、家の外で死んだ" in shown and "家のドアから約 48 m" in shown
        assert "zombie 10" in shown and "level the ground → the reflex" in shown
        (lesson,) = book.lessons
        assert lesson.source == "death"
        assert lesson.keys["ended"] == "died"
        # 復活後の「昼・家の中・体力満タン」ではなく、死ぬ前の様子
        assert (lesson.keys["time"], lesson.keys["place"], lesson.keys["body"]) == ("night", "outside", "hurt")


    @pytest.mark.asyncio
    async def test_the_lesson_is_kept_even_if_the_goal_is_sent_back_and_written_once(
        self, bridge, text_generator, prompt_builder, selector, events, conversation, objectives,
        town, notes,
    ):
        from ailoveshen.application.use_cases.lessons import LessonBook

        store = Mock()
        store.load.return_value = None
        book = LessonBook(store, mode="rules")
        use_case = AdvancePlayUseCase(
            bridge=bridge, text_generator=text_generator, prompt_builder=prompt_builder,
            action_selector=selector, event_publisher=events, conversation=conversation,
            objectives=objectives, town=town, notes=notes, lessons=book,
        )
        lesson = {"lesson": "夜に家の外で長い作業を続けず、暗くなる前に帰る", "lesson_when": {"goal": "have", "place": "outside"}}
        not_offered = {**DIED_PLANKS, "predicate": "at_home", **lesson}
        text_generator.generate_json.side_effect = [not_offered] * 3  # 3 回とも差し戻される
        bridge.observe.return_value = self._with_deaths(0)
        session = _session(HAVE_PLANKS)
        session.observe(self._with_deaths(0))
        session.set_goal(Goal(HAVE_PLANKS, objective_id="m1"), "day")
        bridge.observe.return_value = self._with_deaths(1)
        with pytest.raises(TextGenerationError):
            await use_case.execute(session)

        assert text_generator.generate_json.call_count == 3
        (kept,) = book.lessons  # 目標は決まらなくても、書かせた教訓は残り、3 回書いても 1 件
        assert kept.source == "death" and kept.taught == 1


class TestLessons:
    """失敗から学んだ教訓を、似た状況の小目標の始まりに思い出す（docs/design/35）。"""

    conversation = TestAdvancePlay.conversation
    town = TestAdvancePlay.town
    note_store = TestAdvancePlay.note_store
    notes = TestAdvancePlay.notes

    @pytest.mark.asyncio
    async def test_a_lesson_from_a_failure_is_recalled_when_the_same_goal_starts(
        self, bridge, text_generator, prompt_builder, selector, events, conversation, objectives,
        town, notes,
    ):
        from ailoveshen.application.use_cases.lessons import LessonBook

        store = Mock()
        store.load.return_value = None
        book = LessonBook(store, mode="rules")
        use_case = AdvancePlayUseCase(
            bridge=bridge,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            action_selector=selector,
            event_publisher=events,
            conversation=conversation,
            objectives=objectives,
            town=town,
            notes=notes,
            lessons=book,
        )
        bridge.observe.return_value = _obs(time_phase="day")
        text_generator.generate_json.return_value = {
            **RETRY_PLANKS,
            "lesson": "木が見えないときは遠くまで探索する",
            "lesson_when": {"text": "近くに木がないとき"},
        }
        session = _session(HAVE_PLANKS)
        for _ in range(3):  # 進まないで終わる
            await use_case.execute(session)

        (lesson,) = book.lessons
        # 条件のキーがないので、行き詰まった小目標とそのときの状況
        assert lesson.keys["goal"] == "have" and lesson.keys["item"] == "planks"
        assert lesson.keys["ended"] == "stalled" and not lesson.explicit
        assert lesson.condition == "近くに木がないとき" and lesson.source == "failure"
        # やり直す同じ小目標の始まりに思い出し、選択器と「思い出したこと」に出る
        assert session.goal.lessons == ("木が見えないときは遠くまで探索する",)
        assert session.activity().lessons == (book.lessons[0],)
        assert book.lessons[0].used == 1

        bridge.observe.return_value = _obs(met=True, remaining=0)
        await use_case.execute(session)
        assert book.lessons[0].helped == 1  # 思い出して始めた小目標が済んだ
