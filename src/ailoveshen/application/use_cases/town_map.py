"""
街の理想図（docs/design/47 §6）: 区画ごとの用途を LLM に決めさせ、コードが確かめて持つ。

区画の範囲と地形はブリッジ（`GET /zones`）が測る。今ある施設の区画と、水・溶岩の多い区画は
コードが先に決めて固定する（描き直しでも動かさない。今の畑を忘れて別の区画を描かないため）。
LLM は残りの区画に用途をまとめて割り当てる（`areas`）。書かれなかった区画は空き（free）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ailoveshen.domain.value_objects import FACILITY_USE, ZONE_USES, TownMap, Zone

UNUSABLE_WATER = 0.5  # 水がこれ以上の区画は使えない
MAX_HISTORY = 10
# LLM が割り当てられる用途（home は今ある家、unusable は地形でコードが決める）
ASSIGNABLE = tuple(u for u in ZONE_USES if u not in ("home", "unusable"))
MAX_AREAS = 24
MAX_NOTE = 60


def base_zones(rows: list[dict[str, Any]]) -> list[Zone]:
    """
    ブリッジの区画（GET /zones）から、コードが決める区画: 施設のある区画はその用途で固定、
    水・溶岩の区画は unusable で固定、ほかは空き。
    """
    out = []
    for r in rows:
        box = {k: int(r[k]) for k in ("min_x", "min_z", "max_x", "max_z")}
        facilities = r.get("facilities") or []
        if facilities:
            f = facilities[0]
            names = ", ".join(str(x["name"]) for x in facilities)
            use = FACILITY_USE.get(str(f.get("kind")), "building")
            out.append(Zone(str(r["id"]), **box, use=use, facility=names, fixed=True))
        elif float(r.get("water", 0)) >= UNUSABLE_WATER or float(r.get("lava", 0)) > 0:
            what = "溶岩" if float(r.get("lava", 0)) > 0 else "水"
            out.append(Zone(str(r["id"]), **box, use="unusable", note=what, fixed=True))
        else:
            out.append(Zone(str(r["id"]), **box))
    return out


def town_map_schema(free_ids: list[str]) -> dict[str, Any]:
    """理想図の出力の形: 全体の考えと、用途ごとの区画のまとまり。"""
    return {
        "type": "object",
        "properties": {
            "concept": {
                "type": "string",
                "description": "The idea of the whole layout, one sentence in Japanese",
            },
            "areas": {
                "type": "array",
                "maxItems": MAX_AREAS,
                "description": "Groups of zones with one use each. Zones not listed stay free",
                "items": {
                    "type": "object",
                    "properties": {
                        "use": {"type": "string", "enum": list(ASSIGNABLE)},
                        "zones": {
                            "type": "array",
                            "items": {"type": "string", "enum": free_ids},
                            "description": "Zone ids (column letter + row number), next to each "
                            "other for one facility",
                        },
                        "facility": {
                            "type": "string",
                            "description": "A name for what will be made there (English lowercase "
                            "and _, e.g. wheat_farm_2); empty for roads, plazas and free land",
                        },
                        "note": {
                            "type": "string",
                            "description": f"What and why, Japanese, {MAX_NOTE} characters at most",
                        },
                    },
                    "required": ["use", "zones", "note"],
                },
            },
        },
        "required": ["concept", "areas"],
    }


def apply_areas(
    base: list[Zone],
    data: dict[str, Any],
    center: tuple[int, int],
    stage: int,
    history: tuple[str, ...],
    reason: str,
) -> TownMap:
    """
    LLM の答えを区画に当てはめた理想図。

    Raises:
        ValueError: 固定の区画・ない区画・同じ区画を 2 度使ったとき、用途が知らないものの
            とき、考えがないとき（理由をまとめて言い、LLM にやり直させる）
    """
    concept = str(data.get("concept") or "").strip()
    errors: list[str] = []
    if not concept:
        errors.append("concept is empty")
    zones = {z.id: z for z in base}
    used: set[str] = set()
    names: set[str] = {z.facility for z in base if z.facility}
    for i, area in enumerate(data.get("areas") or []):
        use = str(area.get("use") or "")
        if use not in ASSIGNABLE:
            errors.append(f"area {i + 1}: use {use!r} is not one of {list(ASSIGNABLE)}")
            continue
        facility = str(area.get("facility") or "").strip()
        if facility and facility in names:
            errors.append(f"area {i + 1}: the facility name {facility} is already used")
        if facility:
            names.add(facility)
        note = str(area.get("note") or "").strip()[:MAX_NOTE]
        for zid in area.get("zones") or []:
            zid = str(zid)
            zone = zones.get(zid)
            if zone is None:
                errors.append(f"area {i + 1}: there is no zone {zid}")
            elif zone.fixed:
                what = zone.facility or zone.note
                errors.append(f"area {i + 1}: {zid} is fixed ({zone.use}: {what}); leave it out")
            elif zid in used:
                errors.append(f"area {i + 1}: {zid} is used twice")
            else:
                used.add(zid)
                zones[zid] = replace(zone, use=use, facility=facility, note=note)
    if errors:
        raise ValueError("; ".join(errors))
    return TownMap(
        center_x=center[0],
        center_z=center[1],
        zones=tuple(zones[z.id] for z in base),
        concept=concept,
        stage=stage,
        history=(*history, reason)[-MAX_HISTORY:],
    )

