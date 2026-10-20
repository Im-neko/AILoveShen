"""街の理想図（区画ごとの用途、docs/design/47 §6）のテスト。"""

from unittest.mock import AsyncMock, Mock

import time

import pytest

from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.town import MOVE_CONDITIONS, TownPlanner
from ailoveshen.application.use_cases.town_map import apply_areas, base_zones, town_map_schema
from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.events import TownMapDrawnEvent
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    Mission,
    TownDefinition,
    TownMap,
    TownSite,
    TownStage,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
    format_town_map,
)
from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore

MISSION = Mission("生き延びながら家を建て、街にしていく")
HERE = TownSite("here", 0, 0, "動物が多い", "ひだまり村")
VERSION = GamePromptTemplateBuilder().vocabulary_version()  # 段階は今の語彙で試した（書き直さない）
TOWN = TownDefinition(
    "安全で備えのある小さな街",
    (
        TownStage("備蓄", "冬に備える", unresolved=("倉庫",), tried_with=VERSION),
        TownStage("畑", "食べ物", unresolved=("畑",), tried_with=VERSION),
    ),
)


def _rows() -> list[dict]:
    """2 x 2 の区画: A1 に家、B1 は水、A2 は平ら、B2 はまだ見ていない。"""
    base = {"seen": 1, "water": 0, "lava": 0, "trees": 0, "built": 0, "stone": 0, "flat": 0.9,
            "height": 0, "facilities": []}  # fmt: skip
    return [
        {**base, "id": "A1", "min_x": -8, "min_z": -8, "max_x": -1, "max_z": -1,
         "facilities": [{"name": "home", "kind": "home"}]},
        {**base, "id": "B1", "min_x": 0, "min_z": -8, "max_x": 7, "max_z": -1, "water": 0.8},
        {**base, "id": "A2", "min_x": -8, "min_z": 0, "max_x": -1, "max_z": 7},
        {**base, "id": "B2", "min_x": 0, "min_z": 0, "max_x": 7, "max_z": 7, "seen": 0},
    ]  # fmt: skip


ANSWER = {
    "concept": "家のそばに畑",
    "areas": [{"use": "farm", "zones": ["A2"], "facility": "wheat_farm", "note": "小麦"}],
}
# 道の骨格のある図（planner が描く）では、家の南の A2 が道になる。畑はその東の B2（道に面する）
ANSWER_ON_ROAD = {**ANSWER, "areas": [{**ANSWER["areas"][0], "zones": ["B2"]}]}


def test_the_code_fixes_zones_with_facilities_and_water():
    zones = {z.id: z for z in base_zones(_rows())}
    assert (zones["A1"].use, zones["A1"].facility, zones["A1"].fixed) == ("home", "home", True)
    assert (zones["B1"].use, zones["B1"].fixed) == ("unusable", True)
    assert (zones["A2"].use, zones["A2"].fixed) == ("free", False)
    free = town_map_schema(["A2", "B2"])["properties"]["areas"]["items"]["properties"]["zones"]
    assert free["items"]["enum"] == ["A2", "B2"]


def test_areas_are_applied_and_unlisted_zones_stay_free():
    m = apply_areas(base_zones(_rows()), ANSWER, (0, 0), 0, (), "街を定めた")
    assert m.zone("A2").use == "farm" and m.zone("A2").facility == "wheat_farm"
    assert m.zone("B2").use == "free"
    assert m.history == ("街を定めた",)


@pytest.mark.parametrize(
    "areas, error",
    [
        ([{"use": "farm", "zones": ["A1"], "note": ""}], "A1 is fixed"),
        ([{"use": "farm", "zones": ["Z9"], "note": ""}], "no zone Z9"),
        ([{"use": "farm", "zones": ["A2", "A2"], "note": ""}], "A2 is used twice"),
        ([{"use": "castle", "zones": ["A2"], "note": ""}], "use 'castle'"),
        ([{"use": "farm", "zones": ["A2"], "facility": "home", "note": ""}], "already used"),
    ],
)
def test_a_bad_answer_is_sent_back_with_the_reason(areas, error):
    with pytest.raises(ValueError, match=error):
        apply_areas(base_zones(_rows()), {"concept": "c", "areas": areas}, (0, 0), 0, (), "r")


