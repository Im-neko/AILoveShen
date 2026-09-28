"""Mineflayer のブリッジへの HTTP クライアントのアダプター。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx

from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.domain.exceptions import GameBridgeError, GoalRejectedError, SkillRejectedError
from ailoveshen.domain.value_objects import (
    ActionResult,
    BuildDesign,
    BundleMember,
    Candidate,
    ConditionStatus,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    SkillDraft,
    SkillInfo,
    SkillRun,
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
            timeout_seconds: リクエストのタイムアウト。行動（候補・道具・技）の実行は時間で
                止めない（止めるのはブリッジ: 夕方・夜、空腹・体力、POST /abort）ので、その返事は
                読み取りを待ち続ける
        """
        self._client = httpx.AsyncClient(base_url=f"http://{host}:{port}", timeout=timeout_seconds)
        self._action_timeout = httpx.Timeout(timeout_seconds, read=None)

    async def observe(self) -> GameObservation:
        """観測、目標の状態、候補を取得する。"""
        data = await self._request("GET", "/observe")
        return _to_observation(data)

    async def set_goal(
        self,
        spec: GoalSpec,
        keep: Sequence[GoalSpec] = (),
        also: Sequence[GoalSpec] = (),
        together: Sequence[GoalSpec] = (),
    ) -> GoalStatus:
        """目標を設定する。ブリッジが返す 400 には、拒否した理由が入っている。"""
        body = {
            **spec.to_dict(),
            "keep": [{"item": k.item, "count": k.count} for k in keep],
            "also": [a for a in map(_gathered, also) if a],
        }
        if together:
            body["together"] = [t.to_dict() for t in together]
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
        data = await self._request("POST", "/act", json={"id": action_id}, action=True)
        return ActionResult(
            action_id=action_id,
            ok=bool(data["ok"]),
            result=str(data["result"]),
            seconds=float(data["seconds"]),
            interrupted=bool(data.get("interrupted", False)),
        )

    async def run_tool(self, name: str, args: dict[str, Any]) -> tuple[bool, str, float, bool]:
        """道具を 1 つ呼ぶ。調べものの結果（構造）は JSON の文にして返す。"""
        data = await self._request("POST", "/tool", json={"name": name, "args": args}, action=True)
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

    async def facilities(self) -> list[dict[str, Any]]:
        """施設の一覧（minecraft-bridge/src/facilities.mjs）。"""
        data: Any = await self._request("GET", "/facilities")
        return list(data) if isinstance(data, list) else []

    async def memory(self) -> dict[str, Any]:
        """覚えている場所を座標つきで（minecraft-bridge/src/memory.mjs の memoryList）。"""
        data: Any = await self._request("GET", "/memory")
        return dict(data) if isinstance(data, dict) else {}

    async def zones(self, x: int, z: int) -> list[dict[str, Any]]:
        """街の理想図の区画の地形（minecraft-bridge/src/zones.mjs）。"""
        data: Any = await self._request("GET", f"/zones?x={x}&z={z}")
        return list(data) if isinstance(data, list) else []

    async def items(self) -> list[dict[str, Any]]:
        """アイテムとブロックの名前と日本語名（minecraft-bridge/src/items.mjs の itemList）。"""
        data: Any = await self._request("GET", "/items")
        return list(data) if isinstance(data, list) else []

    async def item_info(self, name: str) -> dict[str, Any]:
        """アイテムの取り方と使い方（items.mjs の itemInfo）。"""
        data: Any = await self._request("GET", f"/items/{quote(name)}")
        return dict(data) if isinstance(data, dict) else {}

    async def set_condition(self, name: str, description: str, code: str) -> dict[str, Any]:
        """Gemini が書いた判定を送る。400 には受け取れない理由が入っている。"""
        try:
            response = await self._client.put(
                f"/conditions/{name}", json={"description": description, "code": code}
            )
        except httpx.RequestError as e:
            raise GameBridgeError(f"Minecraft bridge unreachable (PUT /conditions): {e}") from e
        if response.status_code == 400:
            raise GoalRejectedError(f"check {name} rejected: {response.json()['error']}")
        if response.status_code != 200:
            raise GameBridgeError(
                f"Minecraft bridge PUT /conditions -> {response.status_code}: "
                f"{response.text[:200]}"
            )
        data: dict[str, Any] = response.json()
        return data

    async def conditions(self) -> list[dict[str, Any]]:
        """登録した判定の一覧。"""
        data: Any = await self._request("GET", "/conditions")
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

    async def skills(self) -> list[SkillInfo]:
        """覚えた技の一覧。"""
        data: Any = await self._request("GET", "/skills")
        return [_to_skill(x) for x in data] if isinstance(data, list) else []

    async def skill(self, name: str) -> SkillInfo | None:
        """技の一番新しい版（コードを含む）。"""
        try:
            response = await self._client.get(f"/skills/{name}")
        except httpx.RequestError as e:
            raise GameBridgeError(f"Minecraft bridge unreachable (GET /skills): {e}") from e
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GameBridgeError(f"Minecraft bridge GET /skills/{name} -> {response.status_code}")
        return _to_skill(response.json())

    async def save_skill(self, name: str, draft: SkillDraft) -> int:
        """技を保存する。400 には受け取れない理由が入っている。"""
        try:
            response = await self._client.put(f"/skills/{name}", json=draft.to_dict())
        except httpx.RequestError as e:
            raise GameBridgeError(f"Minecraft bridge unreachable (PUT /skills): {e}") from e
        if response.status_code == 400:
            raise SkillRejectedError(response.json()["error"])
        if response.status_code != 200:
            raise GameBridgeError(
                f"Minecraft bridge PUT /skills/{name} -> {response.status_code}: "
                f"{response.text[:200]}"
            )
        return int(response.json()["version"])

    async def run_skill(
        self, name: str, args: dict[str, Any], version: int | None = None
    ) -> SkillRun:
        """技を 1 回実行する（終わるまで待つ。行動・調べものの回数などの上限はブリッジが持つ）。"""
        payload: dict[str, Any] = {"args": args}
        if version is not None:
            payload["version"] = version
        try:
            response = await self._client.post(
                f"/skills/{name}/run", json=payload, timeout=self._action_timeout
            )
        except httpx.RequestError as e:
            raise GameBridgeError(f"Minecraft bridge unreachable (POST /skills/run): {e}") from e
        if response.status_code == 404:
            return SkillRun(
                name=name,
                version=version or 0,
                ok=False,
                ended="error",
                reason=response.json().get("error", "no such skill"),
            )
        if response.status_code == 409:
            return SkillRun(
                name=name,
                version=version or 0,
                ok=False,
                ended="error",
                reason="another action is running",
            )
        if response.status_code != 200:
            raise GameBridgeError(
                f"Minecraft bridge POST /skills/{name}/run -> {response.status_code}: "
                f"{response.text[:200]}"
            )
        d = response.json()
        return SkillRun(
            name=name,
            version=int(d.get("version", 0)),
            ok=bool(d.get("ok")),
            ended=str(d.get("ended", "")),
            summary=str(d.get("summary") or ""),
            reason=str(d.get("reason") or ""),
            expects_lines=tuple(str(x) for x in (d.get("expects") or {}).get("lines", [])),
            calls=tuple(d.get("calls") or ()),
            log=tuple(str(x) for x in d.get("log") or ()),
            seconds=float(d.get("seconds", 0.0)),
            learned=bool(d.get("learned")),
        )

    async def danger(self) -> dict[str, Any] | None:
        """反射の危険と、判断に渡す状態（なければ None）。"""
        data: Any = await self._request("GET", "/danger")
        return data if isinstance(data, dict) else None

    async def steer_reflex(self, danger_id: int, choice: str, confidence: float) -> bool:
        """反射を選んだものに切り替える。"""
        data = await self._request(
            "POST", "/reflex", json={"id": danger_id, "choice": choice, "confidence": confidence}
        )
        return bool(data.get("accepted", False))

    async def answer_judge(self, judge_id: int, answer: Any, confidence: float) -> None:
        """技の judge() に答える。"""
        await self._request(
            "POST", "/judge", json={"id": judge_id, "answer": answer, "confidence": confidence}
        )

    async def _request(
        self, method: str, path: str, json: Any = None, action: bool = False
    ) -> dict[str, Any]:
        timeout = self._action_timeout if action else httpx.USE_CLIENT_DEFAULT
        try:
            response = await self._client.request(method, path, json=json, timeout=timeout)
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
        members=tuple(
            BundleMember(
                label=_label(m.get("spec") or {}),
                met=bool(m.get("met")),
                remaining=int(m.get("remaining", 0)),
            )
            for m in data.get("members") or []
        ),
    )


