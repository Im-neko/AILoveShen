"""施設（畑・植林場）の設計（docs/design/47 §7）のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.facility_design import FacilityDesigner, facility_schema
from ailoveshen.application.use_cases.goal_vocabulary import (
    MidGoalProposal,
    PlanChange,
    PlanOp,
    parse_decision,
)
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.play import _without_build
from ailoveshen.application.use_cases.town_map import apply_areas, base_zones
from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.exceptions import (
    FacilityAreaNotReadyError,
    FacilityNotDesignedError,
    GameBridgeError,
    GoalRejectedError,
)
from ailoveshen.domain.value_objects import GoalPredicate, GoalSpec, Mission
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)

MISSION = Mission("生き延びながら家を建て、街にしていく")


def _rows() -> list[dict]:
    """2 x 2 の区画: A1 に家、B1 は水、A2 は平ら（畑の予定）、B2 も平ら。"""
    base = {"seen": 1, "water": 0, "lava": 0, "trees": 0, "built": 0, "stone": 0, "flat": 0.9,
            "height": 0, "facilities": []}  # fmt: skip
    return [
        {**base, "id": "A1", "min_x": -8, "min_z": -8, "max_x": -1, "max_z": -1,
         "facilities": [{"name": "home", "kind": "home"}]},
        {**base, "id": "B1", "min_x": 0, "min_z": -8, "max_x": 7, "max_z": -1, "water": 0.8},
        {**base, "id": "A2", "min_x": -8, "min_z": 0, "max_x": -1, "max_z": 7},
        {**base, "id": "B2", "min_x": 0, "min_z": 0, "max_x": 7, "max_z": 7},
    ]  # fmt: skip


PLANNED = {
    "concept": "家のそばに畑",
    "areas": [{"use": "farm", "zones": ["A2"], "facility": "wheat_farm", "note": "小麦"}],
}
FARM = {"kind": "farm", "zones": ["A2"], "crop": "wheat", "width": 8, "depth": 4, "reason": "近い"}


def _designer(answers, with_map=True, set_facility=None):
    plan = MidGoalPlan(mission=MISSION)
    if with_map:
        plan.set_town_map(apply_areas(base_zones(_rows()), PLANNED, (0, 0), 0, (), "街を定めた"))
    bridge = AsyncMock()
    bridge.zones.return_value = _rows()
    bridge.facilities.return_value = []
    bridge.set_facility.side_effect = set_facility or (lambda name, body: {"name": name, **body})
    text_generator = AsyncMock()
    text_generator.generate_json.side_effect = answers
    store = Mock()
    designer = FacilityDesigner(
        text_generator=text_generator,
        prompt_builder=GamePromptTemplateBuilder(),
        bridge=bridge,
        plan=plan,
        store=store,
    )
    return designer, plan, bridge, text_generator, store


@pytest.mark.asyncio
async def test_the_planned_zone_is_designed_registered_and_written_to_the_map():
    designer, plan, bridge, text_generator, store = _designer([FARM])

    await designer.design("wheat_farm", "食べ物を作る: パンのため")

    prompt = text_generator.generate_json.call_args.args[0]
    assert "理想図ではこの施設の区画は A2" in prompt
    assert "   1 H ~\n   2 f ." in prompt  # 予定の畑は小文字
    assert text_generator.generate_json.call_args.kwargs["purpose"] == "facility_design"
    name, body = bridge.set_facility.call_args.args
    assert name == "wheat_farm" and body["zones"] == ["A2"]
    assert body["boxes"] == [{"min_x": -8, "min_z": 0, "max_x": -1, "max_z": 7}]
    assert body["design"] == {"crop": "wheat", "width": 8, "depth": 4}
    zone = plan.town_map.zone("A2")
    assert (zone.use, zone.facility, zone.note) == ("farm", "wheat_farm", "近い")
    assert plan.town_map.history[-1] == "施設 wheat_farm を設計した"
    store.save.assert_called_once()


@pytest.mark.asyncio
async def test_moving_the_facility_frees_the_zones_planned_before():
    designer, plan, *_ = _designer([{**FARM, "zones": ["B2"]}])
    await designer.design("wheat_farm", "b")
    assert plan.town_map.zone("A2").use == "free" and plan.town_map.zone("B2").use == "farm"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first, error",
    [
        ({**FARM, "zones": ["B1"]}, "taken"),  # 水の区画（固定）
        ({**FARM, "zones": ["A2", "B1"]}, "do not make one rectangle"),
        ({**FARM, "zones": ["Z9"]}, "no zone Z9"),
        ({**FARM, "crop": None}, "a farm needs crop"),
        ({"kind": "tree_farm", "zones": ["B2"], "reason": "r"}, "needs count"),
    ],
)
async def test_a_bad_design_is_sent_back_with_the_reason(first, error):
    designer, _, bridge, text_generator, _ = _designer([first, FARM])
    await designer.design("wheat_farm", "b")
    assert error in text_generator.generate_json.call_args.args[0]
    assert bridge.set_facility.await_count == 1


@pytest.mark.asyncio
async def test_the_bridge_refusal_is_retried_but_an_unloaded_area_is_not():
    calls = []

    def refuse_once(name, body):
        calls.append(body)
        if len(calls) == 1:
            raise GoalRejectedError("not enough tillable ground for 8 x 4")
        return {"name": name}

    designer, _, _, text_generator, _ = _designer([FARM, FARM], set_facility=refuse_once)
    await designer.design("wheat_farm", "b")
    assert "not enough tillable ground" in text_generator.generate_json.call_args.args[0]

    def unloaded(name, body):
        raise FacilityAreaNotReadyError("the area is not loaded")

    designer, _, _, text_generator, _ = _designer([FARM, FARM], set_facility=unloaded)
    with pytest.raises(FacilityNotDesignedError) as e:
        await designer.design("wheat_farm", "b")
    assert e.value.name == "wheat_farm" and text_generator.generate_json.await_count == 1

    def down(name, body):
        raise GameBridgeError("connection refused")

    designer, *_ = _designer([FARM], set_facility=down)
    with pytest.raises(FacilityNotDesignedError, match="connection refused"):
        await designer.design("wheat_farm", "b")


@pytest.mark.asyncio
async def test_without_a_map_it_goes_near_the_home():
    tree = {"kind": "tree_farm", "sapling": "oak_sapling", "count": 4, "reason": "木材"}
    designer, plan, bridge, text_generator, store = _designer([tree], with_map=False)
    await designer.design("oak_grove", "木材")
    assert "zones" not in text_generator.generate_json.call_args.args[1]["properties"]
    body = bridge.set_facility.call_args.args[1]
    assert body["near_home"] and "boxes" not in body
    assert body["design"] == {"sapling": "oak_sapling", "count": 4, "spacing": 4}
    assert plan.town_map is None
    store.save.assert_not_called()


def test_the_schema_offers_only_the_free_zones():
    zones = facility_schema(["A2", "B2"])["properties"]["zones"]
    assert zones["items"]["enum"] == ["A2", "B2"] and zones["maxItems"] == 4


@pytest.mark.asyncio
async def test_the_keeper_designs_a_new_facility_before_checking():
    bridge, facilities = AsyncMock(), AsyncMock()
    facilities.known.return_value = {"old_farm"}
    keeper = MidGoalKeeper(
        bridge=bridge, event_publisher=AsyncMock(), store=Mock(), facilities=facilities
    )
    new = GoalSpec(GoalPredicate.FACILITY, name="wheat_farm")
    old = GoalSpec(GoalPredicate.FACILITY, name="old_farm")
    proposal = MidGoalProposal("畑を作る", (new, old), "食べ物")
    await keeper.check_new([PlanChange(PlanOp.ADD, "r", proposal=proposal)])
    facilities.design.assert_awaited_once_with("wheat_farm", "畑を作る: 食べ物")
    bridge.check.assert_awaited_once()

    bridge.facilities.return_value = []
    keeper = MidGoalKeeper(bridge=bridge, event_publisher=AsyncMock(), store=Mock())
    with pytest.raises(GoalRejectedError, match="no facility named wheat_farm"):
        await keeper.check_new([PlanChange(PlanOp.ADD, "r", proposal=proposal)])


def test_the_decision_without_the_facility_keeps_the_rest():
    decision = parse_decision(
        {
            "predicate": "have",
            "item": "food",
            "count": 2,
            "serves": "survival",
            "reason": "満腹度 0",
            "review": "r",
            "plan_changes": [
                {
                    "op": "add",
                    "title": "畑を作る",
                    "conditions": [{"predicate": "facility", "name": "wheat_farm"}],
                    "reason": "食べ物",
                }
            ],
            "steps": [
                {"predicate": "have", "item": "wheat_seeds", "count": 8, "reason": "種"},
                {"predicate": "facility", "name": "wheat_farm", "reason": "作る"},
            ],
        }
    )
    kept = _without_build(decision, "wheat_farm")
    assert kept.changes == () and [s.spec.predicate.value for s in kept.steps] == ["have"]


@pytest.mark.asyncio
async def test_a_mine_is_designed_with_its_depth_and_tunnels():
    mine = {"kind": "mine", "zones": ["B2"], "bottom_y": 16, "tunnels": 2, "reason": "鉄"}
    designer, plan, bridge, text_generator, _ = _designer([{**mine, "bottom_y": None}, mine])
    await designer.design("iron_mine", "鉄を掘る")
    assert "a mine needs bottom_y" in text_generator.generate_json.call_args.args[0]
    assert "mine（採掘場）" in text_generator.generate_json.call_args.args[0]
    body = bridge.set_facility.call_args.args[1]
    assert body["design"] == {"bottom_y": 16, "tunnels": 2, "tunnel_length": 16}
    assert plan.town_map.zone("B2").use == "mine"
