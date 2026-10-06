"""整地する土地をブリッジへ送る（docs/design/70_leveling_dirt.md）。"""

import json

import httpx
import pytest

from ailoveshen.application.use_cases.play import _level_areas
from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.value_objects import (
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    Job,
    Mission,
)
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
    MineflayerBridgeClient,
)

DIRT = GoalSpec(GoalPredicate.HAVE, item="dirt", count=20)
LEVEL_HOME = GoalSpec(GoalPredicate.LEVELED, where="home", distance=5)
LEVEL_FIELD = GoalSpec(GoalPredicate.LEVELED, where="10,64,20", distance=8)


def _plan() -> ObjectivePlan:
    plan = ObjectivePlan(mission=Mission("街"))
    plan.add("食料の安定供給", (GoalSpec(GoalPredicate.FARMED, item="wheat", count=8),))
    plan.set_jobs("m1", (Job(LEVEL_HOME, "畑の地盤"), Job(DIRT, "土")))
    return plan


class TestLevelAreas:
    def test_the_leveled_jobs_of_the_objectives_are_areas(self):
        assert _level_areas(_plan(), (DIRT,)) == (LEVEL_HOME,)

    def test_the_small_goal_and_its_bundle_count_too(self):
        assert _level_areas(_plan(), (LEVEL_FIELD, DIRT)) == (LEVEL_FIELD, LEVEL_HOME)

    def test_the_same_area_is_sent_once(self):
        assert _level_areas(_plan(), (LEVEL_HOME,)) == (LEVEL_HOME,)

    def test_no_leveled_no_areas(self):
        plan = ObjectivePlan(mission=Mission("街"))
        plan.add("木", (DIRT,))
        assert _level_areas(plan, (DIRT,)) == ()


@pytest.mark.asyncio
async def test_the_bridge_gets_where_and_distance_of_each_area():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"met": False, "remaining": 20, "lines": [], "leaves": []})

    client = MineflayerBridgeClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://b")
    status = await client.set_goal(DIRT, level_areas=(LEVEL_HOME, LEVEL_FIELD, DIRT))
    assert isinstance(status, GoalStatus)
    assert seen["level_areas"] == [
        {"where": "home", "distance": 5},
        {"where": "10,64,20", "distance": 8},
    ]
