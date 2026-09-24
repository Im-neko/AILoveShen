"""Tests for the house building domain: blueprint, goals and HouseProject."""

import pytest

from ailoveshen.domain.entities import HouseProject
from ailoveshen.domain.value_objects import (
    ActionResult,
    AvailableAction,
    BlockKind,
    BuildStatus,
    GameObservation,
    Goal,
    GoalType,
    HouseBlueprint,
    Side,
    WallOpening,
)


def _blueprint(**kwargs) -> HouseBlueprint:
    params = {
        "name": "小屋",
        "concept": "最初の家",
        "width": 5,
        "depth": 5,
        "wall_height": 3,
        "door_side": Side.NORTH,
        "door_offset": 2,
    }
    params.update(kwargs)
    return HouseBlueprint(**params)


def _obs(
    inventory=None, actions=(), build=None, table=False, phase="day", food_items=0
) -> GameObservation:
    """An observation; a sword is held unless the inventory says otherwise."""
    return GameObservation(
        state={},
        inventory={"wooden_sword": 1} if inventory is None else inventory,
        actions=tuple(AvailableAction(a, a) for a in actions),
        health=20.0,
        food=20,
        crafting_table_nearby=table,
        build=build,
        time_phase=phase,
        food_items=food_items,
    )


def _build(remaining: dict[BlockKind, int], complete=False) -> BuildStatus:
    total = 72
    return BuildStatus(
        total=total,
        placed=total - sum(remaining.values()),
        complete=complete,
        site_chosen=True,
        remaining=remaining,
    )


class TestHouseBlueprintValidation:
    """Tests for blueprint bounds."""

    @pytest.mark.parametrize(
        "field,value", [("width", 4), ("width", 8), ("depth", 4), ("depth", 8)]
    )
    def test_side_out_of_bounds_raises(self, field, value):
        """Test width/depth outside 5-7 are rejected."""
        with pytest.raises(ValueError, match=field):
            _blueprint(**{field: value})

    @pytest.mark.parametrize("height", [2, 5])
    def test_wall_height_out_of_bounds_raises(self, height):
        """Test wall heights outside 3-4 are rejected."""
        with pytest.raises(ValueError, match="wall_height"):
            _blueprint(wall_height=height)

    @pytest.mark.parametrize("offset", [0, 4])
    def test_door_on_corner_raises(self, offset):
        """Test a door on a corner (or past it) is rejected."""
        with pytest.raises(ValueError, match="door offset"):
            _blueprint(door_offset=offset)

    def test_window_on_door_raises(self):
        """Test a window at the door position is rejected."""
        with pytest.raises(ValueError, match="overlaps"):
            _blueprint(windows=(WallOpening(Side.NORTH, 2),))

    def test_duplicate_windows_raise(self):
        """Test two windows at one position are rejected."""
        with pytest.raises(ValueError, match="overlaps"):
            _blueprint(windows=(WallOpening(Side.EAST, 1), WallOpening(Side.EAST, 1)))

    def test_window_offset_uses_that_walls_length(self):
        """Test east/west offsets are checked against the depth."""
        _blueprint(width=5, depth=7, windows=(WallOpening(Side.EAST, 5),))
        with pytest.raises(ValueError, match="east"):
            _blueprint(width=7, depth=5, windows=(WallOpening(Side.EAST, 5),))


