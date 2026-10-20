"""TownPlanner（街の場所と定義）と、ObjectiveKeeper が進める街の段階のテスト。"""

from unittest.mock import AsyncMock, Mock

import dataclasses

import pytest

from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.town import MOVE_CONDITIONS, TownPlanner
from ailoveshen.domain.entities import ObjectivePlan, PlaySession
from ailoveshen.domain.events import (
    HouseDesignedEvent,
    MissionAdvancedEvent,
    ObjectiveAddedEvent,
    ObjectiveCompletedEvent,
    TownCompletedEvent,
    TownDefinedEvent,
    TownSiteChosenEvent,
)
from ailoveshen.domain.exceptions import GameBridgeError, GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    ConditionStatus,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    HouseBlueprint,
    Mission,
    PlaybookStep,
    Side,
    TownDefinition,
    TownSite,
    TownStage,
)

MISSION = Mission("生き延びながら家を建て、街にしていく")
FOOD = GoalSpec(GoalPredicate.STORED, item="food", count=16)
SWORD = GoalSpec(GoalPredicate.HAVE, item="iron_sword", count=1)
LIT = GoalSpec(GoalPredicate.LIT, distance=16)

STOCK = {"title": "備蓄", "why": "冬に備える", "conditions": [FOOD.to_dict()], "unresolved": []}
SAFE = {"title": "敷地の安全", "why": "夜に備える", "conditions": [LIT.to_dict()], "unresolved": []}
BUILDINGS = {
    "title": "複数の建物",
    "why": "街らしく",
    "conditions": [],
    "unresolved": ["倉庫を建てる（2 軒目を建てる行動が要る）"],
}
TOWN = {"text": "安全で備えのある小さな街", "stages": [STOCK, SAFE, BUILDINGS]}
HERE = TownSite("here", 0, 0, "動物が多い", "ひだまり村")
EAST = TownSite("E", 96, 0, "地表の石が多い", "いしのまち")
SURVEYED = GoalSpec(GoalPredicate.SURVEYED, count=9)


def _row(site_id: str, x: int, z: int, **numbers) -> dict:
    return {
        "id": site_id,
        "x": x,
        "z": z,
        "distance": abs(x) + abs(z),
        "flat_plots": 10,
        "water_pct": 0,
        "steep_pct": 0,
        "stone": 0,
        "coal": 0,
        "iron": 0,
        "logs": 30,
        "lava": 0,
        "animals": 5,
        "loaded_pct": 100,
        **numbers,
    }


ROWS = [_row("here", 0, 0), _row("E", 96, 0, stone=40)]


def _obs(has_home=True, survey=None, build=None) -> GameObservation:
    """ブリッジの観測。survey: 調べた候補地、build: 建築のプランの状態。"""
    state = {"survey": survey, "build": build}
    return GameObservation(
        state=state, candidates=(), health=20.0, food=20, has_home=has_home, has_plan=True
    )


SURVEY_DONE = {"planned": 2, "sites": ROWS}


def _published(events, event_type):
    return [c.args[0] for c in events.publish.call_args_list if isinstance(c.args[0], event_type)]


def _check(met=frozenset(), impossible=frozenset(), rejected=frozenset()):
    """bridge.check: `met` は満たし、`impossible` は手に入らず、`rejected` は判定できない。"""

    async def check(specs):
        for s in specs:
            if s in rejected:
                raise GoalRejectedError(f"conditions rejected: {s.describe()}")
        return [
            ConditionStatus(
                s,
                s in met,
                (s.describe(),),
                ("no way to get iron_sword",) if s in impossible else (),
            )
            for s in specs
        ]

    return check


@pytest.fixture
def bridge():
    """
    ブリッジのモック。まだ何も満たしておらず、何でも手に入る。区画の地形は読めない（理想図は
    描かない。理想図は TestTownMap で確かめる）。
    """
    b = AsyncMock()
    b.check.side_effect = _check()
    b.zones.side_effect = GameBridgeError("no zones in this test")
    b.facilities.return_value = []
    return b


@pytest.fixture
def events():
    """イベント発行のモック。"""
    return AsyncMock()


@pytest.fixture
def store():
    """ミッションストアのモック。"""
    return Mock()


@pytest.fixture
def text_generator():
    """構造化出力つきの LLM のモック。"""
    return AsyncMock()


@pytest.fixture
def prompt_builder():
    """ゲームのプロンプトビルダーのモック。"""
    builder = Mock()
    builder.build_town_prompt.return_value = "town prompt"
    builder.build_stage_prompt.return_value = "stage prompt"
    builder.build_site_prompt.return_value = "site prompt"
    builder.describe_site.return_value = "site note"
    builder.vocabulary_version.return_value = "v1"
    return builder


@pytest.fixture
def keeper(bridge, events, store):
    """モックの上で動く本物の ObjectiveKeeper。"""
    return ObjectiveKeeper(bridge=bridge, event_publisher=events, store=store)


