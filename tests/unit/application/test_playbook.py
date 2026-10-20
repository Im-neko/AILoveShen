"""定石: 寝床 → 食料（畑）→ 建材（植林場）→ 資材（鉄）の順を、コードが守る（docs/design/74）。"""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases import objectives
from ailoveshen.application.use_cases.goal_vocabulary import ObjectiveProposal, PlanChange, PlanOp, parse_spec
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.plan_lint import aimless_jobs
from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.exceptions import FacilityNotDesignedError
from ailoveshen.domain.value_objects import (
    Activity,
    ConditionStatus,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    Job,
    Mission,
    ObjectiveState,
    PlaybookStep,
    StartingObjective,
    TownDefinition,
    TownSite,
    TownStage,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import format_activity

MISSION = Mission("生き延びながら家を建て、街にしていく")
BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
SWORD = GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1)
FARM = GoalSpec(GoalPredicate.FACILITY, name="wheat_farm_1")
WHEAT = GoalSpec(GoalPredicate.HAVE, item="wheat", count=3)
IRON_INGOT = GoalSpec(GoalPredicate.HAVE, item="iron_ingot", count=3)
PICKAXE = GoalSpec(GoalPredicate.HAVE, item="iron_pickaxe", count=1)
STEPS = (
    PlaybookStep("bed", "寝床", (BED,), "夜に寝る"),
    PlaybookStep("weapon", "武器", (SWORD,), "戦う"),
    PlaybookStep("food", "畑", (FARM, WHEAT), "食料"),
    PlaybookStep("iron", "鉄", (PICKAXE,), "資材"),
)


def _plan(steps=STEPS):
    return ObjectivePlan(mission=MISSION, playbook=steps)


def _obs(house=True, distance=10):
    state = {"home": {"door": {"x": 0, "z": distance}}, "self": {"position": {"x": 0, "z": 0}}}
    return GameObservation(
        state=state, candidates=(), health=20.0, food=20, has_home=house, house_complete=house
    )


def _keeper(met=(), observation=None):
    """met: 条件の describe() のうち、満たしているもの。"""
    bridge, facilities = AsyncMock(), AsyncMock()
    bridge.observe.return_value = observation or _obs()

    async def check(specs):
        return [ConditionStatus(sp, sp.describe() in met, (sp.describe(),), ()) for sp in specs]

    bridge.check.side_effect = check
    facilities.known.return_value = set()
    keeper = ObjectiveKeeper(
        bridge=bridge, event_publisher=AsyncMock(), store=Mock(), facilities=facilities
    )
    return keeper, bridge, facilities


@pytest.mark.asyncio
async def test_only_the_first_step_not_done_is_an_objective_at_the_top():
    keeper, _, _ = _keeper()
    plan = _plan()
    await keeper.judge(plan)
    assert [(g.title, g.playbook) for g in plan.pending] == [("寝床", "bed")]


@pytest.mark.asyncio
async def test_a_step_that_already_holds_is_done_without_an_objective_and_the_next_one_comes():
    keeper, _, _ = _keeper(met={BED.describe()})
    plan = _plan()
    await keeper.judge(plan)
    assert plan.playbook_done == ("bed",)
    assert [g.playbook for g in plan.pending] == ["weapon"]


@pytest.mark.asyncio
async def test_a_step_is_done_for_good_when_its_objective_completes():
    keeper, bridge, _ = _keeper()
    plan = _plan()
    await keeper.judge(plan)
    keeper_met, _, _ = _keeper(met={BED.describe()})
    keeper._bridge = keeper_met._bridge  # 世界が変わった: ベッドを置いた
    await keeper.judge(plan)
    assert plan.playbook_done == ("bed",)
    assert [g.playbook for g in plan.pending] == ["weapon"]
    # ベッドを壊しても、済んだ段は戻らない
    keeper2, _, _ = _keeper()
    await keeper2.judge(plan)
    assert plan.playbook_done == ("bed",)
    assert [g.playbook for g in plan.pending] == ["weapon"]