def _label(spec: dict[str, Any]) -> str:
    """ブリッジの describeSpec と同じ形（have(log, 16)）。"""
    args = [
        spec[k] for k in ("name", "item", "count", "where", "distance") if spec.get(k) is not None
    ]
    return f"{spec.get('predicate')}({', '.join(str(a) for a in args)})"


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
        underground=bool(obs.get("underground", False)),
    )


def _to_skill(d: dict[str, Any]) -> SkillInfo:
    return SkillInfo(
        name=str(d["name"]),
        description=str(d.get("description", "")),
        version=int(d.get("version", 0)),
        params=dict(d.get("params") or {}),
        expects=dict(d.get("expects") or {}),
        verified=bool(d.get("verified", d.get("successes", 0) > 0)),
        uses=int(d.get("uses", 0)),
        successes=int(d.get("successes", 0)),
        failures=int(d.get("failures", 0)),
        last_failure=d.get("last_failure"),
        last_good_version=d.get("last_good_version"),
        code=str(d.get("code", "")),
    )


# 畑の作物 → まく物（ついでに集めるのは種）
_SEEDS = {
    "wheat": "wheat_seeds",
    "carrots": "carrot",
    "potatoes": "potato",
    "beetroots": "beetroot_seeds",
}
_ALSO_SEEDS_MAX = 16


def _gathered(spec: GoalSpec) -> dict[str, object] | None:
    """ついでに集める物（ブリッジの also）。集める物でない条件は None。"""
    if spec.count is None:
        return None
    if spec.predicate in (GoalPredicate.HAVE, GoalPredicate.STORED):
        return {"item": spec.item, "count": spec.count}
    if spec.predicate == GoalPredicate.PLANTED:
        return {"item": spec.item or "sapling", "count": spec.count}
    if spec.predicate == GoalPredicate.FARMED and spec.item in _SEEDS:
        return {"item": _SEEDS[spec.item], "count": min(spec.count, _ALSO_SEEDS_MAX)}
    return None
