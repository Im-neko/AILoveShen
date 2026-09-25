"""Tests for MineflayerBridgeClient adapter."""

import json

import httpx
import pytest

from ailoveshen.domain.exceptions import GameBridgeError, GoalRejectedError
from ailoveshen.domain.value_objects import GoalPredicate, GoalSpec, HouseBlueprint, Side
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
    MineflayerBridgeClient,
)

# The shape of GET /observe (minecraft-bridge/src/index.mjs)
OBSERVE = {
    "busy": True,
    "observation": {
        "time": {"phase": "dusk", "time_of_day": 12500},
        "self": {"health": 18.5, "food": 17},
        "home": {"inside": False, "door_open": False, "bed": True},
        "inventory": {"spruce_log": 3},
        "build": {"origin": {"x": 1, "y": 2, "z": 3}, "total": 72, "placed": 10, "complete": False},
    },
    "needs": ["night is coming: hostile mobs spawn outside in the dark"],
    "goal": {
        "spec": {"predicate": "have", "item": "planks", "count": 12},
        "met": False,
        "remaining": 4,
        "lines": ["have 12 planks (5/12): craft spruce_planks x2"],
        "blocked": ["no oak_log nearby for oak_log"],
    },
    "candidates": [
        {"id": "dig spruce_log at 1,70,2", "verb": "dig", "target": "spruce_log", "distance": 3}
    ],
}


def _client(handler) -> MineflayerBridgeClient:
    client = MineflayerBridgeClient()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://bridge"
    )
    return client