@pytest.fixture
def designer():
    """家の設計のモック。"""
    d = AsyncMock()
    d.design.return_value = HouseBlueprint("石の家", "c", 5, 5, 3, Side.NORTH, 2)
    return d


@pytest.fixture
def planner(text_generator, prompt_builder, bridge, events, store, keeper, designer):
    """モックの上で動く TownPlanner。"""
    bridge.observe.return_value = _obs(survey=SURVEY_DONE)
    return TownPlanner(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        bridge=bridge,
        event_publisher=events,
        character=CharacterProfile(),
        store=store,
        objectives=keeper,
        designer=designer,
    )


def _sited(site: TownSite = HERE) -> ObjectivePlan:
    plan = ObjectivePlan(mission=MISSION)
    plan.choose_site(site)
    return plan


def _town(*stages: TownStage) -> TownDefinition:
    return TownDefinition("小さな街", stages)


class TestTownPlanner:
    """TownPlanner のテスト。"""

    @pytest.mark.asyncio
    async def test_defined_once_saved_and_told(
        self, planner, text_generator, prompt_builder, events, store
    ):
        """場所を決めた計画には街ができ、保存して発行する。場所に合わせて定める。"""
        text_generator.generate_json.return_value = TOWN
        plan = _sited(EAST)

        await planner.prepare(plan)

        assert plan.town is not None
        call = prompt_builder.build_town_prompt.call_args
        assert call.args[2] == EAST and call.args[3]["stone"] == 40  # 場所と、そこの数字
        assert [s.title for s in plan.town.stages] == ["備蓄", "敷地の安全", "複数の建物"]
        assert plan.town.stages[0].conditions == (FOOD,)
        assert not plan.town.stages[2].ready
        assert plan.town.stages[2].tried_with == "v1"  # 今の語彙で書いた: 起動で書き直さない
        store.save.assert_called_with(plan)
        [told] = _published(events, TownDefinedEvent)
        assert told.stages == ("備蓄", "敷地の安全", "複数の建物")

    @pytest.mark.asyncio
    async def test_what_cannot_be_got_is_sent_back(
        self, planner, text_generator, prompt_builder, bridge
    ):
        """精錬の前の鉄の剣は、ブリッジの理由をつけて差し戻す。"""
        bridge.check.side_effect = _check(impossible={SWORD})
        with_sword = {**STOCK, "conditions": [FOOD.to_dict(), SWORD.to_dict()]}
        text_generator.generate_json.side_effect = [{**TOWN, "stages": [with_sword]}, TOWN]
        plan = _sited()

        await planner.prepare(plan)

        error = prompt_builder.build_town_prompt.call_args_list[1].kwargs["previous_error"]
        assert "備蓄" in error and "no way to get iron_sword" in error
        assert plan.town.stages[0].conditions == (FOOD,)

    @pytest.mark.asyncio
    async def test_still_wrong_after_the_last_attempt_is_left_unresolved(
        self, planner, text_generator, bridge
    ):
        """手に入らないものは条件にしない。未解決の部分で待つ。"""
        bridge.check.side_effect = _check(impossible={SWORD})
        with_sword = {**STOCK, "conditions": [FOOD.to_dict(), SWORD.to_dict()]}
        text_generator.generate_json.return_value = {**TOWN, "stages": [with_sword]}
        plan = _sited()

        await planner.prepare(plan)

        stage = plan.town.stages[0]
        assert stage.conditions == (FOOD,)
        assert "have(iron_sword, 1)" in stage.unresolved[0]
        assert text_generator.generate_json.await_count == 3

    @pytest.mark.asyncio
    async def test_a_condition_the_bridge_cannot_judge_is_dropped_not_waited_for(
        self, planner, text_generator, bridge
    ):
        """書き損じた条件を未解決で待たせると、能力が足されても段階が終わらない。捨てる。"""
        bridge.check.side_effect = _check(rejected={SWORD})
        with_sword = {**STOCK, "conditions": [FOOD.to_dict(), SWORD.to_dict()]}
        text_generator.generate_json.return_value = {**TOWN, "stages": [with_sword]}
        plan = _sited()

        await planner.prepare(plan)

        stage = plan.town.stages[0]
        assert stage.conditions == (FOOD,)
        assert stage.unresolved == ()
        assert stage.ready

    @pytest.mark.asyncio
    async def test_a_stage_with_nothing_the_bridge_can_judge_is_tried_again_later(
        self, planner, text_generator, bridge
    ):
        """判定できる条件がひとつも残らない段階は、定義を保存せず次の切れ目でやり直す。"""
        bridge.check.side_effect = _check(rejected={SWORD})
        only_sword = {**STOCK, "conditions": [SWORD.to_dict()]}
        text_generator.generate_json.return_value = {**TOWN, "stages": [only_sword]}
        plan = _sited()

        with pytest.raises(TextGenerationError, match="no condition the bridge can judge"):
            await planner.prepare(plan)

        assert plan.town is None

    @pytest.mark.asyncio
    async def test_malformed_every_time_raises(self, planner, text_generator):
        """定義が一度も解釈できなければ、開始を止める。"""
        text_generator.generate_json.return_value = {"text": "", "stages": []}

        with pytest.raises(TextGenerationError, match="no valid town definition"):
            await planner.prepare(_sited())

    @pytest.mark.asyncio
    async def test_no_town_before_the_site(self, planner, text_generator):
        """場所が決まるまでは街を定めない（街は場所に合わせる）。"""
        plan = ObjectivePlan(mission=MISSION)

        await planner.prepare(plan)

        assert plan.town is None
        text_generator.generate_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_defined_town_only_has_its_unresolved_stages_written_again(
        self, planner, text_generator, prompt_builder, events
    ):
        """定義は残し、今の段階の未解決の部分を書き直させる。"""
        text_generator.generate_json.return_value = {
            "conditions": [LIT.to_dict()],
            "unresolved": [],
        }
        done = TownStage("済んだ段階", "w", unresolved=("過去のこと",))
        waiting = TownStage("複数の建物", "街らしく", unresolved=("倉庫",))
        plan = ObjectivePlan(mission=MISSION)
        plan.restore([], [], 1, town=_town(done, waiting), town_stage=1, site=HERE)

        await planner.prepare(plan)

        assert plan.town.stages[0] == done
        assert plan.town.stages[1] == TownStage(
            "複数の建物", "街らしく", conditions=(LIT,), tried_with="v1"
        )
        assert prompt_builder.build_stage_prompt.call_args.args[1] == waiting
        assert not _published(events, TownDefinedEvent)

    @pytest.mark.asyncio
    async def test_a_rewrite_already_done_is_refused_and_the_stage_kept(
        self, planner, text_generator, prompt_builder, bridge
    ):
        """倉庫を最初の家と言い換えても（built() は満たす）、段階は終わらない。"""
        built = GoalSpec(GoalPredicate.BUILT)
        bridge.check.side_effect = _check(met={built})
        text_generator.generate_json.return_value = {
            "conditions": [built.to_dict()],
            "unresolved": [],
        }
        waiting = TownStage("複数の建物", "街らしく", unresolved=("倉庫",))
        plan = ObjectivePlan(mission=MISSION)
        plan.restore([], [], 1, town=_town(waiting), town_stage=0, site=HERE)

        await planner.prepare(plan)

        assert plan.town.stages[0] == TownStage(
            "複数の建物", "街らしく", unresolved=("倉庫",), tried_with="v1"
        )
        error = prompt_builder.build_stage_prompt.call_args.kwargs["previous_error"]
        assert "built() はもう全部そろっている" in error
        assert text_generator.generate_json.await_count == 3

    @pytest.mark.asyncio
    async def test_an_unresolved_condition_that_is_now_met_is_a_resolution_not_a_rephrasing(
        self, planner, text_generator, bridge
    ):
        """未解決に書いてあった `farmed(wheat, 9)` が、畑ができて満たされた（2026-10-19）: 言い換えとして差し戻さない。"""
        farmed = GoalSpec(GoalPredicate.FARMED, item="wheat", count=9)
        bridge.check.side_effect = _check(met={farmed})
        text_generator.generate_json.return_value = {"conditions": [farmed.to_dict()], "unresolved": []}
        stuck = TownStage(
            "畑", "食料", conditions=(LIT,), unresolved=(f"{farmed.describe()} は今できることでは手に入らない（no farm）",),
            tried_with="v0",
        )
        plan = ObjectivePlan(mission=MISSION)
        plan.restore([], [], 1, town=_town(stuck), town_stage=0, site=HERE)

        await planner.prepare(plan)

        assert plan.town.stages[0].conditions == (farmed,) and plan.town.stages[0].ready
        assert text_generator.generate_json.await_count == 1

    @pytest.mark.asyncio
    async def test_a_rewrite_that_adds_a_new_unresolved_part_is_refused(
        self, planner, text_generator, prompt_builder, bridge
    ):
        """元の未解決を条件に書けたのに、新しい未解決（食料が減らないこと）を足すと、段階が終わらなくなる（2026-10-19）。"""
        farmed = GoalSpec(GoalPredicate.FARMED, item="wheat", count=9)
        bridge.check.side_effect = _check(met={farmed})
        text_generator.generate_json.side_effect = [
            {"conditions": [farmed.to_dict()], "unresolved": ["食料が減らないことを判定する条件がない"]},
            {"conditions": [farmed.to_dict()], "unresolved": []},
        ]
        stuck = TownStage(
            "畑", "食料", unresolved=(f"{farmed.describe()} は今できることでは手に入らない（no farm）",), tried_with="v0"
        )
        plan = ObjectivePlan(mission=MISSION)
        plan.restore([], [], 1, town=_town(stuck), town_stage=0, site=HERE)

        await planner.prepare(plan)

        assert plan.town.stages[0].unresolved == () and plan.town.stages[0].conditions == (farmed,)
        error = prompt_builder.build_stage_prompt.call_args.kwargs["previous_error"]
        assert "新しいことを足すと段階が終わらなくなる" in error
        assert text_generator.generate_json.await_count == 2

    @pytest.mark.asyncio
    async def test_a_stage_whose_conditions_are_all_met_is_written_again_once(
        self, planner, text_generator, prompt_builder
    ):
        """条件がそろっても未解決が残る段階は、そろった時点で一度書き直す（施設のほかの理由で手に入るようになったかもしれない）。"""
        text_generator.generate_json.return_value = {"conditions": [LIT.to_dict()], "unresolved": ["柵"]}
        stuck = TownStage("植林場", "木", conditions=(LIT,), unresolved=("柵",), tried_with="v1")
        plan = ObjectivePlan(mission=MISSION)
        plan.restore([], [], 1, town=_town(stuck), town_stage=0, site=HERE)

        await planner.prepare(plan)
        text_generator.generate_json.assert_not_awaited()

        plan.restore([], [], 1, town=_town(stuck), town_stage=0, site=HERE, stage_met=(LIT,))
        await planner.prepare(plan)
        assert text_generator.generate_json.await_count == 1
        await planner.prepare(plan)
        assert text_generator.generate_json.await_count == 1

    @pytest.mark.asyncio
    async def test_a_part_no_predicate_can_say_becomes_a_custom_check_written_later(
        self, planner, text_generator, bridge
    ):
        """柵で囲む（述語がない）を `custom(名前)` で書けば、判定が書かれる前でも保存でき、未解決から外れる（設計書 199 §5）。"""
        fenced = GoalSpec(GoalPredicate.CUSTOM, name="farm_fenced")
        bridge.conditions.return_value = []  # まだ判定は書かれていない
        ok = _check()

        async def check(specs):
            if fenced in specs:
                raise GoalRejectedError("no check named farm_fenced")  # 確かめれば拒否される
            return await ok(specs)

        bridge.check.side_effect = check
        text_generator.generate_json.return_value = {
            "conditions": [LIT.to_dict(), fenced.to_dict()],
            "unresolved": [],
        }
        stuck = TownStage("植林場", "木", conditions=(LIT,), unresolved=("畑を柵で囲むこと",), tried_with="v0")
        plan = ObjectivePlan(mission=MISSION)
        plan.restore([], [], 1, town=_town(stuck), town_stage=0, site=HERE)

        await planner.prepare(plan)

        assert plan.town.stages[0].conditions == (LIT, fenced) and plan.town.stages[0].ready
        assert text_generator.generate_json.await_count == 1

    @pytest.mark.asyncio
    async def test_only_the_current_stage_and_only_when_the_vocabulary_changed(
        self, planner, text_generator, prompt_builder
    ):
        """起動のたびに 5 段階を書き直していた（2026-09-27）: 同じ語彙では書き直さない。"""
        text_generator.generate_json.return_value = {"conditions": [], "unresolved": ["柵"]}
        now = TownStage("防壁", "守る", unresolved=("柵",), tried_with="v1")
        later = TownStage("広場", "街らしく", unresolved=("舗装",), tried_with="v0")
        plan = ObjectivePlan(mission=MISSION)
        plan.restore([], [], 1, town=_town(now, later), town_stage=0, site=HERE)

        await planner.prepare(plan)
        text_generator.generate_json.assert_not_awaited()  # 今の段階は同じ語彙で試した

        prompt_builder.vocabulary_version.return_value = "v2"  # 条件が増えた
        await planner.prepare(plan)
        assert prompt_builder.build_stage_prompt.call_args.args[1] == now
        assert plan.town.stages[0].tried_with == "v2"
        assert plan.town.stages[1] == later  # 先の段階は、今になってから
        text_generator.generate_json.reset_mock()
        await planner.prepare(plan)
        text_generator.generate_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_finished_facility_makes_the_current_stage_be_written_again_once(
        self, planner, text_generator, prompt_builder, bridge
    ):
        """畑ができても未解決の `farmed(...)` が残って段階が終わらなかった（2026-10-19）: 完成した施設が増えたら一度だけ書き直す。"""
        farmed = GoalSpec(GoalPredicate.FARMED, item="wheat", count=9)
        text_generator.generate_json.return_value = {"conditions": [farmed.to_dict()], "unresolved": []}
        stuck = TownStage("畑", "食料", conditions=(LIT,), unresolved=("farmed(wheat, 9)",), tried_with="v1")
        plan = ObjectivePlan(mission=MISSION)
        plan.restore([], [], 1, town=_town(stuck), town_stage=0, site=HERE)

        await planner.prepare(plan)
        text_generator.generate_json.assert_not_awaited()  # 施設がないあいだは、語彙が同じなら書き直さない

        bridge.facilities.return_value = [
            {"name": "wheat_farm", "kind": "farm", "status": "done"},
            {"name": "mine_1", "kind": "mine", "status": "building"},
        ]
        await planner.prepare(plan)
        assert prompt_builder.build_stage_prompt.call_args.args[1] == stuck
        assert plan.town.stages[0].conditions == (farmed,)
        assert plan.town.stages[0].ready and plan.town.stages[0].tried_with.startswith("v1+")

        text_generator.generate_json.reset_mock()
        plan.define_town(_town(dataclasses.replace(plan.town.stages[0], unresolved=("まだ",))))
        await planner.prepare(plan)
        text_generator.generate_json.assert_not_awaited()  # 同じ施設の集まりでは、もう書き直さない