def test_the_prompt_shows_the_grid_the_fixed_zones_and_the_terrain():
    rows = _rows()
    prompt = GamePromptTemplateBuilder().build_town_map_prompt(
        TOWN, 0, rows, base_zones(rows), None, "街を定めた"
    )
    assert "     A B\n   1 H ~\n   2 . ?" in prompt
    assert "- A1: home（home）" in prompt
    assert "- A2: 平ら 0.9" in prompt and "- B2: まだ見ていない" in prompt
    assert "1. [今] 備蓄: 冬に備える" in prompt


def test_the_goal_decision_sees_a_summary():
    m = apply_areas(base_zones(_rows()), ANSWER, (0, 0), 0, (), "r")
    text = format_town_map(m)
    assert "考え: 家のそばに畑" in text
    assert "- home: A1（home）" in text
    assert "- farm: A2（wheat_farm、予定: 小麦）" in text
    assert "free 1 区画" in text and "unusable 1 区画" in text
    assert format_town_map(None) == ""


def test_the_map_is_saved_and_restored(tmp_path):
    plan = ObjectivePlan(mission=MISSION)
    plan.set_town_map(apply_areas(base_zones(_rows()), ANSWER, (0, 0), 0, (), "r"))
    store = JsonMissionStore(tmp_path / "mission.json")
    store.save(plan)
    assert store.load().town_map == plan.town_map


def _planner(answers, zones=None):
    bridge = AsyncMock()
    bridge.zones.return_value = zones if zones is not None else _rows()
    text_generator = AsyncMock()
    text_generator.generate_json.side_effect = answers
    events, store = AsyncMock(), Mock()
    planner = TownPlanner(
        text_generator=text_generator,
        prompt_builder=GamePromptTemplateBuilder(),
        bridge=bridge,
        event_publisher=events,
        character=CharacterProfile(),
        store=store,
        objectives=ObjectiveKeeper(bridge=bridge, event_publisher=events, store=store),
        designer=AsyncMock(),
    )
    return planner, text_generator, events, store


def _defined(town_stage=0, town_map=None) -> ObjectivePlan:
    plan = ObjectivePlan(mission=MISSION)
    plan.restore([], [], 1, town=TOWN, town_stage=town_stage, site=HERE, town_map=town_map)
    return plan


@pytest.mark.asyncio
async def test_the_map_is_drawn_once_after_the_town_retrying_a_bad_answer():
    planner, text_generator, events, store = _planner(
        [{"concept": "c", "areas": [{"use": "farm", "zones": ["A1"], "note": ""}]}, ANSWER_ON_ROAD]
    )
    plan = _defined()

    await planner.prepare(plan)

    assert plan.town_map.zone("A2").use == "road" and plan.town_map.zone("A2").fixed
    assert plan.town_map.zone("B2").use == "farm"
    planner._bridge.set_town_layout.assert_awaited_once()  # 敷く道の列と壊す施設をブリッジに送る（設計書 167）
    strips, clear = planner._bridge.set_town_layout.await_args.args
    assert strips and clear == []
    assert plan.town_map.concept == "家のそばに畑"
    assert text_generator.generate_json.call_args.kwargs["purpose"] == "town_plan"
    assert "A1 is fixed" in text_generator.generate_json.call_args.args[0]
    store.save.assert_called()
    drawn = [c.args[0] for c in events.publish.call_args_list]
    assert isinstance(drawn[-1], TownMapDrawnEvent) and not drawn[-1].revised

    await planner.prepare(plan)  # 段階が同じなら描き直さない
    assert text_generator.generate_json.await_count == 2