@pytest.mark.asyncio
async def test_other_objectives_go_behind_the_playbook_step_and_cannot_be_moved_before_it():
    keeper, _, _ = _keeper()
    plan = _plan()
    await keeper.judge(plan)
    other = plan.add("松明", (GoalSpec(GoalPredicate.HAVE, item="torch", count=8),), position=0)
    assert [g.title for g in plan.pending] == ["寝床", "松明"]  # 先頭に入れようとしても、後ろ
    later = plan.add("石", (GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=8),))
    with pytest.raises(ValueError, match="playbook"):
        plan.move(later.id, 0)
    plan.move(later.id, 1)
    assert [g.title for g in plan.pending] == ["寝床", "石", "松明"]
    with pytest.raises(ValueError, match="cannot be moved"):
        plan.move(plan.pending[0].id, 2)
    with pytest.raises(ValueError, match="cannot be dropped"):
        plan.drop(plan.pending[0].id, "later")
    assert other.state == ObjectiveState.PENDING


@pytest.mark.asyncio
async def test_a_plan_edit_that_moves_an_objective_before_the_playbook_is_sent_back_with_the_reason():
    keeper, _, _ = _keeper()
    plan = _plan()
    await keeper.judge(plan)
    other = plan.add("松明", (GoalSpec(GoalPredicate.HAVE, item="torch", count=8),))
    with pytest.raises(ValueError, match="playbook"):
        keeper.rehearse(plan, [PlanChange(PlanOp.MOVE, "r", objective_id=other.id, position=0)])


@pytest.mark.asyncio
async def test_a_viewers_now_request_waits_behind_the_playbook_step_but_goes_first_among_the_rest():
    keeper, _, _ = _keeper()
    plan = _plan()
    await keeper.judge(plan)
    plan.add("松明", (GoalSpec(GoalPredicate.HAVE, item="torch", count=8),))
    proposal = ObjectiveProposal(
        "石を集める", (GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=8),), "r", now=True
    )
    keeper._bridge.check.side_effect = None
    keeper._bridge.check.return_value = []
    await keeper.accept(plan, proposal, "viewer")
    assert [g.title for g in plan.pending] == ["寝床", "石を集める", "松明"]


@pytest.mark.asyncio
async def test_the_towns_stages_wait_for_the_playbook_step():
    stage = TownStage("備蓄", "r", conditions=(GoalSpec(GoalPredicate.STORED, item="food", count=16),))
    keeper, _, _ = _keeper()
    plan = _plan()
    plan.define_town(TownDefinition("街", (stage,)))
    plan.choose_site(TownSite("NE", 59, -345, "平ら", "Neo-Shenburg"))
    await keeper.judge(plan)
    assert [g.title for g in plan.pending] == ["寝床"]  # 街の段階は定石の段のあと
    keeper2, _, _ = _keeper(met={BED.describe(), SWORD.describe(), FARM.describe(), WHEAT.describe(), PICKAXE.describe()})
    plan.complete(plan.pending[0].id)
    await keeper2.judge(plan)
    assert plan.playbook_step is None
    assert [g.title for g in plan.pending] == ["備蓄"]


@pytest.mark.asyncio
async def test_a_facility_step_is_designed_first_and_may_hold_a_harvest_beside_the_facility():
    keeper, bridge, facilities = _keeper(met={BED.describe(), SWORD.describe()})
    plan = _plan()
    await keeper.judge(plan)
    assert [g.playbook for g in plan.pending] == ["food"]
    assert {c.args[0] for c in facilities.design.await_args_list} == {"wheat_farm_1"}
    assert [c.describe() for c in plan.pending[0].conditions] == ["facility(wheat_farm_1)", "have(wheat, 3)"]


@pytest.mark.asyncio
async def test_a_facility_step_waits_for_a_home_nearby_and_does_not_skip_ahead():
    for obs in (_obs(house=False), _obs(distance=500)):
        keeper, _, facilities = _keeper(met={BED.describe(), SWORD.describe()}, observation=obs)
        plan = _plan()
        await keeper.judge(plan)
        facilities.design.assert_not_awaited()
        assert plan.pending == ()  # 鉄の段へ飛ばさない


@pytest.mark.asyncio
async def test_a_step_that_cannot_be_designed_is_given_up_after_a_few_tries_and_the_next_comes():
    keeper, _, facilities = _keeper(met={BED.describe(), SWORD.describe()})
    facilities.design.side_effect = FacilityNotDesignedError("no room", name="x")
    plan = _plan()
    for _ in range(objectives.PLAYBOOK_TRIES + 1):
        keeper._playbook_at = 0.0
        await keeper.judge(plan)
    assert facilities.design.await_count == objectives.PLAYBOOK_TRIES
    assert [g.playbook for g in plan.pending] == ["iron"]