class TestTownStages:
    """大目標としての街の段階のテスト（ObjectiveKeeper.judge）。"""

    def _plan(self, *stages: TownStage) -> ObjectivePlan:
        plan = _sited()
        plan.add("羊毛を集める", (GoalSpec(GoalPredicate.HAVE, item="wool", count=3),))
        plan.define_town(_town(*stages))
        return plan

    @pytest.mark.asyncio
    async def test_the_ready_stage_goes_on_top(self, keeper, events):
        """取り組む段階が一番上の大目標になり、配信者自身の大目標と同じように言う。"""
        plan = self._plan(TownStage("備蓄", "冬に備える", conditions=(FOOD,)))

        await keeper.judge(plan)

        top = plan.current
        assert (top.title, top.conditions, top.reason, top.stage) == (
            "備蓄",
            (FOOD,),
            "冬に備える",
            0,
        )
        [added] = _published(events, ObjectiveAddedEvent)
        assert added.position == 1 and not added.requested_by

        await keeper.judge(plan)
        assert sum(g.stage == 0 for g in plan.pending) == 1

    @pytest.mark.asyncio
    async def test_a_stage_with_unresolved_parts_waits(self, keeper):
        """まだできない段階は大目標にしない。"""
        plan = self._plan(TownStage("複数の建物", "街らしく", unresolved=("倉庫",)))

        await keeper.judge(plan)

        assert [g.title for g in plan.pending] == ["羊毛を集める"]

    @pytest.mark.asyncio
    async def test_a_done_stage_moves_the_town_on_and_the_last_completes_it(
        self, keeper, bridge, events
    ):
        """段階が終わると次の段階が入り、街の完成を言う。"""
        plan = self._plan(
            TownStage("備蓄", "冬に備える", conditions=(FOOD,)),
            TownStage("敷地の安全", "夜に備える", conditions=(LIT,)),
        )
        await keeper.judge(plan)

        bridge.check.side_effect = _check(met={FOOD})
        await keeper.judge(plan)
        assert plan.town_stage == 1
        assert plan.current.title == "敷地の安全" and plan.current.stage == 1
        assert not _published(events, TownCompletedEvent)

        bridge.check.side_effect = _check(met={FOOD, LIT})
        await keeper.judge(plan)
        assert plan.town_complete
        assert [e.title for e in _published(events, ObjectiveCompletedEvent)] == [
            "備蓄",
            "敷地の安全",
        ]
        [done] = _published(events, TownCompletedEvent)
        assert done.text == "小さな街"
        assert [g.title for g in plan.pending] == ["羊毛を集める"]

    @pytest.mark.asyncio
    async def test_what_can_be_done_goes_first_the_rest_waits_for_the_ability(
        self, keeper, bridge, events
    ):
        """town1: どの段階にも未解決の部分があった。できる部分は進める。"""
        fence = TownStage("安全", "夜に備える", conditions=(LIT, FOOD), unresolved=("柵",))
        plan = self._plan(fence, TownStage("備蓄", "冬", conditions=(SWORD,)))

        await keeper.judge(plan)
        assert plan.current.conditions == (LIT, FOOD) and plan.current.stage == 0

        bridge.check.side_effect = _check(met={LIT, FOOD})
        await keeper.judge(plan)
        assert plan.town_stage == 0 and plan.stage_met == (LIT, FOOD)
        assert plan.stage_goal() is None  # 柵ができるまで、することはない
        assert [g.title for g in plan.pending] == ["羊毛を集める"]

        # 能力ができて柵を書き直したら、柵だけを求める
        fenced = GoalSpec(GoalPredicate.HAVE, item="oak_fence", count=16)
        plan.define_town(
            _town(TownStage("安全", "夜に備える", (LIT, FOOD, fenced)), plan.town.stages[1])
        )
        await keeper.judge(plan)
        assert plan.current.conditions == (fenced,)

        bridge.check.side_effect = _check(met={LIT, FOOD, fenced})
        await keeper.judge(plan)
        assert plan.town_stage == 1 and plan.stage_met == ()
        assert plan.current.conditions == (SWORD,) and plan.current.stage == 1
        assert not _published(events, TownCompletedEvent)

    @pytest.mark.asyncio
    async def test_a_stage_is_not_dropped_when_its_conditions_are_rejected(self, keeper, bridge):
        """ブリッジが判定できない段階も残る（落とさない）。"""
        plan = self._plan(TownStage("備蓄", "冬に備える", conditions=(FOOD,)))
        await keeper.judge(plan)

        bridge.check.side_effect = _check(rejected={FOOD})
        await keeper.judge(plan)

        assert plan.current.stage == 0

    @pytest.mark.asyncio
    async def test_a_full_list_keeps_the_stage_waiting(self, keeper):
        """段階は上限を破らず、リストが空くまで待つ。"""
        plan = self._plan(TownStage("備蓄", "冬に備える", conditions=(FOOD,)))
        for i in range(plan.max_goals - 1):
            plan.add(f"g{i}", (GoalSpec(GoalPredicate.HAVE, item="log", count=i + 1),))

        await keeper.judge(plan)

        assert plan.stage_goal() is None


