"""街の道の骨格（docs/design/167）のテスト。"""

from ailoveshen.application.use_cases.town_roads import (
    MAX_LINK,
    MAX_ROAD_ZONES,
    SPINE,
    neighbours,
    plan_roads,
    touches_road,
    with_roads,
    zone_name,
    zone_xy,
)
from ailoveshen.domain.value_objects import Zone


def _grid(special: dict[str, tuple[str, str]] | None = None, size: int = 12) -> list[Zone]:
    """size x size の空き区画。special は id → (用途, 施設)で固定にする。"""
    out = []
    for r in range(size):
        for c in range(size):
            zid = zone_name(c, r)
            use, facility = (special or {}).get(zid, ("free", ""))
            out.append(Zone(zid, c * 8, r * 8, c * 8 + 7, r * 8 + 7, use=use, facility=facility, fixed=use != "free"))
    return out


def _picture(zones: list[Zone], size: int = 12) -> list[str]:
    mark = {"road": "#", "home": "H", "unusable": "~", "farm": "F", "free": "."}
    return ["".join(mark.get(zones[r * size + c].use, "?") for c in range(size)) for r in range(size)]


def test_ids_and_neighbours():
    assert zone_xy("A1") == (0, 0) and zone_xy("L12") == (11, 11) and zone_name(11, 11) == "L12"
    assert set(neighbours("B2")) == {"B1", "C2", "B3", "A2"}
    assert set(neighbours("A1")) == {"B1", "A2"}  # 地図の外（負）は返さない


def test_a_cross_of_avenues_runs_out_from_the_home():
    base = _grid({"F6": ("home", "home")})
    roads = plan_roads(base)
    assert len(roads) == 4 * SPINE
    picture = _picture(with_roads(base, roads))
    assert picture[5] == "." + "#" * 4 + "H" + "#" * 4 + ".."
    assert [row[5] for row in picture] == list("." * 1 + "#" * 4 + "H" + "#" * 4 + "." * 2)
    zones = {z.id: z for z in with_roads(base, roads)}
    assert all(touches_road(zid, zones) for zid in ("E5", "G7", "G5"))


def test_an_avenue_stops_at_water_and_never_crosses_a_facility():
    base = _grid({"F6": ("home", "home"), "F4": ("unusable", ""), "I6": ("farm", "farm")})
    roads = plan_roads(base)
    assert "F5" in roads and "F4" not in roads and "F3" not in roads  # 北は水で止まる
    assert "G6" in roads and "H6" in roads and "I6" not in roads and "J6" not in roads  # 東は畑で止まる


def test_a_facility_off_the_avenues_is_linked_by_the_shortest_free_path():
    base = _grid({"F6": ("home", "home"), "K9": ("farm", "farm")})
    roads = plan_roads(base)
    after = with_roads(base, roads)
    zones = {z.id: z for z in after}
    assert touches_road("K9", zones)
    assert len(roads) <= MAX_ROAD_ZONES
    # 道から MAX_LINK 区画より遠い施設は、つながない（20 x 20 の地図の隅）
    far = _grid({"J10": ("home", "home"), "T20": ("farm", "farm")}, size=20)
    assert not touches_road("T20", {z.id: z for z in with_roads(far, plan_roads(far))})
    near = _grid({"J10": ("home", "home"), "O13": ("farm", "farm")}, size=20)
    assert touches_road("O13", {z.id: z for z in with_roads(near, plan_roads(near))})
    assert MAX_LINK == 8


def test_old_roads_stay_when_they_are_still_free_and_are_dropped_when_something_stands_there():
    base = _grid({"F6": ("home", "home"), "B2": ("farm", "farm")})
    roads = plan_roads(base, ("B3", "B2", "L1"))
    assert "B3" in roads and "L1" in roads  # 空いているので残す
    assert "B2" not in roads  # 畑が建った


def test_no_home_starts_from_the_middle_and_no_free_zone_means_no_road():
    base = _grid()
    roads = plan_roads(base)
    assert roads and roads[0] in {"F6", "G6", "F7", "G7"}
    solid = _grid({zone_name(c, r): ("unusable", "") for r in range(3) for c in range(3)}, size=3)
    assert plan_roads(solid) == []