@pytest.mark.asyncio
async def test_iron_gathered_for_the_farms_bucket_is_a_job_of_the_step_and_is_not_sent_back():
    keeper, _, _ = _keeper(met={BED.describe(), SWORD.describe()})
    plan = _plan()
    await keeper.judge(plan)
    goal = plan.pending[0]
    job = Job(IRON_INGOT, "桶で畑の水")
    assert aimless_jobs([job], goal, trees={}) == ""
    assert aimless_jobs([job], goal, trees=None) == ""


def test_the_playbook_survives_a_save_and_a_restart(tmp_path):
    from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore

    store = JsonMissionStore(tmp_path / "mission.json")
    plan = _plan()
    plan.mark_playbook("bed")
    plan.add("武器", (SWORD,), position=0, playbook="weapon")
    store.save(plan)
    saved = store.load()
    assert saved is not None
    assert saved.playbook_done == ("bed",)
    assert [g.playbook for g in saved.pending] == ["weapon"]
    restored = _plan()
    restored.restore(list(saved.pending), list(saved.finished), saved.next_id, playbook_done=saved.playbook_done)
    assert restored.playbook_step is not None and restored.playbook_step.name == "weapon"


def test_the_prompt_shows_the_playbook_and_marks_its_objective():
    plan = _plan()
    plan.mark_playbook("bed")
    plan.add("武器", (SWORD,), position=0, playbook="weapon")
    text = format_activity(
        Activity(
            mission=MISSION,
            objectives=plan.pending,
            playbook=plan.playbook,
            playbook_done=plan.playbook_done,
        )
    )
    assert "[済] 寝床 → [今] 武器 → [先] 畑 → [先] 鉄" in text
    assert "[定石 weapon: 動かせない・やめられない]" in text


GROVE = GoalSpec(GoalPredicate.FACILITY, name="oak_grove_1")
LOGS = GoalSpec(GoalPredicate.HAVE, item="oak_log", count=16)
ASYNC_STEPS = (
    PlaybookStep("food", "畑", (FARM, WHEAT), "食料", ready=(FARM,)),
    PlaybookStep("lumber", "植林場", (GROVE, LOGS), "建材", ready=(GROVE,)),
    PlaybookStep("iron", "鉄", (PICKAXE,), "資材"),
)


def test_ready_must_be_some_of_the_conditions():
    with pytest.raises(ValueError, match="ready must be some of its conditions"):
        PlaybookStep("x", "x", (FARM,), ready=(GROVE,))


FARM_STARTED = GoalSpec(GoalPredicate.FACILITY, name="wheat_farm_1", started=True)


def test_ready_may_be_a_started_facility_of_a_condition_that_needs_all_of_it():
    step = PlaybookStep("food", "畑", (FARM, WHEAT), ready=(FARM_STARTED,))
    assert step.ready == (FARM_STARTED,)
    assert FARM_STARTED.to_dict() == {"predicate": "facility", "name": "wheat_farm_1", "started": True}
    assert FARM_STARTED.describe() != FARM.describe()
    assert "started" not in FARM.to_dict()
    assert parse_spec({"predicate": "facility", "name": "wheat_farm_1", "started": True}) == FARM_STARTED
    with pytest.raises(ValueError, match="ready must be some of its conditions"):
        PlaybookStep("x", "x", (FARM,), ready=(replace(GROVE, started=True),))
    with pytest.raises(ValueError, match="started is only for facility"):
        GoalSpec(GoalPredicate.HAVE, item="wheat", count=3, started=True)


@pytest.mark.asyncio
async def test_a_quarter_planted_farm_lets_the_next_step_start_while_the_rest_waits_for_harvested_seeds():
    steps = (
        PlaybookStep("food", "畑", (FARM, WHEAT), "食料", ready=(FARM_STARTED,)),
        PlaybookStep("lumber", "植林場", (GROVE, LOGS), "建材", ready=(GROVE,)),
    )
    keeper, _, _ = _keeper(met={FARM_STARTED.describe()})  # 全マスは植わっていないが、1/4 は植わった
    plan = _plan(steps)
    await keeper.judge(plan)
    assert [g.playbook for g in plan.pending] == ["lumber", "food"]  # 畑は育成中で、次の段が先


