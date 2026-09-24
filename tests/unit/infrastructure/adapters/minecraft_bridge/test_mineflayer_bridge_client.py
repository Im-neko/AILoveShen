"""Tests for MineflayerBridgeClient adapter."""

import json

import httpx
import pytest

from ailoveshen.domain.exceptions import GameBridgeError
from ailoveshen.domain.value_objects import BlockKind, PlannedBlock
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
    MineflayerBridgeClient,
)

OBSERVE = {
    "busy": False,
    "observation": {
        "self": {"health": 18.5, "food": 17},
        "inventory": {"spruce_log": 3},
        "crafting_table_nearby": True,
        "build": {
            "origin": {"x": 1, "y": 2, "z": 3},
            "total": 72,
            "placed": 10,
            "complete": False,
            "materials_needed": {"planks": 61, "door": 1},
        },
    },
    "actions": [{"id": "collect_log", "description": "Chop"}],
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
        assert obs.inventory == {"spruce_log": 3}
        assert obs.crafting_table_nearby
        assert obs.actions[0].action_id == "collect_log"
        assert obs.build.placed == 10 and obs.build.site_chosen
        assert obs.build.remaining == {BlockKind.PLANKS: 61, BlockKind.DOOR: 1}
        assert obs.state is not None and obs.state["inventory"] == {"spruce_log": 3}

    @pytest.mark.asyncio
    async def test_observe_without_plan(self):
        """Test no build section means no build status."""
        data = {**OBSERVE, "observation": {**OBSERVE["observation"]}}
        del data["observation"]["build"]
        client = _client(lambda req: httpx.Response(200, json=data))

        assert (await client.observe()).build is None

    @pytest.mark.asyncio
    async def test_act_posts_action_id(self):
        """Test act() posts the id and returns the result."""
        seen = {}

        def handler(req):
            seen["path"] = req.url.path
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"ok": False, "result": "failed: x", "seconds": 1.5})

        result = await _client(handler).act("build_step")

        assert seen == {"path": "/act", "body": {"id": "build_step"}}
        assert not result.ok and result.result == "failed: x" and result.seconds == 1.5

    @pytest.mark.asyncio
    async def test_set_build_plan_sends_blocks_in_order(self):
        """Test the plan is sent with block kinds as strings, in order."""
        seen = {}

        def handler(req):
            seen["method"] = req.method
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={})

        blocks = (PlannedBlock(0, 0, 0, BlockKind.PLANKS), PlannedBlock(2, 0, 0, BlockKind.DOOR))
        await _client(handler).set_build_plan(blocks, 5, 5, 4)

        assert seen["method"] == "PUT"
        assert seen["body"] == {
            "blocks": [
                {"x": 0, "y": 0, "z": 0, "block": "planks"},
                {"x": 2, "y": 0, "z": 0, "block": "door"},
            ],
            "width": 5,
            "depth": 5,
            "height": 4,
        }

    @pytest.mark.asyncio
    async def test_error_status_raises(self):
        """Test a non-200 response raises GameBridgeError."""
        client = _client(lambda req: httpx.Response(409, json={"error": "busy"}))

        with pytest.raises(GameBridgeError, match="409"):
            await client.act("explore")

    @pytest.mark.asyncio
    async def test_unreachable_raises(self):
        """Test a connection error raises GameBridgeError."""

        def handler(req):
            raise httpx.ConnectError("refused")

        with pytest.raises(GameBridgeError, match="unreachable"):
            await _client(handler).observe()