class TestMineflayerBridgeClient:
    """Tests for the HTTP contract with the Node bridge."""

    @pytest.mark.asyncio
    async def test_observe_parses_observation(self):
        """Test the observation JSON becomes a GameObservation."""
        client = _client(lambda req: httpx.Response(200, json=OBSERVE))

        obs = await client.observe()

        assert obs.health == 18.5 and obs.food == 17
        assert obs.candidates[0].action_id == "dig spruce_log at 1,70,2"
        assert obs.candidates[0].description == {
            "verb": "dig",
            "target": "spruce_log",
            "distance": 3,
        }
        assert obs.goal.remaining == 4 and not obs.goal.met
        assert obs.goal.lines == ("have 12 planks (5/12): craft spruce_planks x2",)
        assert obs.goal.blocked == ("no oak_log nearby for oak_log",)
        assert obs.needs == ("night is coming: hostile mobs spawn outside in the dark",)
        assert obs.state["inventory"] == {"spruce_log": 3}
        assert obs.time_phase == "dusk" and obs.busy
        assert obs.has_plan and not obs.house_complete
        assert obs.has_home and not obs.inside_home and obs.bed_in_home

    @pytest.mark.asyncio
    async def test_observe_without_plan_home_or_goal(self):
        """Test missing sections mean no plan, no home and no goal yet."""
        data = {**OBSERVE, "goal": None, "observation": {**OBSERVE["observation"], "home": None}}
        del data["observation"]["build"]
        client = _client(lambda req: httpx.Response(200, json=data))

        obs = await client.observe()

        assert not obs.has_plan and not obs.house_complete
        assert not obs.has_home and not obs.inside_home
        assert obs.goal is None

    @pytest.mark.asyncio
    async def test_set_goal_puts_the_spec(self):
        """Test set_goal() sends the spec with what the chests keep, and returns its status."""
        seen = {}

        def handler(req):
            seen["method"] = req.method
            seen["path"] = req.url.path
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json=OBSERVE["goal"])

        status = await _client(handler).set_goal(
            GoalSpec(GoalPredicate.HAVE, item="planks", count=12),
            keep=(GoalSpec(GoalPredicate.STORED, item="food", count=10),),
        )

        assert seen == {
            "method": "PUT",
            "path": "/goal",
            "body": {
                "predicate": "have",
                "item": "planks",
                "count": 12,
                "keep": [{"item": "food", "count": 10}],
            },
        }
        assert status.remaining == 4

    @pytest.mark.asyncio
    async def test_rejected_goal_raises_with_the_reason(self):
        """Test a 400 becomes GoalRejectedError carrying the bridge's reason."""
        client = _client(
            lambda req: httpx.Response(400, json={"error": "unknown item or group: x"})
        )

        with pytest.raises(GoalRejectedError, match="unknown item or group: x"):
            await client.set_goal(GoalSpec(GoalPredicate.HAVE, item="x", count=1))

    @pytest.mark.asyncio
    async def test_check_judges_conditions_without_setting_a_goal(self):
        """Test check() posts the specs and returns each one's status in order."""
        seen = {}

        def handler(req):
            seen["path"] = req.url.path
            seen["body"] = json.loads(req.content)
            return httpx.Response(
                200,
                json=[
                    {"spec": {"predicate": "built"}, "met": True, "lines": []},
                    {
                        "spec": {"predicate": "placed", "item": "bed", "where": "home"},
                        "met": False,
                        "lines": ["a bed in the house: no"],
                        "impossible": ["no way to get white_wool"],
                    },
                ],
            )

        built = GoalSpec(GoalPredicate.BUILT)
        bed = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
        statuses = await _client(handler).check([built, bed])

        assert seen == {
            "path": "/check",
            "body": {"specs": [{"predicate": "built"}, bed.to_dict()]},
        }
        assert [(s.spec, s.met, s.lines, s.impossible) for s in statuses] == [
            (built, True, (), ()),
            (bed, False, ("a bed in the house: no",), ("no way to get white_wool",)),
        ]

    @pytest.mark.asyncio
    async def test_check_rejected_condition_raises_with_the_reason(self):
        """Test a condition the bridge cannot judge becomes GoalRejectedError."""
        client = _client(
            lambda req: httpx.Response(400, json={"error": "unknown item or group: diamondz"})
        )

        with pytest.raises(GoalRejectedError, match="diamondz"):
            await client.check([GoalSpec(GoalPredicate.HAVE, item="diamondz", count=1)])

    @pytest.mark.asyncio
    async def test_check_nothing_asks_nothing(self):
        """Test no conditions make no request."""
        assert await _client(lambda req: httpx.Response(500)).check([]) == []

    @pytest.mark.asyncio
    async def test_act_posts_candidate_id(self):
        """Test act() posts the id and returns the result."""
        seen = {}

        def handler(req):
            seen["path"] = req.url.path
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"ok": False, "result": "failed: x", "seconds": 1.5})

        result = await _client(handler).act("wait inside")

        assert seen == {"path": "/act", "body": {"id": "wait inside"}}
        assert not result.ok and result.result == "failed: x" and result.seconds == 1.5

    @pytest.mark.asyncio
    async def test_set_build_plan_sends_blocks_in_order_with_the_design(self):
        """Test the plan is sent with block kinds as strings, in order, and the design."""
        seen = {}

        def handler(req):
            seen["method"] = req.method
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={})

        blueprint = HouseBlueprint(
            "ぽかぽか", "木の家", 5, 6, 3, Side.SOUTH, 2, corner_pillars=True
        )
        await _client(handler).set_build_plan(blueprint)

        body = seen["body"]
        assert seen["method"] == "PUT"
        assert body["blocks"][:2] == [
            {"x": b.x, "y": b.y, "z": b.z, "block": b.kind.value} for b in blueprint.blocks()[:2]
        ]
        assert len(body["blocks"]) == len(blueprint.blocks())
        assert (body["width"], body["depth"], body["height"]) == (5, 6, 4)
        assert body["design"] == {
            "name": "ぽかぽか",
            "concept": "木の家",
            "width": 5,
            "depth": 6,
            "wall_height": 3,
            "door_side": "south",
            "door_offset": 2,
            "corner_pillars": True,
        }

    @pytest.mark.asyncio
    async def test_error_status_raises(self):
        """Test a non-200 response raises GameBridgeError."""
        client = _client(lambda req: httpx.Response(409, json={"error": "busy"}))

        with pytest.raises(GameBridgeError, match="409"):
            await client.act("explore north")

    @pytest.mark.asyncio
    async def test_unreachable_raises(self):
        """Test a connection error raises GameBridgeError."""

        def handler(req):
            raise httpx.ConnectError("refused")

        with pytest.raises(GameBridgeError, match="unreachable"):
            await _client(handler).observe()