@pytest.mark.asyncio
async def test_the_map_is_redrawn_when_the_stage_advances():
    old = TownMap(0, 0, tuple(base_zones(_rows())), concept="前", stage=0, history=("街を定めた",))
    planner, text_generator, events, _ = _planner([ANSWER_ON_ROAD])
    plan = _defined(town_stage=1, town_map=old)

    await planner.prepare(plan)

    assert plan.town_map.stage == 1
    assert plan.town_map.history == ("街を定めた", "段階 2「畑」に進んだ")
    assert "## 今の理想図（描き直す" in text_generator.generate_json.call_args.args[0]
    assert events.publish.call_args.args[0].revised


@pytest.mark.asyncio
async def test_no_map_while_moving_and_an_error_after_the_last_try():
    planner, text_generator, _, _ = _planner([])
    plan = _defined()
    plan.add("E に引っ越す", (MOVE_CONDITIONS[0],), "r", prepares_town=True)
    await planner.prepare(plan)
    text_generator.generate_json.assert_not_awaited()

    planner, text_generator, _, _ = _planner([{"concept": ""}] * 3)
    plan = _defined()
    await planner.prepare(plan)  # 描けなくても止めない（2026-10-19: 出力の上限で切れ続け、プレイを始められなかった）
    assert plan.town_map is None and text_generator.generate_json.await_count == 3
    await planner.prepare(plan)  # しばらくは頼み直さない
    assert text_generator.generate_json.await_count == 3


@pytest.mark.asyncio
async def test_a_map_that_could_not_be_drawn_keeps_the_old_one_and_is_asked_again_later(monkeypatch):
    old = TownMap(0, 0, tuple(base_zones(_rows())), concept="前", stage=0)
    planner, text_generator, _, _ = _planner([TextGenerationError("cut at 8192"), ANSWER_ON_ROAD])
    plan = _defined(town_stage=1, town_map=old)

    await planner.prepare(plan)
    assert plan.town_map is old

    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 301)
    await planner.prepare(plan)
    assert plan.town_map.stage == 1


def test_an_area_that_touches_no_road_is_sent_back_and_a_mine_may_be_off_the_road():
    from ailoveshen.application.use_cases.town_map import draw_base

    base = draw_base(_rows(), None)
    assert {z.id: z.use for z in base} == {"A1": "home", "B1": "unusable", "A2": "road", "B2": "free"}
    far = [{"use": "workshop", "zones": ["B2"], "facility": "shop", "note": "作業場"}]
    assert apply_areas(base, {"concept": "c", "areas": far}, (0, 0), 0, (), "r").zone("B2").use == "workshop"
    big = []
    for r in range(12):
        for c in range(12):
            zid = f"{chr(65 + c)}{r + 1}"
            big.append({"id": zid, "min_x": c * 8, "min_z": r * 8, "max_x": c * 8 + 7, "max_z": r * 8 + 7, "seen": 1,
                        "water": 0, "lava": 0, "trees": 0, "built": 0, "stone": 0, "flat": 0.9, "height": 0,
                        "facilities": [{"name": "home", "kind": "home"}] if zid == "F6" else []})  # fmt: skip
    base = draw_base(big, None)
    with pytest.raises(ValueError, match="touches no road"):
        apply_areas(base, {"concept": "c", "areas": [{"use": "farm", "zones": ["A12"], "facility": "f", "note": ""}]}, (0, 0), 0, (), "r")
    ok = {"concept": "c", "areas": [{"use": "mine", "zones": ["A12"], "facility": "m", "note": ""}]}
    assert apply_areas(base, ok, (0, 0), 0, (), "r").zone("A12").use == "mine"
    with pytest.raises(ValueError, match="use 'road'"):
        apply_areas(base, {"concept": "c", "areas": [{"use": "road", "zones": ["A12"], "note": ""}]}, (0, 0), 0, (), "r")