class TestTownSite:
    """街の場所: 調査 → 選択 → 引っ越し → 定義（TownPlanner.advance）。"""

    def _session(self, plan: ObjectivePlan) -> PlaySession:
        return PlaySession(blueprint=None, plan=plan)

    @pytest.mark.asyncio
    async def test_the_survey_waits_for_the_first_home(self, planner, events):
        """最初の家ができてから、候補地を調べる大目標を 1 つだけ足す。"""
        plan = ObjectivePlan(mission=MISSION)
        session = self._session(plan)

        await planner.advance(session, _obs(has_home=False))
        assert plan.pending == ()

        await planner.advance(session, _obs())
        [survey] = plan.pending
        assert survey.conditions == (SURVEYED,) and survey.prepares_town
        assert [e.title for e in _published(events, ObjectiveAddedEvent)] == ["街の場所を探す"]

        await planner.advance(session, _obs(survey={"planned": 9, "sites": ROWS}))
        assert len(plan.pending) == 1

    @pytest.mark.asyncio
    async def test_no_stage_before_the_site_or_during_the_move(self, keeper):
        """段階の条件は家を基準にする: 場所が決まり、引っ越すまでは段階を足さない。"""
        plan = ObjectivePlan(mission=MISSION)
        plan.define_town(_town(TownStage("備蓄", "冬に備える", conditions=(FOOD,))))
        await keeper.judge(plan)
        assert plan.stage_goal() is None

        plan.choose_site(EAST)
        await keeper.add_town_goal(plan, "引っ越す", MOVE_CONDITIONS, "石が多い", position=0)
        await keeper.judge(plan)
        assert plan.stage_goal() is None

    @pytest.mark.asyncio
    async def test_a_town_goal_cannot_be_dropped(self, keeper):
        """調査と引っ越しは、段階と同じくやめられない。"""
        plan = ObjectivePlan(mission=MISSION)
        goal = await keeper.add_town_goal(plan, "街の場所を探す", (SURVEYED,), "場所を決める")

        with pytest.raises(ValueError, match="prepares the town"):
            plan.drop(goal.id, "面倒")

    @pytest.mark.asyncio
    async def test_the_site_is_chosen_from_the_surveyed_numbers(
        self, planner, text_generator, prompt_builder, events, store, designer, bridge
    ):
        """調べ終えたら表から選ぶ。表にない場所は差し戻す。最初の家の場所なら引っ越さない。"""
        text_generator.generate_json.side_effect = [
            {"site_id": "NW", "reason": "r", "town_name": "n"},
            {"site_id": "here", "reason": "動物が多い", "town_name": "ひだまり村"},
            TOWN,
        ]
        plan = ObjectivePlan(mission=MISSION)

        await planner.advance(self._session(plan), _obs(survey=SURVEY_DONE))

        assert prompt_builder.build_site_prompt.call_args.args[2] == ROWS
        error = prompt_builder.build_site_prompt.call_args.kwargs["previous_error"]
        assert "NW is not one of the surveyed sites" in error
        assert plan.site == HERE
        [chosen] = _published(events, TownSiteChosenEvent)
        assert (chosen.name, chosen.moving) == ("ひだまり村", False)
        designer.design.assert_not_called()
        bridge.set_build_plan.assert_not_called()
        assert plan.town is not None  # 場所を決めたら街を定める
        assert _published(events, TownDefinedEvent)
        store.save.assert_called_with(plan)

    @pytest.mark.asyncio
    async def test_another_site_means_a_new_house_and_a_move(
        self, planner, text_generator, events, designer, bridge
    ):
        """別の場所なら、そこに建てる家を設計して送り、引っ越しを一番上の大目標にする。"""
        text_generator.generate_json.side_effect = [
            {"site_id": "E", "reason": "地表の石が多い", "town_name": "いしのまち"},
            TOWN,
        ]
        plan = ObjectivePlan(mission=MISSION)
        plan.add("食料を蓄える", (GoalSpec(GoalPredicate.HAVE, item="food", count=8),))
        session = self._session(plan)
        session.completion_announced = True

        await planner.advance(session, _obs(survey=SURVEY_DONE, build={"site": None}))

        assert plan.site == EAST
        assert designer.design.call_args.kwargs["site_note"] == "site note"
        blueprint = designer.design.return_value
        bridge.set_build_plan.assert_awaited_once_with(blueprint, EAST)
        assert session.blueprint == blueprint and not session.completion_announced
        assert _published(events, HouseDesignedEvent)[0].name == "石の家"
        move = plan.current
        assert (move.title, move.conditions, move.prepares_town) == (
            "いしのまちに引っ越す",
            MOVE_CONDITIONS,
            True,
        )
        assert plan.town is not None

    @pytest.mark.asyncio
    async def test_the_move_resumes_after_a_restart(self, planner, designer, bridge, keeper):
        """場所を決めた後の再起動: 送った計画は送り直さず、足りないものだけを足す。"""
        plan = _sited(EAST)
        plan.define_town(_town(TownStage("備蓄", "冬に備える", conditions=(FOOD,))))
        session = self._session(plan)
        sent = {"site": {"x": 96, "z": 0}, "complete": False}

        # 計画は送ったが、引っ越しの大目標を足す前に止まった
        await planner.advance(session, _obs(build=sent))
        designer.design.assert_not_called()
        assert plan.current.title == "いしのまちに引っ越す"

        await planner.advance(session, _obs(build=sent))
        assert len(plan.pending) == 1

        # 計画をまだ送っていなかった
        plan2 = _sited(EAST)
        plan2.define_town(plan.town)
        await planner.advance(self._session(plan2), _obs(build={"site": None}))
        bridge.set_build_plan.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failed_choice_waits_for_the_next_goal_change(self, planner, text_generator):
        """使える選択が出なければ、代わりの場所で進めず、次の切れ目でやり直す。"""
        text_generator.generate_json.return_value = {"site_id": "NW", "reason": "", "town_name": ""}
        plan = ObjectivePlan(mission=MISSION)

        await planner.advance(self._session(plan), _obs(survey=SURVEY_DONE))

        assert plan.site is None and plan.town is None


