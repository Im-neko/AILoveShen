"""Gemini が設計する建物のテスト（docs/design/25_builds.md）。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.builds import BUILD_SCHEMA, BuildDesigner, parse_build
from ailoveshen.application.use_cases.goal_vocabulary import MidGoalProposal, parse_spec
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.exceptions import GoalRejectedError
from ailoveshen.domain.value_objects import (
    BlockKind,
    BuildAnchor,
    BuildDesign,
    BuildShape,
    CharacterProfile,
    ConditionStatus,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    Mission,
    ShapeKind,
)
from ailoveshen.presentation.web.goal_board import goal_label

ANNEX = {
    "purpose": "寝室を東に足す",
    "anchor": "home:east",
    "shapes": [
        {
            "shape": "hollow_box",
            "block": "planks",
            "from": {"x": 0, "y": 0, "z": 0},
            "to": {"x": 4, "y": 4, "z": 4},
        },
        {"shape": "clear", "from": {"x": 1, "y": 1, "z": 1}, "to": {"x": 3, "y": 3, "z": 3}},
        {"shape": "clear", "from": {"x": 0, "y": 1, "z": 2}, "to": {"x": 0, "y": 2, "z": 2}},
    ],
}


def test_shapes_expand_in_a_buildable_order():
    design = parse_build(ANNEX, "annex")
    blocks = design.blocks()
    kinds = [b.kind for b in blocks]
    # 空けるマスが先、ブロックは下の層から、ドアは最後
    first_solid = next(i for i, k in enumerate(kinds) if k != BlockKind.AIR)
    assert all(k == BlockKind.AIR for k in kinds[:first_solid])
    solids = blocks[first_solid:]
    assert [b.y for b in solids] == sorted(b.y for b in solids)
    assert design.size() == (5, 5, 5)
    assert design.anchor == BuildAnchor.HOME_EAST
    assert design.material_counts() == {BlockKind.PLANKS: 98 - 2}
    assert BUILD_SCHEMA["properties"]["anchor"]["enum"][-1] == "near_home"


def test_a_door_takes_two_cells_and_comes_last():
    design = BuildDesign(
        "shed",
        "",
        BuildAnchor.NEAR_HOME,
        (
            BuildShape(ShapeKind.FILL, (0, 0, 0), (2, 3, 0), BlockKind.COBBLESTONE),
            BuildShape(ShapeKind.DOOR, (1, 1, 0), (1, 1, 0)),
        ),
    )
    blocks = design.blocks()
    assert blocks[-1].kind == BlockKind.DOOR and (blocks[-1].x, blocks[-1].y) == (1, 1)
    assert not any((b.x, b.y, b.z) == (1, 2, 0) for b in blocks)  # ドアの上半分


@pytest.mark.parametrize(
    "shapes, reason",
    [
        ([BuildShape(ShapeKind.FILL, (0, 0, 0), (31, 15, 31), BlockKind.DIRT)], "over 600"),
        ([BuildShape(ShapeKind.FILL, (0, 0, 0), (32, 0, 0), BlockKind.DIRT)], "must fit"),
        ([BuildShape(ShapeKind.CLEAR, (0, 0, 0), (1, 1, 1))], "at least one block"),
    ],
)
def test_limits_give_reasons(shapes, reason):
    with pytest.raises(ValueError, match=reason):
        BuildDesign("big", "", BuildAnchor.NEAR_HOME, tuple(shapes))


def test_bad_input_is_a_value_error_with_a_reason():
    with pytest.raises(ValueError, match="needs a solid block"):
        parse_build({**ANNEX, "shapes": [{"shape": "fill", "from": {"x": 0, "y": 0, "z": 0}}]}, "a")
    with pytest.raises(ValueError, match="name"):
        parse_build(ANNEX, "Bad Name")
    with pytest.raises(ValueError):
        parse_build({"purpose": "x"}, "a")


def test_built_can_name_a_build_and_the_board_shows_it():
    spec = parse_spec({"predicate": "built", "name": "Annex"})
    assert spec.name == "annex"
    assert spec.to_dict() == {"predicate": "built", "name": "annex"}
    assert spec.describe() == "built(annex)"
    assert parse_spec({"predicate": "built"}).name is None
    assert goal_label(spec) == "「annex」を建てる"


def _designer(outputs, check=None, reject=()):
    generator = AsyncMock()
    generator.generate_json.side_effect = outputs
    prompts = Mock()
    prompts.build_build_design_prompt.return_value = "PROMPT"
    bridge = AsyncMock()
    bridge.observe.return_value = GameObservation(
        state={
            "home": {
                "design": {
                    "name": "小屋",
                    "width": 5,
                    "depth": 5,
                    "wall_height": 3,
                    "door_side": "north",
                }
            }
        },
        candidates=(),
        health=20,
        food=20,
    )
    bridge.check.side_effect = check or (lambda specs: [ConditionStatus(s, False) for s in specs])
    bridge.set_build.side_effect = list(reject) + [None] * 5
    bridge.builds.return_value = []
    return BuildDesigner(generator, prompts, bridge, CharacterProfile()), generator, prompts, bridge


@pytest.mark.asyncio
async def test_the_designer_retries_with_the_reason_until_the_bridge_accepts():
    bad = {
        **ANNEX,
        "shapes": [
            {
                "shape": "fill",
                "block": "planks",
                "from": {"x": 0, "y": 0, "z": 0},
                "to": {"x": 40, "y": 0, "z": 0},
            }
        ],
    }
    designer, generator, prompts, bridge = _designer(
        [bad, ANNEX, ANNEX],
        reject=[GoalRejectedError("the block (2,1,4) would replace the oak_door")],
    )
    design = await designer.design("annex", "家を広くする: 視聴者の頼み")
    assert design.name == "annex"
    assert generator.generate_json.await_count == 3
    errors = [c.kwargs["previous_error"] for c in prompts.build_build_design_prompt.call_args_list]
    assert errors[0] == "" and "must fit" in errors[1] and "oak_door" in errors[2]
    assert "幅 5" in prompts.build_build_design_prompt.call_args.kwargs["home_note"]
    assert generator.generate_json.call_args.kwargs["purpose"] == "build_design"
    bridge.set_build.assert_awaited_with(design)


@pytest.mark.asyncio
async def test_materials_that_cannot_be_obtained_are_sent_back():
    def check(specs):
        return [ConditionStatus(s, False, impossible=("no way to get cobblestone",)) for s in specs]

    designer, *_ = _designer([ANNEX, ANNEX, ANNEX], check=check)
    with pytest.raises(GoalRejectedError, match="cannot be obtained"):
        await designer.design("annex", "x")


@pytest.mark.asyncio
async def test_the_keeper_designs_a_new_build_before_adding_and_widens_a_viewers_budget():
    bridge = AsyncMock()
    bridge.builds.return_value = [{"name": "annex", "total": 120}]
    builder = AsyncMock()
    builder.known.return_value = set()
    keeper = MidGoalKeeper(
        bridge=bridge, event_publisher=AsyncMock(), store=Mock(), builder=builder
    )
    plan = MidGoalPlan(mission=Mission("街"))
    proposal = MidGoalProposal(
        title="家を広くする",
        conditions=(GoalSpec(GoalPredicate.BUILT, name="annex"),),
        reason="ベッドを置く場所がほしい",
    )
    goal = await keeper.accept(plan, proposal, requested_by="まめ")
    builder.design.assert_awaited_once_with("annex", "家を広くする: ベッドを置く場所がほしい")
    bridge.check.assert_awaited_once()
    assert goal.budget == 280


@pytest.mark.asyncio
async def test_a_known_build_is_not_designed_again():
    bridge = AsyncMock()
    bridge.builds.return_value = [{"name": "annex", "total": 10}]
    builder = AsyncMock()
    builder.known.return_value = {"annex"}
    keeper = MidGoalKeeper(
        bridge=bridge, event_publisher=AsyncMock(), store=Mock(), builder=builder
    )
    proposal = MidGoalProposal(
        title="増築", conditions=(GoalSpec(GoalPredicate.BUILT, name="annex"),), reason=""
    )
    await keeper.accept(MidGoalPlan(mission=Mission("街")), proposal, requested_by="a")
    builder.design.assert_not_awaited()
