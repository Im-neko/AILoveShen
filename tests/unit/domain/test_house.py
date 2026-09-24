"""Tests for the play domain: house blueprint, goal specs and the PlaySession lifecycle."""

import pytest

from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.value_objects import (
    ActionResult,
    BlockKind,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
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


def _obs(remaining=5, met=False, phase="day", goal=True) -> GameObservation:
    return GameObservation(
        state={},
        candidates=(),
        health=20.0,
        food=20,
        goal=GoalStatus(met=met, remaining=remaining) if goal else None,
        time_phase=phase,
    )


PLANKS = GoalSpec(GoalPredicate.HAVE, item="planks", count=12)
OK = ActionResult("dig oak_log at 1,2,3", True, "dug", 1.0)
FAILED = ActionResult("dig oak_log at 1,2,3", False, "failed", 1.0)


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


class TestGoalSpec:
    """Tests for the goal vocabulary's shape checks."""

    def test_to_dict_and_describe(self):
        assert PLANKS.to_dict() == {"predicate": "have", "item": "planks", "count": 12}
        assert PLANKS.describe() == "have(planks, 12)"
        assert GoalSpec(GoalPredicate.AT_HOME).describe() == "at_home()"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"predicate": GoalPredicate.HAVE, "item": "planks"},
            {"predicate": GoalPredicate.HAVE, "count": 3},
            {"predicate": GoalPredicate.HAVE, "item": "planks", "count": 0},
            {"predicate": GoalPredicate.PLACED, "item": "bed"},
            {"predicate": GoalPredicate.EXPLORED},
        ],
    )
    def test_missing_arguments_raise(self, kwargs):
        with pytest.raises(ValueError):
            GoalSpec(**kwargs)


class TestPlaySession:
    """Tests for the goal lifecycle."""

    def _session(self, **kwargs) -> PlaySession:
        session = PlaySession(blueprint=_blueprint(), **kwargs)
        session.set_goal(Goal(PLANKS, "板材が要る"), "day")
        return session

    def test_needs_goal_initially(self):
        session = PlaySession(blueprint=_blueprint())
        assert session.needs_new_goal(_obs(goal=False))
        assert session.goal_end_reason(_obs(goal=False)) == "no goal yet"

    def test_goal_goes_on_while_not_met(self):
        assert not self._session().needs_new_goal(_obs())

    def test_met_goal_ends(self):
        assert self._session().goal_end_reason(_obs(met=True, remaining=0)) == (
            "goal have(planks, 12) is met"
        )

    def test_consecutive_failures_end_the_goal(self):
        session = self._session(max_consecutive_failures=2)
        session.record(FAILED)
        assert not session.needs_new_goal(_obs())
        session.record(FAILED)
        assert "stuck" in session.goal_end_reason(_obs())

    def test_success_resets_failures(self):
        session = self._session(max_consecutive_failures=2)
        session.record(FAILED)
        session.record(OK)
        session.record(FAILED)
        assert not session.needs_new_goal(_obs())

    def test_stall_counts_steps_without_less_remaining_work(self):
        session = self._session(max_stalled_steps=2)
        session.track_progress(_obs(remaining=5))
        session.record(OK)
        session.track_progress(_obs(remaining=5))
        session.record(OK)
        assert not session.needs_new_goal(_obs(remaining=5))
        session.track_progress(_obs(remaining=6))
        assert "stalled" in session.goal_end_reason(_obs(remaining=6))

    def test_progress_resets_the_stall(self):
        session = self._session(max_stalled_steps=2)
        session.track_progress(_obs(remaining=5))
        session.record(OK)
        session.track_progress(_obs(remaining=5))
        session.record(OK)
        session.track_progress(_obs(remaining=4))
        assert session.stalled_steps == 0

    def test_step_budget_ends_the_goal(self):
        session = self._session(max_steps_per_goal=2)
        session.record(OK)
        session.record(OK)
        assert "ran for 2 steps" in session.goal_end_reason(_obs())

    def test_time_of_day_change_ends_the_goal(self):
        session = self._session()
        assert session.goal_end_reason(_obs(phase="dusk")) == (
            "the time of day changed from day to dusk"
        )

    def test_set_goal_resets_counters(self):
        session = self._session(max_stalled_steps=2)
        session.track_progress(_obs(remaining=5))
        session.record(FAILED)
        session.track_progress(_obs(remaining=5))
        session.set_goal(Goal(GoalSpec(GoalPredicate.AT_HOME)), "dusk")
        assert (session.steps_in_goal, session.consecutive_failures, session.stalled_steps) == (
            0,
            0,
            0,
        )
        assert session.least_remaining is None
        assert session.goal_phase == "dusk"

    @pytest.mark.parametrize(
        "field", ["max_steps_per_goal", "max_consecutive_failures", "max_stalled_steps"]
    )
    def test_limits_must_be_positive(self, field):
        with pytest.raises(ValueError, match=field):
            PlaySession(blueprint=_blueprint(), **{field: 0})
