"""
街の理想図（docs/design/47 §6）: 区画ごとの用途を LLM に決めさせ、コードが確かめて持つ。

区画の範囲と地形はブリッジ（`GET /zones`）が測る。今ある施設の区画と、水・溶岩の多い区画は
コードが先に決めて固定する（描き直しでも動かさない。今の畑を忘れて別の区画を描かないため）。
LLM は残りの区画に用途をまとめて割り当てる（`areas`）。書かれなかった区画は空き（free）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ailoveshen.application.use_cases.town_roads import plan_roads, touches_road, with_roads
from ailoveshen.domain.value_objects import FACILITY_USE, ZONE_USES, TownMap, Zone

UNUSABLE_WATER = 0.5  # 水がこれ以上の区画は使えない
MAX_HISTORY = 10
# LLM が割り当てられる用途（home は今ある家、unusable は地形で、road は道の骨格でコードが決める。設計書 167）
ASSIGNABLE = tuple(u for u in ZONE_USES if u not in ("home", "unusable", "road"))
# 道に面していなくてもよい用途（採掘場は石のある所に作る）
OFF_ROAD_USES = ("mine", "free")
# 再設計のとき、計画に合わなければ壊してよい既存の建築（家は住んでいる所、採掘場は地下なので壊さない）
REMOVABLE_USES = ("building", "farm", "tree_farm")
MAX_CLEAR_WHY = 60


def removable_facilities(base: list[Zone]) -> dict[str, list[str]]:
    """壊してよい既存の施設の名前 → その区画の id。"""
    out: dict[str, list[str]] = {}
    for z in base:
        if z.fixed and z.use in REMOVABLE_USES:
            for name in (n.strip() for n in z.facility.split(",") if n.strip()):
                out.setdefault(name, []).append(z.id)
    return out
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


def draw_base(rows: list[dict[str, Any]], current: TownMap | None) -> list[Zone]:
    """描く前の区画: 施設・地形の固定に、道の骨格を足したもの。前の理想図の道は、空いていれば残す。"""
    base = base_zones(rows)
    kept = tuple(z.id for z in current.zones if z.use == "road") if current else ()
    return with_roads(base, plan_roads(base, kept))


def town_map_schema(
    free_ids: list[str], removable: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    """
    理想図の出力の形: 全体の考えと、用途ごとの区画のまとまり。`removable` があれば、計画に合わない
    既存の建築を壊す指定（clear）もできる。壊すと書いた施設の区画も、areas に使える。
    """
    removable = removable or {}
    zone_ids = sorted({*free_ids, *(z for ids in removable.values() for z in ids)})
    schema = _town_map_schema(zone_ids)
    if removable:
        props = schema["properties"]
        props["clear"] = {
            "type": "array",
            "description": "Existing buildings or facilities that do not fit this layout and are taken "
            "down (the site is levelled). Only what really is in the way; what is in use stays. "
            "Their zones may be used in areas",
            "items": {
                "type": "object",
                "properties": {
                    "facility": {"type": "string", "enum": sorted(removable)},
                    "why": {
                        "type": "string",
                        "description": f"Why it does not fit, Japanese, {MAX_CLEAR_WHY} characters at most",
                    },
                },
                "required": ["facility", "why"],
            },
        }
    return schema


def _town_map_schema(free_ids: list[str]) -> dict[str, Any]:
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
                "description": "Groups of zones with one use each. Zones not listed stay free. "
                "Roads are already fixed: every area except a mine has a zone next to a road",
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
                            "and _, e.g. wheat_farm_2); empty for plazas and free land",
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
    roads = any(z.use == "road" for z in base)
    removable = removable_facilities(base)
    clear: list[tuple[str, str]] = []
    for i, c in enumerate(data.get("clear") or []):
        name = str(c.get("facility") or "")
        if name not in removable:
            errors.append(f"clear {i + 1}: {name!r} cannot be taken down (one of {sorted(removable) or 'none'})")
        elif name not in [n for n, _ in clear]:
            clear.append((name, str(c.get("why") or "").strip()[:MAX_CLEAR_WHY]))
    gone = {n for n, _ in clear}
    for zid in {z for n in gone for z in removable[n]}:
        zone = zones[zid]
        left = [n.strip() for n in zone.facility.split(",") if n.strip() and n.strip() not in gone]
        if not left:  # この区画の施設は全部壊す: 空き区画になる
            zones[zid] = replace(zone, use="free", facility="", note="", fixed=False)
    names: set[str] = {n.strip() for z in base for n in z.facility.split(",") if n.strip()}  # 壊すまで名前は使えない
    base = [zones[z.id] for z in base]
    used: set[str] = set()
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
        if (
            roads
            and use not in OFF_ROAD_USES
            and not any(touches_road(str(zid), zones) for zid in area.get("zones") or [])
        ):
            errors.append(
                f"area {i + 1}: {use} {list(area.get('zones') or [])} touches no road; put a zone "
                "of it next to a road zone (roads are fixed, see the map)"
            )
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
        clear=tuple(clear),
    )