@pytest.mark.asyncio
async def test_while_the_crops_grow_the_next_step_is_worked_on_in_front_of_the_growing_one():
    keeper, _, facilities = _keeper(met={FARM.describe()})  # 畑はできた。小麦はまだ
    plan = _plan(ASYNC_STEPS)
    await keeper.judge(plan)
    assert [g.playbook for g in plan.pending] == ["lumber", "food"]  # 手を動かす段が先、育成中が後ろ
    assert {c.args[0] for c in facilities.design.await_args_list} == {"wheat_farm_1", "oak_grove_1"}


@pytest.mark.asyncio
async def test_only_two_steps_at_once_the_third_waits_until_both_are_done():
    keeper, _, _ = _keeper(met={FARM.describe(), GROVE.describe()})  # どちらも育成中
    plan = _plan(ASYNC_STEPS)
    await keeper.judge(plan)
    assert [g.playbook for g in plan.pending] == ["food", "lumber"]  # 育成中どうしは段の順
    assert plan.pending[0].playbook == "food" and all(g.playbook != "iron" for g in plan.pending)


@pytest.mark.asyncio
async def test_a_step_that_is_not_ready_blocks_the_next_one():
    keeper, _, _ = _keeper()  # 畑はまだできていない
    plan = _plan(ASYNC_STEPS)
    await keeper.judge(plan)
    assert [g.playbook for g in plan.pending] == ["food"]


@pytest.mark.asyncio
async def test_the_playbook_goes_on_in_order_when_the_growing_one_is_done():
    keeper, _, _ = _keeper(met={FARM.describe(), GROVE.describe()})
    plan = _plan(ASYNC_STEPS)
    await keeper.judge(plan)
    both = {FARM.describe(), WHEAT.describe(), GROVE.describe()}
    keeper2, _, _ = _keeper(met=both)
    await keeper2.judge(plan)  # 小麦をとった: 畑は済み、植林場が育成中のまま。鉄が 2 つ目として加わる
    assert plan.playbook_done == ("food",)
    assert [g.playbook for g in plan.pending] == ["iron", "lumber"]


FARM_ONLY = PlaybookStep("food", "畑", (FARM, WHEAT), "食料", ready=(FARM,))


def test_a_full_list_names_what_can_and_cannot_be_dropped():
    plan = ObjectivePlan(mission=MISSION, max_goals=3, playbook=(FARM_ONLY,))
    plan.add("畑", (FARM, WHEAT), position=0, playbook="food")
    plan.add("石", (GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=8),))
    plan.add("松明", (GoalSpec(GoalPredicate.HAVE, item="torch", count=8),))
    with pytest.raises(ValueError) as e:
        plan.add("木", (GoalSpec(GoalPredicate.HAVE, item="oak_log", count=8),))
    text = str(e.value)
    assert "can drop: [m2] 石; [m3] 松明" in text and "cannot drop (town / playbook): [m1] 畑" in text


@pytest.mark.asyncio
async def test_an_edit_that_adds_before_it_drops_is_applied_when_the_list_is_full():
    keeper, _, _ = _keeper()
    plan = ObjectivePlan(mission=MISSION, max_goals=3)
    plan.add("石", (GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=8),))
    plan.add("松明", (GoalSpec(GoalPredicate.HAVE, item="torch", count=8),))
    old = plan.add("木", (GoalSpec(GoalPredicate.HAVE, item="oak_log", count=8),))
    new = ObjectiveProposal("ツルハシ", (GoalSpec(GoalPredicate.HAVE, item="stone_pickaxe", count=1),), "r")
    changes = [
        PlanChange(PlanOp.ADD, "r", proposal=new),  # 書いた順は、足す・落とす
        PlanChange(PlanOp.DROP, "もう要らない", objective_id=old.id),
    ]
    keeper.rehearse(plan, changes)  # 断られない
    await keeper.commit(plan, changes)
    assert [g.title for g in plan.pending] == ["石", "松明", "ツルハシ"]
    # 落とすだけでは足りない（足す方が多い）なら、これまでどおり断る
    with pytest.raises(ValueError, match="full"):
        keeper.rehearse(plan, [PlanChange(PlanOp.ADD, "r", proposal=new), PlanChange(PlanOp.ADD, "r", proposal=ObjectiveProposal("砂", (GoalSpec(GoalPredicate.HAVE, item="sand", count=1),), "r"))])


