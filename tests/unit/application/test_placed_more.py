"""置いてある物を置けという頼み（チェストの増設）は、今の数より 1 つ多い数に直す（2026-10-08）。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.goal_vocabulary import ObjectiveProposal
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.domain.value_objects import ConditionStatus, GoalPredicate, GoalSpec


def _keeper(placed: int):
    """placed 個ある世界。placed(chest, home, n) は n <= placed のとき済み。"""
    bridge = AsyncMock()

    async def check(specs):
        return [
            ConditionStatus(sp, sp.predicate != GoalPredicate.PLACED or (sp.count or 1) <= placed, (), ())
            for sp in specs
        ]

    bridge.check.side_effect = check
    return ObjectiveKeeper(bridge=bridge, event_publisher=AsyncMock(), store=Mock(), facilities=AsyncMock())


CHEST = GoalSpec(GoalPredicate.PLACED, item="chest", where="home")


@pytest.mark.asyncio
async def test_a_chest_that_is_already_there_becomes_one_more():
    proposal = ObjectiveProposal("チェストの増設", (CHEST,), "いっぱい")
    fixed = await _keeper(1)._beyond_what_is_placed(proposal)
    assert [c.describe() for c in fixed.conditions] == ["placed(chest, 2, home)"]
    fixed = await _keeper(3)._beyond_what_is_placed(proposal)
    assert [c.describe() for c in fixed.conditions] == ["placed(chest, 4, home)"]
    assert fixed.title == "チェストの増設"


@pytest.mark.asyncio
async def test_a_request_that_is_not_met_yet_stays_as_it_is():
    proposal = ObjectiveProposal("チェストを置く", (CHEST,), "")
    assert await _keeper(0)._beyond_what_is_placed(proposal) is proposal
    two = GoalSpec(GoalPredicate.PLACED, item="chest", where="home", count=3)
    proposal = ObjectiveProposal("チェストを 3 つ", (two,), "")
    assert await _keeper(2)._beyond_what_is_placed(proposal) is proposal


@pytest.mark.asyncio
async def test_other_conditions_are_left_alone():
    have = GoalSpec(GoalPredicate.HAVE, item="oak_planks", count=8)
    proposal = ObjectiveProposal("板材を集める", (have,), "")
    keeper = _keeper(1)
    assert await keeper._beyond_what_is_placed(proposal) is proposal
    keeper._bridge.check.assert_not_awaited()
    mixed = ObjectiveProposal("チェストの増設", (have, CHEST), "")
    fixed = await keeper._beyond_what_is_placed(mixed)
    assert [c.describe() for c in fixed.conditions] == [have.describe(), "placed(chest, 2, home)"]


@pytest.mark.asyncio
async def test_a_viewer_request_to_add_a_chest_is_not_done_the_moment_it_is_added():
    from ailoveshen.domain.entities import ObjectivePlan
    from ailoveshen.domain.value_objects import Mission

    keeper = _keeper(1)
    plan = ObjectivePlan(mission=Mission("生き延びながら家を建て、街にしていく"))
    goal = await keeper.accept(plan, ObjectiveProposal("チェストの増設", (CHEST,), "いっぱい"), "ねこかも", advice=True)
    assert [c.describe() for c in goal.conditions] == ["placed(chest, 2, home)"]
    assert goal.title == "チェストの増設"
