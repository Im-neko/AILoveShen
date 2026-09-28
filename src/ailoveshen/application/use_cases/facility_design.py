"""
施設の設計（docs/design/47 §7、§16）: 畑・植林場・採掘場は、作る前に配置を決める。

まだない名前の `facility(name)` を中目標に足すとき、`MidGoalKeeper` が呼ぶ。街の理想図に
その名前の予定の区画があればそこに、なければ空いた区画から LLM が選ぶ（理想図がまだなければ
家のまわり）。LLM が決めるのは種類・区画・大きさ・作物か苗木・採掘場の深さと坑道。実際のマス
（耕せる・植えられる地面、安全に下りられる階段）はブリッジが区画の中から選んで登録する。登録したら、理想図のその区画に施設を書き込む。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.exceptions import (
    FacilityAreaNotReadyError,
    FacilityNotDesignedError,
    GameBridgeError,
    GoalRejectedError,
    TextGenerationError,
)
from ailoveshen.domain.value_objects import TownMap, Zone

KINDS = ("farm", "tree_farm", "mine")
CROPS = ("wheat", "carrots", "potatoes", "beetroots")
FARM_SIDE = (1, 16)  # minecraft-bridge/src/facilities.mjs の FARM_SIDE
TREES = (1, 32)
SPACING = (4, 6)  # 苗木どうしの間（ブリッジの植える規則 TREE_GAP 以上）
MINE_BOTTOM = (-58, 100)  # 採掘場の底の y（1.21.4 の岩盤は y -64〜-60）
TUNNELS = (0, 4)
TUNNEL_LENGTH = (4, 32)
MAX_ZONES = 4
MAX_NOTE = 60


def facility_schema(zone_ids: list[str]) -> dict[str, Any]:
    """施設の設計の出力の形。区画は理想図があるときだけ。"""
    props: dict[str, Any] = {
        "kind": {"type": "string", "enum": list(KINDS)},
        "crop": {"type": "string", "enum": list(CROPS), "description": "farm: the crop"},
        "width": {
            "type": "integer",
            "minimum": FARM_SIDE[0],
            "maximum": FARM_SIDE[1],
            "description": "farm: blocks east-west",
        },
        "depth": {
            "type": "integer",
            "minimum": FARM_SIDE[0],
            "maximum": FARM_SIDE[1],
            "description": "farm: blocks north-south",
        },
        "sapling": {
            "type": "string",
            "description": "tree_farm: sapling (any kind) or one kind like oak_sapling",
        },
        "count": {
            "type": "integer",
            "minimum": TREES[0],
            "maximum": TREES[1],
            "description": "tree_farm: how many trees",
        },
        "spacing": {
            "type": "integer",
            "minimum": SPACING[0],
            "maximum": SPACING[1],
            "description": "tree_farm: blocks between saplings",
        },
        "bottom_y": {
            "type": "integer",
            "minimum": MINE_BOTTOM[0],
            "maximum": MINE_BOTTOM[1],
            "description": "mine: the y of the bottom (iron is most common around y 16, "
            "diamonds below y 0, coal and copper higher)",
        },
        "tunnels": {
            "type": "integer",
            "minimum": TUNNELS[0],
            "maximum": TUNNELS[1],
            "description": "mine: straight tunnels out from the bottom room (they show ores)",
        },
        "tunnel_length": {
            "type": "integer",
            "minimum": TUNNEL_LENGTH[0],
            "maximum": TUNNEL_LENGTH[1],
            "description": "mine: blocks per tunnel",
        },
        "reason": {
            "type": "string",
            "description": "Why this place and size, one Japanese sentence",
        },
    }
    required = ["kind", "reason"]
    if zone_ids:
        props["zones"] = {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_ZONES,
            "items": {"type": "string", "enum": zone_ids},
            "description": "Zones next to each other that make one rectangle",
        }
        required.insert(1, "zones")
    return {"type": "object", "properties": props, "required": required}


def _design(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """LLM の答えから種類とブリッジに送る設計。形が足りなければ ValueError。"""
    kind = str(data.get("kind") or "")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    if kind == "farm":
        missing = [k for k in ("crop", "width", "depth") if data.get(k) is None]
        if missing:
            raise ValueError(f"a farm needs {', '.join(missing)}")
        return kind, {
            "crop": data["crop"],
            "width": int(data["width"]),
            "depth": int(data["depth"]),
        }
    if kind == "mine":
        if data.get("bottom_y") is None:
            raise ValueError("a mine needs bottom_y")
        tunnels = int(data.get("tunnels") or 0)
        design = {"bottom_y": int(data["bottom_y"]), "tunnels": tunnels}
        if tunnels:
            design["tunnel_length"] = int(data.get("tunnel_length") or 16)
        return kind, design
    if data.get("count") is None:
        raise ValueError("a tree_farm needs count")
    return kind, {
        "sapling": str(data.get("sapling") or "sapling"),
        "count": int(data["count"]),
        "spacing": int(data.get("spacing") or SPACING[0]),
    }


def _rectangle(zones: list[Zone], chosen: list[str], name: str) -> list[Zone]:
    """選んだ区画が 1 つの四角になり、ほかの用途の区画を含まないか。区画の一覧を返す。"""
    by_id = {z.id: z for z in zones}
    unknown = [z for z in chosen if z not in by_id]
    if unknown:
        raise ValueError(f"there is no zone {', '.join(unknown)}")
    picked = [by_id[z] for z in chosen]
    min_x = min(z.min_x for z in picked)
    max_x = max(z.max_x for z in picked)
    min_z = min(z.min_z for z in picked)
    max_z = max(z.max_z for z in picked)
    inside = [z for z in zones if z.min_x >= min_x and z.max_x <= max_x and z.min_z >= min_z
              and z.max_z <= max_z]  # fmt: skip
    extra = [z for z in inside if z.id not in chosen]
    if extra:
        ids = ", ".join(z.id for z in extra)
        raise ValueError(f"zones {', '.join(chosen)} do not make one rectangle (also covers {ids})")
    taken = [z for z in picked if z.fixed or (z.facility and z.facility != name)]
    if taken:
        what = ", ".join(f"{z.id} ({z.use} {z.facility or z.note})" for z in taken)
        raise ValueError(f"these zones are taken: {what}")
    return picked


class FacilityDesigner:
    """施設（畑・植林場・採掘場）を設計して登録し、理想図に書き込む。"""

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        plan: MidGoalPlan,
        store: IMissionStore,
        max_attempts: int = 3,
    ) -> None:
        """
        Args:
            text_generator: LLM（構造化出力。purpose=facility_design）
            prompt_builder: プロンプトの組み立て
            bridge: 区画の地形を読み、設計を登録する
            plan: 大目標のプラン（理想図を読み、書き込む。オブジェクトは差し替えられない）
            store: 理想図を書き込んだら保存する
            max_attempts: あきらめるまでに設計させる回数
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._plan = plan
        self._store = store
        self._max_attempts = max_attempts

    async def known(self) -> set[str]:
        """登録済みの施設の名前（取り込んだものも）。"""
        return {str(f["name"]) for f in await self._bridge.facilities()}

    async def design(self, name: str, brief: str) -> dict[str, Any]:
        """
        施設を設計して登録する。登録した施設（ブリッジの形）を返す。

        Raises:
            FacilityNotDesignedError: 使える設計が出なかった、区画が読み込まれていない、
                ブリッジにつながらない
        """
        town_map = self._plan.town_map
        rows: list[dict[str, Any]] = []
        if town_map is not None:
            try:
                rows = await self._bridge.zones(town_map.center_x, town_map.center_z)
            except GameBridgeError as e:
                raise FacilityNotDesignedError(str(e), name=name) from e
        planned = [z.id for z in town_map.zones if z.facility == name] if town_map else []
        choices = (
            [z.id for z in town_map.zones if not z.fixed and z.use in ("free", *KINDS)
             and (not z.facility or z.facility == name)]  # fmt: skip
            if town_map
            else []
        )
        schema = facility_schema(choices)
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_facility_prompt(
                name, brief, town_map, rows, planned, previous_error=error
            )
            try:
                data = await self._text_generator.generate_json(
                    prompt, schema, purpose="facility_design"
                )
                kind, design = _design(data)
                body: dict[str, Any] = {"kind": kind, "purpose": brief, "design": design}
                zones: list[Zone] = []
                if town_map is not None:
                    chosen = [str(z) for z in data.get("zones") or []]
                    if not chosen:
                        raise ValueError("choose the zones for it")
                    zones = _rectangle(list(town_map.zones), chosen, name)
                    body["zones"] = chosen
                    body["boxes"] = [
                        {"min_x": z.min_x, "min_z": z.min_z, "max_x": z.max_x, "max_z": z.max_z}
                        for z in zones
                    ]
                else:
                    body["near_home"] = True
                facility = await self._bridge.set_facility(name, body)
            except FacilityAreaNotReadyError as e:
                raise FacilityNotDesignedError(str(e), name=name) from e
            except (ValueError, GoalRejectedError, TextGenerationError) as e:
                error = str(e)
                logger.warning(f"施設 {name} の設計の試行 {attempt} を差し戻した: {error}")
                continue
            except GameBridgeError as e:  # ブリッジにつながらない: やり直しても同じ
                raise FacilityNotDesignedError(str(e), name=name) from e
            logger.info(f"施設 {name} を設計した: {kind} {design}（{data.get('reason', '')}）")
            if town_map is not None:
                self._write_map(town_map, name, kind, zones, str(data.get("reason") or brief))
            return facility
        raise FacilityNotDesignedError(
            f"facility {name} was not designed after {self._max_attempts} attempts: {error}",
            name=name,
        )

    def _write_map(
        self, town_map: TownMap, name: str, kind: str, zones: list[Zone], note: str
    ) -> None:
        """理想図の区画に施設を書き込む（前にその名前で予定した区画のうち使わないものは空きに）。"""
        used = {z.id for z in zones}
        written = []
        for z in town_map.zones:
            if z.id in used:
                written.append(replace(z, use=kind, facility=name, note=note[:MAX_NOTE]))
            elif z.facility == name and not z.fixed:
                written.append(replace(z, use="free", facility="", note=""))
            else:
                written.append(z)
        self._plan.set_town_map(
            replace(
                town_map,
                zones=tuple(written),
                history=(*town_map.history, f"施設 {name} を設計した")[-10:],
            )
        )
        self._store.save(self._plan)