@pytest.mark.asyncio
async def test_the_playbook_step_closes_the_own_objectives_it_covers_but_not_viewers_or_the_towns():
    keeper, _, _ = _keeper(met={BED.describe(), SWORD.describe()})
    plan = _plan()
    old_farm = plan.add("小麦畑を作る", (FARM,))
    mine = plan.add("石", (GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=8),))
    viewer = plan.add("畑を作って", (FARM,), requested_by="alice")
    advice = plan.add("畑どう？", (FARM,), suggested_by="bob")
    await keeper.judge(plan)
    ids = {g.id for g in plan.pending}
    assert old_farm.id not in ids  # 定石の畑（施設 + 小麦）に含まれる自分の大目標
    assert {mine.id, viewer.id, advice.id} <= ids
    assert [g.playbook for g in plan.pending][0] == "food"


@pytest.mark.asyncio
async def test_a_step_that_cannot_be_added_because_the_list_is_full_first_closes_the_old_objective_it_covers():
    keeper, _, _ = _keeper(met={BED.describe(), SWORD.describe()})
    plan = ObjectivePlan(mission=MISSION, max_goals=3, playbook=(PlaybookStep("food", "畑", (FARM, WHEAT), "食料"),))
    plan.add("小麦畑を作る", (FARM,))
    plan.add("石", (GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=8),))
    plan.add("松明", (GoalSpec(GoalPredicate.HAVE, item="torch", count=8),))
    await keeper.judge(plan)
    assert [g.title for g in plan.pending] == ["畑", "石", "松明"]


@pytest.mark.asyncio
async def test_a_kept_step_opens_again_when_what_it_gave_is_lost_but_others_stay_done():
    """武器（`keep`）は、死んで失ったらまた今の段になる。家・畑のように作ったものが残る段は戻さない。

    武器の段は済みのまま、剣なしで地下に出て死んだ（2026-10-01 19:44）。
    """
    steps = (
        PlaybookStep("bed", "寝床", (BED,), "夜に寝る"),
        PlaybookStep("weapon", "武器", (SWORD,), "戦う", keep=True),
        PlaybookStep("iron", "鉄", (PICKAXE,), "資材"),
    )
    plan = _plan(steps)
    keeper, _, _ = _keeper(met={BED.describe(), SWORD.describe()})
    await keeper.judge(plan)
    assert plan.playbook_done == ("bed", "weapon")
    assert [g.playbook for g in plan.pending] == ["iron"]
    # 死んで剣を失った（ベッドは残る）
    keeper2, _, _ = _keeper(met={BED.describe()})
    await keeper2.judge(plan)
    assert plan.playbook_done == ("bed",)
    assert [g.playbook for g in plan.pending][0] == "weapon"


@pytest.mark.asyncio
async def test_reset_puts_the_starting_objectives_back_and_forgets_the_playbook_and_viewers():
    keeper, _, _ = _keeper(met=(BED.describe(),))
    start = StartingObjective("家を建てる", (GoalSpec(GoalPredicate.BUILT),), "まず家")
    plan = ObjectivePlan(mission=MISSION, playbook=STEPS, initial=(start,))
    plan.add_initial()
    plan.add("焼き鳥を食べたい", (WHEAT,), requested_by="neko")
    await keeper.judge(plan)
    assert "bed" in plan.playbook_done
    old_ids = {g.id for g in plan.pending}

    await keeper.reset(plan)

    assert [g.title for g in plan.pending] == ["家を建てる"]
    assert plan.finished == () and plan.playbook_done == ()
    assert not old_ids & {g.id for g in plan.pending}  # id は戻さない
    keeper._store.save.assert_called_with(plan)
    published = [c.args[0] for c in keeper._event_publisher.publish.await_args_list]
    assert any(type(e).__name__ == "ObjectivesResetEvent" and e.count == 1 for e in published)