class TestHouseBlueprintBlocks:
    """Tests for expansion into placeable blocks."""

    def test_5x5x3_block_count(self):
        """Test walls (16 x 3 minus a 2-high door gap) + roof (25) + door."""
        blocks = _blueprint().blocks()

        assert len(blocks) == 16 * 3 - 2 + 25 + 1
        assert _blueprint().material_counts() == {BlockKind.PLANKS: 71, BlockKind.DOOR: 1}

    def test_7x7x4_block_count(self):
        """Test the largest house matches the bridge-measured 144 blocks."""
        assert len(_blueprint(width=7, depth=7, wall_height=4, door_offset=3).blocks()) == 144

    def test_order_walls_then_roof_then_door(self):
        """Test walls come layer by layer, then the roof, then the door last."""
        blocks = _blueprint().blocks()
        ys = [b.y for b in blocks[:-1]]

        assert ys == sorted(ys)
        assert blocks[-1] == blocks[-1].__class__(2, 0, 0, BlockKind.DOOR)

    def test_roof_is_laid_from_edges_inward(self):
        """Test each roof block touches a wall top or an earlier roof block."""
        bp = _blueprint(width=7, depth=6)
        placed = set()
        for b in bp.blocks():
            if b.y != bp.wall_height:
                continue
            on_wall = b.x in (0, bp.width - 1) or b.z in (0, bp.depth - 1)
            touches = any(
                (b.x + dx, b.z + dz) in placed for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
            )
            assert on_wall or touches, b
            placed.add((b.x, b.z))
        assert len(placed) == bp.width * bp.depth

    def test_door_gap_is_two_high(self):
        """Test no wall block is planned in the door's two lower cells."""
        blocks = {(b.x, b.y, b.z) for b in _blueprint(door_side=Side.SOUTH).blocks()[:-1]}

        assert (2, 0, 4) not in blocks
        assert (2, 1, 4) not in blocks
        assert (2, 2, 4) in blocks

    def test_window_is_one_block_at_eye_height(self):
        """Test a window removes only the block at y=1."""
        blocks = {
            (b.x, b.y, b.z) for b in _blueprint(windows=(WallOpening(Side.WEST, 2),)).blocks()
        }

        assert (0, 1, 2) not in blocks
        assert (0, 0, 2) in blocks and (0, 2, 2) in blocks

    def test_corner_pillars_use_logs(self):
        """Test corner pillars turn the four corner columns into logs."""
        counts = _blueprint(corner_pillars=True).material_counts()

        assert counts[BlockKind.LOG] == 4 * 3
        assert counts[BlockKind.PLANKS] == 71 - 12


class TestGoal:
    """Tests for Goal.allows."""

    def test_goal_actions_allowed(self):
        """Test the goal's own actions are allowed and others are not."""
        goal = Goal(GoalType.GATHER_WOOD)

        assert goal.allows("collect_log")
        assert not goal.allows("build_step")

    def test_survival_actions_always_allowed(self):
        """Test survival actions are allowed in every goal."""
        for goal_type in GoalType:
            assert Goal(goal_type).allows("flee_hostile")

    def test_only_the_explore_goal_explores(self):
        """Test explore is not a fallback of other goals (it would keep them always available)."""
        assert Goal(GoalType.EXPLORE).allows("explore")
        for goal_type in (GoalType.GATHER_WOOD, GoalType.CRAFT, GoalType.BUILD_SHELTER):
            assert not Goal(goal_type).allows("explore")


