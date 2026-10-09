"""もう持っている施設で大目標を済みにする（docs/design/142）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.facility_satisfy import check_satisfy, describe_memory
from ailoveshen.application.use_cases.goal_vocabulary import PlanChange, PlanOp, parse_decision
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase
from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.events import ObjectiveCompletedEvent
from ailoveshen.domain.exceptions import GoalRejectedError
from ailoveshen.domain.value_objects import (
    ConditionStatus,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    Mission,
    PlaybookStep,
)

MISSION = Mission("生き延びながら家を建て、街にしていく")
FARM = GoalSpec(GoalPredicate.FACILITY, name="wheat_farm_1")
WHEAT = GoalSpec(GoalPredicate.HAVE, item="wheat", count=3)
PICKAXE = GoalSpec(GoalPredicate.HAVE, item="iron_pickaxe", count=1)


def _home(x=0, z=0):
    return {"name": "home", "kind": "home", "status": "done", "center": {"x": x, "y": 70, "z": z}}


def _farm(name, x, z, status="done", crop="wheat", kind="farm", cells=(237, 237)):
    return {
        "name": name,
        "kind": kind,
        "status": status,
        "center": {"x": x, "y": 70, "z": z},
        "contents": {"crop": crop, "cells": cells[1], "done": cells[0]},
    }


def _statuses(facility_met=False, others_met=True):
    return [ConditionStatus(FARM, facility_met), ConditionStatus(WHEAT, others_met)]


FACILITIES = [_home(), _farm("wheat_farm_1", 30, 0, "planned", cells=(0, 59)), _farm("old_farm", 40, 10)]


def test_a_finished_farm_of_the_same_crop_near_the_home_stands_in():
    check_satisfy(_statuses(), "old_farm", FACILITIES)


@pytest.mark.parametrize(
    "facilities, by, message",
    [
        (FACILITIES, "nothing", "no facility named nothing"),
        (FACILITIES, "home", "no facility named home"),
        (FACILITIES, "wheat_farm_1", "is the facility the objective asks for"),
        ([*FACILITIES[:2], _farm("old_farm", 40, 10, "building", cells=(100, 237))], "old_farm", "not finished yet"),
        ([*FACILITIES[:2], _farm("old_farm", 400, 10)], "old_farm", "too far to serve it"),
        ([*FACILITIES[:2], _farm("old_farm", 40, 10, crop="carrots")], "old_farm", "grows carrots but"),
        ([*FACILITIES[:2], _farm("old_farm", 40, 10, kind="tree_farm", crop=None)], "old_farm", "is a tree_farm but"),
        ([FACILITIES[1], FACILITIES[2]], "old_farm", "no home yet"),
    ],
)
def test_a_facility_that_does_not_fit_is_sent_back_with_the_reason(facilities, by, message):
    with pytest.raises(GoalRejectedError, match=message):
        check_satisfy(_statuses(), by, facilities)


def test_the_other_conditions_are_still_judged_by_the_world():
    with pytest.raises(GoalRejectedError, match=r"every other condition met.*have\(wheat"):
        check_satisfy(_statuses(others_met=False), "old_farm", FACILITIES)


def test_one_facility_at_a_time_and_nothing_to_do_when_the_world_already_says_so():
    two = [ConditionStatus(FARM, False), ConditionStatus(GoalSpec(GoalPredicate.FACILITY, name="b"), False)]
    with pytest.raises(GoalRejectedError, match="one facility at a time"):
        check_satisfy(two, "old_farm", FACILITIES)
    check_satisfy(_statuses(facility_met=True), "nothing", FACILITIES)


def test_the_memory_lists_facilities_with_the_distance_from_the_home():
    text = describe_memory([*FACILITIES, _farm("far_farm", 300, 0)])
    assert "- old_farm: farm（wheat） 完成、家から 41m（家の近く）" in text
    assert "- wheat_farm_1: farm（wheat） planned 0/59、家から 30m（家の近く）" in text
    assert "- far_farm: farm（wheat） 完成、家から 300m（家の近くではない）" in text
    assert "home" not in text


def test_satisfy_needs_by_and_only_satisfy_takes_it():
    base = {"serves": "current", "predicate": "at_home", "reason": "r"}
    ok = parse_decision({**base, "plan_changes": [{"op": "satisfy", "id": "m1", "by": "old_farm", "reason": "r"}]})
    assert ok.changes[0].op == PlanOp.SATISFY and ok.changes[0].by == "old_farm"
    with pytest.raises(ValueError, match="needs by"):
        parse_decision({**base, "plan_changes": [{"op": "satisfy", "id": "m1", "reason": "r"}]})
    with pytest.raises(ValueError, match="only satisfy"):
        parse_decision({**base, "plan_changes": [{"op": "drop", "id": "m1", "by": "x", "reason": "r"}]})


def _keeper(met=(), facilities=FACILITIES):
    bridge, designer = AsyncMock(), AsyncMock()
    bridge.facilities.return_value = list(facilities)
    state = {"home": {"door": {"x": 0, "z": 10}}, "self": {"position": {"x": 0, "z": 0}}}
    bridge.observe.return_value = GameObservation(
        state=state, candidates=(), health=20.0, food=20, has_home=True, house_complete=True
    )

    async def check(specs):
        return [ConditionStatus(sp, sp.describe() in met) for sp in specs]

    bridge.check.side_effect = check
    designer.known.return_value = set()
    publisher = AsyncMock()
    keeper = ObjectiveKeeper(bridge=bridge, event_publisher=publisher, store=Mock(), facilities=designer)
    return keeper, bridge, publisher


@pytest.mark.asyncio
async def test_a_satisfied_playbook_step_is_done_and_does_not_come_back():
    steps = (PlaybookStep("food", "畑", (FARM, WHEAT), "食料"), PlaybookStep("iron", "鉄", (PICKAXE,), "資材"))
    plan = ObjectivePlan(mission=MISSION, playbook=steps)
    keeper, _, publisher = _keeper(met=(WHEAT.describe(),))
    await keeper.judge(plan)
    goal = plan.pending[0]
    assert goal.playbook == "food"
    change = PlanChange(PlanOp.SATISFY, "もう畑がある", objective_id=goal.id, by="old_farm")
    await keeper.check_new(plan, [change])
    await keeper.commit(plan, [change])
    assert plan.playbook_done == ("food",)
    assert plan.finished[-1].ended_because == "already served by the facility old_farm"
    assert any(isinstance(c.args[0], ObjectiveCompletedEvent) for c in publisher.publish.await_args_list)
    await keeper.judge(plan)
    assert [g.playbook for g in plan.pending] == ["iron"]


@pytest.mark.asyncio
async def test_a_playbook_step_whose_other_condition_is_not_met_is_refused():
    plan = ObjectivePlan(mission=MISSION, playbook=(PlaybookStep("food", "畑", (FARM, WHEAT), "食料"),))
    keeper, _, _ = _keeper()
    await keeper.judge(plan)
    change = PlanChange(PlanOp.SATISFY, "r", objective_id=plan.pending[0].id, by="old_farm")
    with pytest.raises(GoalRejectedError, match="every other condition"):
        await keeper.check_new(plan, [change])


@pytest.mark.asyncio
async def test_a_bad_proof_is_refused_before_anything_changes():
    plan = ObjectivePlan(mission=MISSION)
    plan.add("畑", (FARM,), "食料")
    keeper, _, _ = _keeper(facilities=[*FACILITIES[:2], _farm("old_farm", 400, 0)])
    change = PlanChange(PlanOp.SATISFY, "r", objective_id=plan.pending[0].id, by="old_farm")
    with pytest.raises(GoalRejectedError, match="too far"):
        await keeper.check_new(plan, [change])
    assert len(plan.pending) == 1
    with pytest.raises(GoalRejectedError, match="no pending objective m99"):
        await keeper.check_new(plan, [PlanChange(PlanOp.SATISFY, "r", objective_id="m99", by="old_farm")])


@pytest.mark.asyncio
async def test_satisfy_is_only_for_objectives_with_a_facility_condition():
    plan = ObjectivePlan(mission=MISSION)
    plan.add("小麦", (WHEAT,), "食料")
    keeper, _, _ = _keeper()
    with pytest.raises(GoalRejectedError, match="no facility condition"):
        await keeper.check_new(plan, [PlanChange(PlanOp.SATISFY, "r", objective_id=plan.pending[0].id, by="old_farm")])


@pytest.mark.asyncio
async def test_the_memory_is_shown_when_the_list_or_the_home_changes_not_every_time():
    plan = ObjectivePlan(mission=MISSION)
    plan.add("畑", (FARM,), "食料")
    bridge = AsyncMock()
    bridge.facilities.return_value = list(FACILITIES)
    me = SimpleNamespace(_bridge=bridge, _facilities_seen=None)
    text, seen = await AdvancePlayUseCase._facility_memory(me, plan)
    assert "old_farm" in text
    me._facilities_seen = seen
    assert (await AdvancePlayUseCase._facility_memory(me, plan))[0] == ""
    plan.add("小麦", (WHEAT,), "食料")
    assert "old_farm" in (await AdvancePlayUseCase._facility_memory(me, plan))[0]
    bridge.facilities.return_value = [_home(500, 500), *FACILITIES[1:]]
    me._facilities_seen = (tuple(g.id for g in plan.pending), (0.0, 0.0))
    assert "old_farm" in (await AdvancePlayUseCase._facility_memory(me, plan))[0]


@pytest.mark.asyncio
async def test_no_memory_without_a_facility_condition():
    plan = ObjectivePlan(mission=MISSION)
    plan.add("小麦", (WHEAT,), "食料")
    bridge = AsyncMock()
    me = SimpleNamespace(_bridge=bridge, _facilities_seen=None)
    assert await AdvancePlayUseCase._facility_memory(me, plan) == ("", None)
    bridge.facilities.assert_not_awaited()
