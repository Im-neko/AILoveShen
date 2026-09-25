"""Mineflayer bridge HTTP client adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.domain.exceptions import GameBridgeError, GoalRejectedError
from ailoveshen.domain.value_objects import (
    ActionResult,
    Candidate,
    ConditionStatus,
    GameObservation,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
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
        """Fetch the observation, the goal's status and the candidates."""
        data = await self._request("GET", "/observe")
        return _to_observation(data)

    async def set_goal(self, spec: GoalSpec) -> GoalStatus:
        """Set the goal; a 400 from the bridge carries the reason it was rejected."""
        try:
            response = await self._client.put("/goal", json=spec.to_dict())
        except httpx.RequestError as e:
            raise GameBridgeError(f"Minecraft bridge unreachable (PUT /goal): {e}") from e
        if response.status_code == 400:
            raise GoalRejectedError(f"goal {spec.describe()} rejected: {response.json()['error']}")
        if response.status_code != 200:
            raise GameBridgeError(
                f"Minecraft bridge PUT /goal -> {response.status_code}: {response.text[:200]}"
            )
        return _to_status(response.json())

    async def check(self, specs: Sequence[GoalSpec]) -> list[ConditionStatus]:
        """Judge conditions without setting a goal; a 400 carries why one cannot be judged."""
        if not specs:
            return []
        try:
            response = await self._client.post(
                "/check", json={"specs": [s.to_dict() for s in specs]}
            )
        except httpx.RequestError as e:
            raise GameBridgeError(f"Minecraft bridge unreachable (POST /check): {e}") from e
        if response.status_code == 400:
            raise GoalRejectedError(f"conditions rejected: {response.json()['error']}")
        if response.status_code != 200:
            raise GameBridgeError(
                f"Minecraft bridge POST /check -> {response.status_code}: {response.text[:200]}"
            )
        return [
            ConditionStatus(spec=spec, met=bool(r["met"]), lines=tuple(r.get("lines", [])))
            for spec, r in zip(specs, response.json(), strict=True)
        ]

    async def act(self, action_id: str) -> ActionResult:
        """Run one candidate on the bridge."""
        data = await self._request("POST", "/act", json={"id": action_id})
        return ActionResult(
            action_id=action_id,
            ok=bool(data["ok"]),
            result=str(data["result"]),
            seconds=float(data["seconds"]),
        )

    async def set_build_plan(self, blueprint: HouseBlueprint) -> None:
        """Send the plan's blocks in placement order, with the design."""
        b = blueprint
        payload = {
            "blocks": [{"x": p.x, "y": p.y, "z": p.z, "block": p.kind.value} for p in b.blocks()],
            "width": b.width,
            "depth": b.depth,
            "height": b.height,
            "design": {
                "name": b.name,
                "concept": b.concept,
                "width": b.width,
                "depth": b.depth,
                "wall_height": b.wall_height,
                "door_side": b.door_side.value,
                "door_offset": b.door_offset,
                "corner_pillars": b.corner_pillars,
            },
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


def _to_status(data: dict[str, Any]) -> GoalStatus:
    return GoalStatus(
        met=bool(data["met"]),
        remaining=int(data["remaining"]),
        lines=tuple(str(x) for x in data.get("lines", [])),
        blocked=tuple(str(x) for x in data.get("blocked", [])),
    )


def _to_observation(data: dict[str, Any]) -> GameObservation:
    obs = data["observation"]
    home = obs.get("home")
    build = obs.get("build")
    return GameObservation(
        state=obs,
        candidates=tuple(
            Candidate(c["id"], {k: v for k, v in c.items() if k != "id"})
            for c in data["candidates"]
        ),
        health=float(obs["self"]["health"]),
        food=int(obs["self"]["food"]),
        needs=tuple(str(n) for n in data.get("needs", [])),
        goal=_to_status(data["goal"]) if data.get("goal") else None,
        time_phase=str(obs["time"]["phase"]),
        has_plan=build is not None,
        house_complete=bool(build and build["complete"]),
        has_home=home is not None,
        inside_home=bool(home and home["inside"]),
        bed_in_home=bool(home and home.get("bed")),
        busy=bool(data.get("busy", False)),
    )
