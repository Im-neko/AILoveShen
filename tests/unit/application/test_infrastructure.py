"""畑を作ると題名に書いて条件にない提案の差し戻し（定石の段を足す仕組みは test_playbook.py）。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases import objectives
from ailoveshen.application.use_cases.goal_vocabulary import ObjectiveProposal, PlanChange, PlanOp
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.plan_lint import fake_infrastructure
from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.exceptions import FacilityNotDesignedError, GoalRejectedError
from ailoveshen.domain.value_objects import (
    ConditionStatus,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    Mission,
    TownDefinition,
    TownSite,
    TownStage,
)

MISSION = Mission("生き延びながら家を建て、街にしていく")
SITE = TownSite("NE", 59, -345, "平ら", "Neo-Shenburg")
FOOD = GoalSpec(GoalPredicate.STORED, item="food", count=16)
HAVE_SEEDS = GoalSpec(GoalPredicate.HAVE, item="wheat_seeds", count=4)
FARM = lambda name: GoalSpec(GoalPredicate.FACILITY, name=name)  # noqa: E731


def _obs(house=True, distance=10):
    state = {"home": {"door": {"x": 0, "z": distance}}, "self": {"position": {"x": 0, "z": 0}}}
    return GameObservation(
        state=state, candidates=(), health=20.0, food=20, has_home=house, house_complete=house
    )


def _plan(*stages: TownStage):
    plan = ObjectivePlan(mission=MISSION)
    plan.define_town(TownDefinition("街", stages or (TownStage("備蓄", "r", conditions=(FOOD,)),)))
    plan.choose_site(SITE)
    return plan


def _keeper(observation=None, built=()):
    bridge, facilities = AsyncMock(), AsyncMock()
    bridge.observe.return_value = observation or _obs()
    bridge.facilities.return_value = list(built)

    async def not_met(specs):
        return [ConditionStatus(sp, False, (sp.describe(),), ()) for sp in specs]

    bridge.check.side_effect = not_met
    facilities.known.return_value = set()
    store = Mock()
    return ObjectiveKeeper(bridge=bridge, event_publisher=AsyncMock(), store=store, facilities=facilities), bridge, facilities


def test_a_farm_title_without_the_facility_in_its_conditions_is_sent_back():
    assert "facility(<new name>)" in fake_infrastructure("小麦畑を作る", (HAVE_SEEDS,))
    assert fake_infrastructure("小麦畑を作る", (FARM("wheat_farm_1"),)) == ""
    assert fake_infrastructure("小麦畑を作る", (GoalSpec(GoalPredicate.FARMED, item="wheat", count=4),)) == ""
    assert fake_infrastructure("松明を作る", (HAVE_SEEDS,)) == ""
    assert fake_infrastructure("石を採石する", (GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=64),)) == ""


@pytest.mark.asyncio
async def test_the_keeper_rejects_a_fake_farm_but_not_a_job_of_a_bigger_objective():
    keeper, bridge, _ = _keeper()
    plan = _plan()
    fake = ObjectiveProposal("食料の安定供給: 小麦畑を作る", (HAVE_SEEDS,), "畑")
    with pytest.raises(GoalRejectedError, match="facility"):
        await keeper.check_new([PlanChange(PlanOp.ADD, "r", proposal=fake)])
    with pytest.raises(GoalRejectedError, match="facility"):
        await keeper.accept(plan, fake, "viewer")