@pytest.mark.asyncio
async def test_reset_keeps_the_town_and_forgets_failed_playbook_steps():
    keeper, _, _ = _keeper()
    plan = ObjectivePlan(mission=MISSION, playbook=STEPS)
    town = TownDefinition("小さな村", (TownStage("家", "住む", conditions=(BED,)),))
    plan.define_town(town)
    keeper._playbook_failures["bed"] = 3
    await keeper.reset(plan)
    assert plan.town == town
    assert keeper._playbook_failures == {}


NEXT = Mission("鉄の装備で遠くを探索する")
ARMOR = PlaybookStep("armor", "鉄の防具", (GoalSpec(GoalPredicate.HAVE, item="iron_chestplate", count=1),), "守りを固める", keep=True)


DONE_TOWN = TownDefinition("街", (TownStage("備え", "夜に備える", (BED,)),))


def _finished_plan():
    """街が完成し、定石が全部済んだプラン。"""
    plan = _plan()
    plan.restore(
        [], [], 1, town=DONE_TOWN, town_stage=1, playbook_done=tuple(s.name for s in STEPS)
    )
    return plan


def test_the_mission_is_achieved_when_the_town_is_complete_and_every_step_is_done():
    plan = _plan()
    assert not plan.mission_achieved  # 街がまだない
    plan.restore([], [], 1, town=DONE_TOWN, town_stage=1, playbook_done=("bed", "weapon", "food"))
    assert not plan.mission_achieved  # 鉄が残っている
    assert _finished_plan().mission_achieved


def test_the_next_mission_adds_its_steps_after_the_old_ones_and_keeps_what_is_done():
    plan = _finished_plan()

    plan.advance_mission(NEXT, [ARMOR])

    assert plan.mission == NEXT and plan.origin == MISSION
    assert plan.missions_done == (MISSION.text,)
    assert [s.name for s in plan.playbook] == ["bed", "weapon", "food", "iron", "armor"]
    assert plan.playbook_step == ARMOR  # 今の段は、新しい最初の段
    assert plan.extra_playbook == (ARMOR,)
    assert not plan.mission_achieved  # 新しい段が残っている


def test_the_next_mission_is_refused_before_the_mission_is_achieved_or_with_a_used_name():
    with pytest.raises(ValueError, match="not achieved"):
        _plan().advance_mission(NEXT, [ARMOR])
    with pytest.raises(ValueError, match="already used"):
        _finished_plan().advance_mission(NEXT, [replace(ARMOR, name="iron")])
    with pytest.raises(ValueError, match="at least one"):
        _finished_plan().advance_mission(NEXT, [])


def test_reset_goes_back_to_the_mission_of_the_settings():
    plan = _finished_plan()
    plan.advance_mission(NEXT, [ARMOR])

    plan.reset()

    assert plan.mission == MISSION and plan.missions_done == ()
    assert plan.playbook == STEPS and plan.playbook_done == ()


def test_restore_puts_back_the_mission_and_the_steps_that_were_added():
    plan = _plan()

    plan.restore(
        [], [], 1, town=DONE_TOWN, town_stage=1, playbook_done=("bed",),
        mission=NEXT, missions_done=(MISSION.text,), extra_playbook=(ARMOR,),
    )

    assert plan.mission == NEXT and plan.origin == MISSION
    assert [s.name for s in plan.playbook][-1] == "armor"
    assert plan.missions_done == (MISSION.text,)


def test_the_goal_prompt_tells_the_missions_already_achieved():
    plan = _finished_plan()
    plan.advance_mission(NEXT, [ARMOR])

    text = format_activity(
        Activity(mission=plan.mission, missions_done=plan.missions_done, playbook=plan.playbook,
                 playbook_done=plan.playbook_done)
    )

    assert f"最終目標: {NEXT.text}" in text
    assert f"達成した最終目標: {MISSION.text}" in text


def test_the_goal_prompt_tells_what_was_tried_for_the_top_objective():
    tried = ("have(raw_iron): 進まず止まった 9 回", "explored(): 達成 1 回")

    text = format_activity(Activity(mission=MISSION, tried=tried))

    assert "これまでに選んだ小目標" in text and "have(raw_iron): 進まず止まった 9 回 / explored(): 達成 1 回" in text
    assert "これまでに選んだ小目標" not in format_activity(Activity(mission=MISSION))