class TestHouseProjectMaterialNeeds:
    """Tests for the material shortfall computation."""

    def test_nothing_in_hand(self):
        """Test a fresh 5x5 house needs 71 + 6 (door) + 4 (table) planks = 21 logs."""
        needs = HouseProject(blueprint=_blueprint()).material_needs(_obs())

        assert needs.planks_short == 81
        assert needs.logs_short == 21
        assert needs.door_needed and needs.table_needed

    def test_logs_in_hand_reduce_shortfall(self):
        """Test held logs count toward the log shortfall only."""
        needs = HouseProject(blueprint=_blueprint()).material_needs(
            _obs({"wooden_sword": 1, "spruce_log": 20})
        )

        assert needs.logs_short == 1
        assert needs.planks_short == 81
        assert not needs.wood_ready

    def test_table_nearby_or_held_is_not_needed(self):
        """Test a placed or held crafting table removes its 4 planks."""
        project = HouseProject(blueprint=_blueprint())

        assert not project.material_needs(_obs(table=True)).table_needed
        assert not project.material_needs(
            _obs({"wooden_sword": 1, "crafting_table": 1})
        ).table_needed
        assert project.material_needs(_obs(table=True)).planks_short == 77

    def test_door_in_hand_needs_no_door_planks(self):
        """Test a held door removes the door and table planks."""
        needs = HouseProject(blueprint=_blueprint()).material_needs(
            _obs({"wooden_sword": 1, "spruce_door": 1})
        )

        assert not needs.door_needed and not needs.table_needed
        assert needs.planks_short == 71

    def test_remaining_blocks_come_from_build_status(self):
        """Test placed blocks no longer count once building has started."""
        obs = _obs(
            {"wooden_sword": 1, "oak_planks": 10},
            build=_build({BlockKind.PLANKS: 10, BlockKind.DOOR: 1}),
        )
        needs = HouseProject(blueprint=_blueprint()).material_needs(obs)

        assert needs.planks_short == 10  # 10 blocks + 6 door + 4 table - 10 held
        assert needs.logs_short == 3

    def test_corner_logs_count_as_logs(self):
        """Test log blocks are needed as logs, not planks."""
        needs = HouseProject(blueprint=_blueprint(corner_pillars=True)).material_needs(
            _obs({"wooden_sword": 1, "spruce_door": 1, "spruce_planks": 59})
        )

        assert needs.planks_short == 0
        assert needs.logs_short == 12
        assert needs.crafted


class TestSwordNeeds:
    """Tests for the sword in the material needs."""

    def test_no_sword_needs_sword_planks_and_a_table(self):
        """Test a missing sword adds 4 planks and needs a crafting table."""
        project = HouseProject(blueprint=_blueprint())
        done = _obs(build=_build({}, complete=True), inventory={})

        needs = project.material_needs(done)

        assert needs.sword_needed and needs.table_needed
        assert needs.planks_short == 8  # sword 4 + table 4
        assert not needs.crafted

    def test_held_sword_is_not_needed(self):
        """Test a held sword of any material removes the need."""
        project = HouseProject(blueprint=_blueprint())
        needs = project.material_needs(
            _obs({"stone_sword": 1}, build=_build({}, complete=True), table=True)
        )

        assert not needs.sword_needed
        assert needs.crafted


