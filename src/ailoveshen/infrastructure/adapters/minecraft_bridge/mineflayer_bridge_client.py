"""Mineflayer bridge HTTP client adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.domain.exceptions import GameBridgeError
from ailoveshen.domain.value_objects import (
    ActionResult,
    AvailableAction,
    BlockKind,
    BuildStatus,
    GameObservation,
    PlannedBlock,
)


class MineflayerBridgeClient(IMinecraftBridge):
    """
    Infrastructure adapter for the Node.js Mineflayer sidecar (minecraft-bridge/).

    Implements IMinecraftBridge over the sidecar's HTTP API.
    """

    def __init__(
        self, host: str = "localhost", port: int = 3000, timeout_seconds: float = 60.0
    ) -> None:
        """
        Initialize the client.

        Args:
            host: Bridge hostname
            port: Bridge HTTP port
            timeout_seconds: Request timeout; must exceed the bridge's action timeout
        """
        self._client = httpx.AsyncClient(base_url=f"http://{host}:{port}", timeout=timeout_seconds)

    async def observe(self) -> GameObservation:
        """Fetch the observation and the executable actions."""
        data = await self._request("GET", "/observe")
        return _to_observation(data)

    async def act(self, action_id: str) -> ActionResult:
        """Run one action on the bridge."""
        data = await self._request("POST", "/act", json={"id": action_id})
        return ActionResult(
            action_id=action_id,
            ok=bool(data["ok"]),
            result=str(data["result"]),
            seconds=float(data["seconds"]),
        )

    async def set_build_plan(
        self,
        blocks: Sequence[PlannedBlock],
        width: int,
        depth: int,
        height: int,
    ) -> None:
        """Send the plan's blocks in placement order."""
        payload = {
            "blocks": [{"x": b.x, "y": b.y, "z": b.z, "block": b.kind.value} for b in blocks],
            "width": width,
            "depth": depth,
            "height": height,
        }
        await self._request("PUT", "/build-plan", json=payload)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _request(self, method: str, path: str, json: Any = None) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.RequestError as e:
            raise GameBridgeError(f"Minecraft bridge unreachable ({method} {path}): {e}") from e
        if response.status_code != 200:
            raise GameBridgeError(
                f"Minecraft bridge {method} {path} -> {response.status_code}: {response.text[:200]}"
            )
        return response.json()


def _to_observation(data: dict[str, Any]) -> GameObservation:
    obs = data["observation"]
    home = obs.get("home")
    build = obs.get("build")
    build_status = None
    if build is not None:
        build_status = BuildStatus(
            total=int(build["total"]),
            placed=int(build["placed"]),
            complete=bool(build["complete"]),
            site_chosen=build.get("origin") is not None,
            remaining={BlockKind(k): int(v) for k, v in build.get("materials_needed", {}).items()},
        )
    return GameObservation(
        state=obs,
        inventory={str(k): int(v) for k, v in obs["inventory"].items()},
        actions=tuple(AvailableAction(a["id"], a["description"]) for a in data["actions"]),
        health=float(obs["self"]["health"]),
        food=int(obs["self"]["food"]),
        crafting_table_nearby=bool(obs.get("crafting_table_nearby", False)),
        build=build_status,
        time_phase=str(obs["time"]["phase"]),
        food_items=int(obs.get("food_items", 0)),
        has_home=home is not None,
        inside_home=bool(home and home["inside"]),
        busy=bool(data.get("busy", False)),
    )
