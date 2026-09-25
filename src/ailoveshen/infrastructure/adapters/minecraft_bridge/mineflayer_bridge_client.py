"""Mineflayer のブリッジへの HTTP クライアントのアダプター。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.domain.exceptions import GameBridgeError, GoalRejectedError
from ailoveshen.domain.value_objects import (
    ActionResult,
    BuildDesign,
    Candidate,
    ConditionStatus,
    GameObservation,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    TownSite,
)


class MineflayerBridgeClient(IMinecraftBridge):
    """
    Node.js の Mineflayer サイドカー（minecraft-bridge/）のインフラ側アダプター。

    IMinecraftBridge を、サイドカーの HTTP API で実装する。
    """

    def __init__(
        self, host: str = "localhost", port: int = 3000, timeout_seconds: float = 60.0
    ) -> None:
        """
        クライアントを初期化する。

        Args:
            host: ブリッジのホスト名
            port: ブリッジの HTTP ポート
            timeout_seconds: リクエストのタイムアウト。ブリッジの行動のタイムアウトより
                長くすること
        """
        self._client = httpx.AsyncClient(base_url=f"http://{host}:{port}", timeout=timeout_seconds)

    async def observe(self) -> GameObservation:
        """観測、目標の状態、候補を取得する。"""
        data = await self._request("GET", "/observe")
        return _to_observation(data)

    async def set_goal(self, spec: GoalSpec, keep: Sequence[GoalSpec] = ()) -> GoalStatus:
        """目標を設定する。ブリッジが返す 400 には、拒否した理由が入っている。"""
        body = {**spec.to_dict(), "keep": [{"item": k.item, "count": k.count} for k in keep]}
        try:
            response = await self._client.put("/goal", json=body)
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
        """目標を設定せずに条件を判定する。400 には、判定できない理由が入っている。"""
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
            ConditionStatus(
                spec=spec,
                met=bool(r["met"]),
                lines=tuple(r.get("lines", [])),
                impossible=tuple(r.get("impossible", [])),
            )
            for spec, r in zip(specs, response.json(), strict=True)
        ]

    async def act(self, action_id: str) -> ActionResult:
        """ブリッジで候補を 1つ実行する。"""
        data = await self._request("POST", "/act", json={"id": action_id})
        return ActionResult(
            action_id=action_id,
            ok=bool(data["ok"]),
            result=str(data["result"]),
            seconds=float(data["seconds"]),
        )

    async def run_tool(self, name: str, args: dict[str, Any]) -> tuple[bool, str, float, bool]:
        """道具を 1 つ呼ぶ。調べものの結果（構造）は JSON の文にして返す。"""
        data = await self._request("POST", "/tool", json={"name": name, "args": args})
        result = data["result"]
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return bool(data["ok"]), text, float(data["seconds"]), bool(data.get("refused", False))

    async def state(self) -> dict[str, Any]:
        """共通の状態を取得する。"""
        return await self._request("GET", "/state")

    async def abort(self, reason: str) -> bool:
        """実行中の行動を止める。もう終わっていれば False。"""
        data = await self._request("POST", "/abort", json={"reason": reason})
        return bool(data.get("aborted", False))

    async def set_build(self, design: BuildDesign) -> None:
        """名前付きの建物を送る。400 には置けない理由が入っている。"""
        width, height, depth = design.size()
        payload = {
            "blocks": [
                {"x": p.x, "y": p.y, "z": p.z, "block": p.kind.value} for p in design.blocks()
            ],
            "size": {"width": width, "depth": depth, "height": height},
            "anchor": design.anchor.value,
            "purpose": design.purpose,
        }
        if design.site is not None:
            payload["site"] = {"x": design.site[0], "z": design.site[1]}
        try:
            response = await self._client.put(f"/builds/{design.name}", json=payload)
        except httpx.RequestError as e:
            raise GameBridgeError(f"Minecraft bridge unreachable (PUT /builds): {e}") from e
        if response.status_code == 400:
            raise GoalRejectedError(f"build {design.name} rejected: {response.json()['error']}")
        if response.status_code != 200:
            raise GameBridgeError(
                f"Minecraft bridge PUT /builds -> {response.status_code}: {response.text[:200]}"
            )

    async def map(self) -> dict[str, Any]:
        """家のまわりの真上から見た地図のデータ（minecraft-bridge/src/map.mjs）。"""
        return await self._request("GET", "/map")

    async def builds(self) -> list[dict[str, Any]]:
        """登録した建物の一覧。"""
        data: Any = await self._request("GET", "/builds")
        return list(data) if isinstance(data, list) else []

    async def set_build_plan(self, blueprint: HouseBlueprint, site: TownSite | None = None) -> None:
        """設計図のブロックを置く順に、設計と（あれば）建てる場所と一緒に送る。"""
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
            "site": {"x": site.x, "z": site.z} if site else None,
        }
        await self._request("PUT", "/build-plan", json=payload)

    async def close(self) -> None:
        """HTTP クライアントを閉じる。"""
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
        unfinished_builds=tuple(
            str(b["name"]) for b in obs.get("builds") or [] if not b.get("complete")
        ),
        busy=bool(data.get("busy", False)),
        day=int(obs["time"]["day"]) if obs["time"]["day"] is not None else None,
    )