class TestHouseProjectGoals:
    """Tests for goal completion and re-decision rules."""

    def test_needs_goal_initially(self):
        """Test a new project asks for a goal."""
        project = HouseProject(blueprint=_blueprint())

        assert project.needs_new_goal(_obs())
        assert project.goal_end_reason(_obs()) == "no goal yet"

    def test_gather_wood_met_when_enough_logs(self):
        """Test gathering ends once held logs cover the shortfall."""
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.GATHER_WOOD), "day")

        assert not project.goal_met(_obs({"wooden_sword": 1, "spruce_log": 20}))
        assert project.goal_met(_obs({"wooden_sword": 1, "spruce_log": 21}))

    def test_craft_met_when_planks_and_door_ready(self):
        """Test crafting ends when planks and the door are ready."""
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.CRAFT), "day")

        assert not project.goal_met(_obs({"wooden_sword": 1, "spruce_planks": 71}))
        assert project.goal_met(_obs({"wooden_sword": 1, "spruce_planks": 71, "spruce_door": 1}))

    def test_build_met_when_complete(self):
        """Test building ends only when the bridge reports completion."""
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.BUILD_SHELTER), "day")

        assert not project.goal_met(_obs(build=_build({BlockKind.PLANKS: 1})))
        assert project.goal_met(_obs(build=_build({}, complete=True)))
        assert project.is_complete(_obs(build=_build({}, complete=True)))

    def test_explore_met_after_steps(self):
        """Test exploring ends after explore_steps steps."""
        project = HouseProject(blueprint=_blueprint(), explore_steps=2)
        project.set_goal(Goal(GoalType.EXPLORE), "day")
        project.record(ActionResult("explore", True, "", 1.0))
        assert not project.goal_met(_obs())
        project.record(ActionResult("explore", True, "", 1.0))

        assert project.goal_met(_obs())

    def test_consecutive_failures_force_new_goal(self):
        """Test repeated failures ask for a new goal and a success resets the count."""
        project = HouseProject(blueprint=_blueprint(), max_consecutive_failures=2)
        project.set_goal(Goal(GoalType.GATHER_WOOD), "day")
        project.record(ActionResult("collect_log", False, "failed", 1.0))
        project.record(ActionResult("collect_log", True, "ok", 1.0))
        project.record(ActionResult("collect_log", False, "failed", 1.0))
        assert not project.needs_new_goal(_obs())

        project.record(ActionResult("collect_log", False, "failed", 1.0))
        assert project.needs_new_goal(_obs())
        assert "stuck" in project.goal_end_reason(_obs())

    def test_step_budget_forces_new_goal(self):
        """Test a goal pursued for max_steps_per_goal steps is re-decided."""
        project = HouseProject(blueprint=_blueprint(), max_steps_per_goal=1)
        project.set_goal(Goal(GoalType.GATHER_WOOD), "day")
        project.record(ActionResult("collect_log", True, "ok", 1.0))

        assert project.needs_new_goal(_obs())

    def test_block_goal_forces_new_goal(self):
        """Test block_goal marks the goal as stuck."""
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.CRAFT), "day")
        project.block_goal()

        assert project.needs_new_goal(_obs())

    def test_set_goal_resets_counters(self):
        """Test a new goal starts with fresh counters."""
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.CRAFT), "day")
        project.block_goal()
        project.set_goal(Goal(GoalType.GATHER_WOOD), "day")

        assert project.steps_in_goal == 0
        assert project.consecutive_failures == 0

    def test_survive_night_met_in_the_morning(self):
        """Test the night goal ends when the day begins."""
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.SURVIVE_NIGHT), "dusk")

        assert not project.goal_met(_obs(phase="night"))
        assert project.goal_met(_obs(phase="day"))

    def test_survive_night_goes_on_while_a_mob_waits_outside(self):
        """Test morning with a mob at the door (stay_inside still offered) keeps the goal."""
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.SURVIVE_NIGHT), "dawn")

        assert not project.goal_met(_obs(actions=("stay_inside",), phase="day"))

    def test_get_food_met_with_food_stock(self):
        """Test the food goal ends once enough food is carried."""
        project = HouseProject(blueprint=_blueprint(), food_stock=3)
        project.set_goal(Goal(GoalType.GET_FOOD), "day")

        assert not project.goal_met(_obs(food_items=2))
        assert project.goal_met(_obs(food_items=3))

    def test_time_of_day_change_forces_new_goal(self):
        """Test dusk falling during a goal asks for a new decision."""
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.GATHER_WOOD), "day")

        assert not project.needs_new_goal(_obs(phase="day"))
        assert project.needs_new_goal(_obs(phase="dusk"))
        assert project.goal_end_reason(_obs(phase="dusk")) == (
            "the time of day changed from day to dusk"
        )

    def test_fulfilled_goals_are_not_offered(self):
        """Test a finished house offers neither gathering, crafting nor building."""
        project = HouseProject(blueprint=_blueprint())
        done = _obs(
            actions=("collect_log", "craft_planks", "explore", "hunt_animal"),
            build=_build({}, complete=True),
            table=True,
        )

        assert project.pursuable_goals(done) == [GoalType.EXPLORE, GoalType.GET_FOOD]

    def test_limits_must_be_positive(self):
        """Test non-positive limits are rejected."""
        with pytest.raises(ValueError, match="max_steps_per_goal"):
            HouseProject(blueprint=_blueprint(), max_steps_per_goal=0)
