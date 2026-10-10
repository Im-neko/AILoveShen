"""
街の道の骨格（docs/design/167）: 施設を置く前に、コードが道の区画を決める。

道は街の背骨で、LLM には描かせない（つながっていない道、施設をふさぐ道を防ぐ）。LLM は道に面した
区画に用途を割り当てる。決め方は決まりきったもの:
  1. 家（なければ真ん中に近い空き区画）から、東西南北に最大 SPINE 区画の大通りを延ばす。固定の区画（施設・
     水・溶岩）に当たったら、そこで止める
  2. 道に面していない施設（固定の区画）を、近い順に道へつなぐ。空き区画だけを通る最短の道で、MAX_LINK 区画
     より遠ければ諦める
  3. 前の理想図の道は、まだ空いていれば残す（描き直しで道が動かないように。作った道を無駄にしない）
区画の id は列の文字（A.. が西→東）と行の数（1.. が北→南）。
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace

from ailoveshen.domain.value_objects import Zone

SPINE = 4  # 家から各方向へ延ばす大通りの長さ（区画）
MAX_LINK = 8  # 施設を道につなぐ道の長さの上限（区画）
MAX_ROAD_ZONES = 28  # 道の区画の数の上限（街 144 区画の約 2 割）
ROAD_NOTE = "道"
_STEPS = ((0, -1), (1, 0), (0, 1), (-1, 0))  # 北、東、南、西


def zone_xy(zone_id: str) -> tuple[int, int]:
    """区画 id → (列, 行)。A1 は (0, 0)。"""
    return ord(zone_id[0]) - 65, int(zone_id[1:]) - 1


def zone_name(col: int, row: int) -> str:
    """(列, 行) → 区画 id。"""
    return f"{chr(65 + col)}{row + 1}"


def neighbours(zone_id: str) -> list[str]:
    """辺で隣り合う区画の id（地図の外も含む。使う側で地図にあるものだけ見る）。"""
    col, row = zone_xy(zone_id)
    return [zone_name(col + dc, row + dr) for dc, dr in _STEPS if col + dc >= 0 and row + dr >= 0]


def plan_roads(base: list[Zone], keep: tuple[str, ...] = ()) -> list[str]:
    """
    道にする区画の id（決めた順）。`keep` は前の理想図の道で、まだ空いていれば残す。

    空き = 固定でない区画。家・施設・水・溶岩の区画は通らない。
    """
    zones = {z.id: z for z in base}
    free = {zid for zid, z in zones.items() if not z.fixed}
    terminals = [z for z in base if z.fixed and z.use not in ("unusable", "road")]
    if not free:
        return []
    cols = [zone_xy(z.id)[0] for z in base]
    rows = [zone_xy(z.id)[1] for z in base]
    mid = ((min(cols) + max(cols)) / 2, (min(rows) + max(rows)) / 2)

    def away(zid: str) -> float:
        c, r = zone_xy(zid)
        return abs(c - mid[0]) + abs(r - mid[1])

    homes = sorted((z for z in terminals if z.use == "home"), key=lambda z: away(z.id))
    roads: list[str] = [zid for zid in keep if zid in free]

    def add(zid: str) -> bool:
        if zid in roads or zid not in free or len(roads) >= MAX_ROAD_ZONES:
            return False
        roads.append(zid)
        return True

    # 1. 大通り
    if homes:
        col, row = zone_xy(homes[0].id)
        for dc, dr in _STEPS:
            for k in range(1, SPINE + 1):
                if not add(zone_name(col + dc * k, row + dr * k)) and zone_name(col + dc * k, row + dr * k) not in roads:
                    break
    else:
        centre = min(free, key=away)
        add(centre)
        col, row = zone_xy(centre)
        for dc, dr in _STEPS:
            for k in range(1, SPINE + 1):
                zid = zone_name(col + dc * k, row + dr * k)
                if not add(zid) and zid not in roads:
                    break
    # 2. 道に面していない施設をつなぐ（家に近い順）
    root = zone_xy(homes[0].id) if homes else (round(mid[0]), round(mid[1]))
    for t in sorted(terminals, key=lambda z: abs(zone_xy(z.id)[0] - root[0]) + abs(zone_xy(z.id)[1] - root[1])):
        if any(n in roads for n in neighbours(t.id)):
            continue
        for zid in _link(t.id, free, set(roads)):
            add(zid)
    return roads


def _link(start: str, free: set[str], roads: set[str]) -> list[str]:
    """start の隣から、空き区画だけを通って今の道に辺で着く最短の道（道に着く手前まで）。着けなければ空。"""
    seen = {start}
    queue: deque[tuple[str, list[str]]] = deque([(start, [])])
    while queue:
        zid, path = queue.popleft()
        if len(path) > MAX_LINK:
            continue
        for n in neighbours(zid):
            if n in seen or n not in free:
                continue
            seen.add(n)
            if any(m in roads for m in neighbours(n)) or n in roads:
                return path if n in roads else [*path, n]
            queue.append((n, [*path, n]))
    return []


def with_roads(base: list[Zone], roads: list[str]) -> list[Zone]:
    """道の区画を、用途 road の固定の区画にした一覧。"""
    ids = set(roads)
    return [replace(z, use="road", note=ROAD_NOTE, fixed=True) if z.id in ids else z for z in base]


def touches_road(zone_id: str, zones: dict[str, Zone]) -> bool:
    """区画が、道の区画と辺で隣り合うか。"""
    return any(zones[n].use == "road" for n in neighbours(zone_id) if n in zones)