def test_the_prompt_shows_the_fixed_roads_on_the_map_and_tells_the_rule():
    from ailoveshen.application.use_cases.town_map import draw_base

    rows = _rows()
    base = draw_base(rows, None)
    prompt = GamePromptTemplateBuilder().build_town_map_prompt(TOWN, 0, rows, base, None, "r")
    assert "= 道（固定）" in prompt
    assert "- 道（1 区画）: A2" in prompt
    assert "道に面した" in prompt
    assert "- A2: 平ら" not in prompt  # 道は空き区画の地形に入れない


def _rows_with_old_farm() -> list[dict]:
    """A1 に家、B1 に古い畑 old_farm、A2 は空き（家の南の道になる）、B2 は空き。"""
    rows = _rows()
    rows[1] = {**rows[1], "water": 0, "facilities": [{"name": "old_farm", "kind": "farm"}]}
    rows[3] = {**rows[3], "seen": 1}
    return rows


def test_a_redraw_may_take_down_an_existing_building_and_reuse_its_zones():
    from ailoveshen.application.use_cases.town_map import draw_base, removable_facilities

    base = draw_base(_rows_with_old_farm(), None)
    assert removable_facilities(base) == {"old_farm": ["B1"]}
    schema = town_map_schema([z.id for z in base if not z.fixed], removable_facilities(base))
    props = schema["properties"]
    assert props["clear"]["items"]["properties"]["facility"]["enum"] == ["old_farm"]
    assert "B1" in props["areas"]["items"]["properties"]["zones"]["items"]["enum"]
    answer = {
        "concept": "畑を南へ",
        "clear": [{"facility": "old_farm", "why": "道の正面をふさぐ"}],
        "areas": [{"use": "workshop", "zones": ["B1"], "facility": "shop", "note": "作業場"}],
    }
    m = apply_areas(base, answer, (0, 0), 1, (), "r")
    assert m.clear == (("old_farm", "道の正面をふさぐ"),)
    assert m.zone("B1").use == "workshop" and not m.zone("B1").fixed
    # 壊すと書かなければ、固定のまま使えない。家と、ない名前は壊せない
    with pytest.raises(ValueError, match="B1 is fixed"):
        apply_areas(base, {**answer, "clear": []}, (0, 0), 1, (), "r")
    with pytest.raises(ValueError, match="cannot be taken down"):
        apply_areas(base, {**answer, "clear": [{"facility": "home", "why": "x"}]}, (0, 0), 1, (), "r")
    # 壊す施設の名前は、壊すまで新しい施設に使えない
    reuse = {**answer, "areas": [{"use": "farm", "zones": ["B1"], "facility": "old_farm", "note": ""}]}
    with pytest.raises(ValueError, match="already used"):
        apply_areas(base, reuse, (0, 0), 1, (), "r")


def test_the_prompt_lists_what_may_be_taken_down_and_the_map_shows_the_marks(tmp_path):
    from ailoveshen.application.use_cases.town_map import draw_base

    rows = _rows_with_old_farm()
    base = draw_base(rows, None)
    prompt = GamePromptTemplateBuilder().build_town_map_prompt(TOWN, 0, rows, base, None, "r")
    assert "## 壊してよい既存の建築" in prompt and "- old_farm: B1" in prompt and "clear に書く" in prompt
    plain = GamePromptTemplateBuilder().build_town_map_prompt(TOWN, 0, _rows(), draw_base(_rows(), None), None, "r")
    assert "壊してよい既存の建築" not in plain
    m = apply_areas(base, {"concept": "c", "clear": [{"facility": "old_farm", "why": "道をふさぐ"}], "areas": []}, (0, 0), 1, (), "r")
    text = format_town_map(m)
    assert "## 計画に合わない既存の建築" in text and "- old_farm: 道をふさぐ" in text
    store = JsonMissionStore(tmp_path / "m.json")
    plan = ObjectivePlan(mission=MISSION)
    plan.restore([], [], 1, town=TOWN, town_stage=0, site=HERE, town_map=m)
    store.save(plan)
    assert store.load().town_map.clear == (("old_farm", "道をふさぐ"),)