ARMOR = GoalSpec(GoalPredicate.HAVE, item="iron_chestplate", count=1)
MINE = GoalSpec(GoalPredicate.FACILITY, name="iron_mine_1")
NEXT_MISSION = {
    "text": "鉄の装備で遠くを探索する",
    "reason": "街が整ったので、外の世界を見に行く",
    "steps": [
        {"name": "armor", "title": "鉄の防具をそろえる", "conditions": [ARMOR.to_dict()],
         "reason": "守りを固める", "keep": True},
        {"name": "mine", "title": "鉄の採掘場を作る", "conditions": [MINE.to_dict()],
         "reason": "鉄を安定して手に入れる", "keep": False},
    ],
}


def _finished() -> ObjectivePlan:
    """街が完成し、定石の段がない（全部済み）プラン。"""
    plan = _sited()
    plan.define_town(_town(TownStage("備蓄", "備える", (FOOD,))))
    plan.restore([], [], 1, town=plan.town, town_stage=1, site=HERE)
    return plan


class TestNextMission:
    """最終目標を達成したら、次の最終目標とその定石を LLM に書かせる（docs/design/180）。"""

    def _session(self, plan: ObjectivePlan) -> PlaySession:
        return PlaySession(blueprint=None, plan=plan)

    @pytest.mark.asyncio
    async def test_the_next_mission_and_its_steps_are_written_checked_saved_and_told(
        self, planner, text_generator, prompt_builder, bridge, events, store
    ):
        text_generator.generate_json.return_value = NEXT_MISSION
        plan = _finished()
        obs = GameObservation(
            state={"inventory": {"cobblestone": 256}}, candidates=(), health=20.0, food=20,
            has_home=True, day=244, titles=(("storehouse_3", "蔵"),),
        )

        await planner.advance(self._session(plan), obs)

        assert plan.mission == Mission("鉄の装備で遠くを探索する")
        assert plan.missions_done == (MISSION.text,)
        assert [s.name for s in plan.playbook] == ["armor", "mine"]
        assert plan.playbook[0].keep and not plan.playbook[1].keep
        facts = prompt_builder.build_mission_prompt.call_args.args[5]
        assert facts == {"day": 244, "inventory": {"cobblestone": 256}, "builds": ["storehouse_3"]}
        [told] = _published(events, MissionAdvancedEvent)
        assert (told.done, told.text) == (MISSION.text, "鉄の装備で遠くを探索する")
        assert told.steps == ("鉄の防具をそろえる", "鉄の採掘場を作る")
        store.save.assert_called_with(plan)
        # 新しい施設は、足すときに設計してから判定する: ここでは確かめない
        checked = [s for c in bridge.check.call_args_list for s in c.args[0]]
        assert ARMOR in checked and MINE not in checked

    @pytest.mark.asyncio
    async def test_a_step_already_met_is_sent_back_unless_it_is_kept(
        self, planner, text_generator, prompt_builder, bridge
    ):
        """すでにある建物だけの段は、発展にならない（蔵を並べた空回り。設計書 180、200）。持ち続ける段は、満たしていてよい。"""
        tower = GoalSpec(GoalPredicate.BUILT, name="tower")
        bridge.check.side_effect = _check(met={tower})
        old = {**NEXT_MISSION, "steps": [
            {"name": "armor", "title": "鉄の防具をそろえる", "conditions": [ARMOR.to_dict()], "reason": "守る", "keep": True},
            {"name": "tower", "title": "見張りの塔を建てる", "conditions": [tower.to_dict()], "reason": "街を見渡す", "keep": False},
        ]}
        text_generator.generate_json.side_effect = [old, NEXT_MISSION]
        plan = _finished()

        await planner.advance(self._session(plan), _obs())

        error = prompt_builder.build_mission_prompt.call_args.kwargs["previous_error"]
        assert "見張りの塔を建てる" in error and "もう満たされている" in error
        assert [s.name for s in plan.playbook] == ["armor", "mine"]

    @pytest.mark.asyncio
    async def test_nothing_is_asked_before_the_mission_is_achieved(
        self, planner, text_generator, prompt_builder
    ):
        plan = _sited()  # 街がまだない
        plan.define_town(_town(TownStage("備蓄", "備える", (FOOD,))))

        await planner.advance(self._session(plan), _obs())

        prompt_builder.build_mission_prompt.assert_not_called()
        assert plan.mission == MISSION

    @pytest.mark.asyncio
    async def test_a_step_that_cannot_be_got_is_sent_back(
        self, planner, text_generator, prompt_builder, bridge
    ):
        bridge.check.side_effect = _check(impossible={ARMOR})
        fixed = {**NEXT_MISSION, "steps": [{**NEXT_MISSION["steps"][0], "conditions": [SWORD.to_dict()]}]}
        text_generator.generate_json.side_effect = [NEXT_MISSION, fixed]
        plan = _finished()

        await planner.advance(self._session(plan), _obs())

        error = prompt_builder.build_mission_prompt.call_args.kwargs["previous_error"]
        assert "iron_chestplate" in error and "鉄の防具をそろえる" in error
        assert [s.name for s in plan.playbook] == ["armor"]

    @pytest.mark.asyncio
    async def test_steps_that_are_all_done_already_are_sent_back(
        self, planner, text_generator, prompt_builder, bridge
    ):
        only_armor = {**NEXT_MISSION, "steps": NEXT_MISSION["steps"][:1]}
        bridge.check.side_effect = _check(met={ARMOR})
        text_generator.generate_json.return_value = only_armor
        plan = _finished()

        await planner.advance(self._session(plan), _obs())

        assert "もう満たされている" in prompt_builder.build_mission_prompt.call_args.kwargs["previous_error"]
        assert plan.mission == MISSION  # 3 回とも差し戻した

    @pytest.mark.asyncio
    async def test_a_name_already_used_is_sent_back(self, planner, text_generator, prompt_builder):
        used = {**NEXT_MISSION, "steps": [{**NEXT_MISSION["steps"][0], "name": "armor"}]}
        plan = _finished()
        plan.playbook = (PlaybookStep("armor", "古い防具", (ARMOR,)),)
        plan.restore([], [], 1, town=plan.town, town_stage=1, site=HERE, playbook_done=("armor",),
                     extra_playbook=(PlaybookStep("armor", "古い防具", (ARMOR,)),))
        text_generator.generate_json.return_value = used

        await planner.advance(self._session(plan), _obs())

        assert "already used" in prompt_builder.build_mission_prompt.call_args.kwargs["previous_error"]

    @pytest.mark.asyncio
    async def test_after_a_failure_it_waits_before_asking_again(
        self, planner, text_generator, prompt_builder
    ):
        text_generator.generate_json.return_value = {"text": "", "reason": "", "steps": []}
        plan = _finished()

        await planner.advance(self._session(plan), _obs())
        calls = text_generator.generate_json.call_count
        await planner.advance(self._session(plan), _obs())

        assert calls == 3 and text_generator.generate_json.call_count == 3
        assert plan.mission == MISSION and plan.missions_done == ()
