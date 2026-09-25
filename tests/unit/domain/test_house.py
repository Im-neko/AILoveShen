"""プレイの domain のテスト: 家の設計、小目標の指定、中目標、PlaySession。"""

import pytest

from ailoveshen.domain.entities import MidGoalPlan, PlaySession
from ailoveshen.domain.value_objects import (
    ActionResult,
    BlockKind,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    MidGoal,
    MidGoalState,
    Mission,
    Side,
    TownDefinition,
    TownStage,
    is_survival,
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
BUILT = GoalSpec(GoalPredicate.BUILT)
BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
SWORD = GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1)
MISSION = Mission("生き延びながら家を建て、街にしていく")


def _plan(**kwargs) -> MidGoalPlan:
    """家（m1）とベッド（m2）の計画。"""
    plan = MidGoalPlan(mission=MISSION, **kwargs)
    plan.add("自分の家を作る", (BUILT,))
    plan.add("夜に寝られるようにする", (BED,))
    return plan


OK = ActionResult("dig oak_log at 1,2,3", True, "dug", 1.0)
FAILED = ActionResult("dig oak_log at 1,2,3", False, "failed", 1.0)


class TestHouseBlueprintValidation:
    """設計の範囲のテスト。"""

    @pytest.mark.parametrize(
        "field,value", [("width", 4), ("width", 8), ("depth", 4), ("depth", 8)]
    )
    def test_side_out_of_bounds_raises(self, field, value):
        """幅・奥行きが 5〜7 の外なら通さない。"""
        with pytest.raises(ValueError, match=field):
            _blueprint(**{field: value})

    @pytest.mark.parametrize("height", [2, 5])
    def test_wall_height_out_of_bounds_raises(self, height):
        """壁の高さが 3〜4 の外なら通さない。"""
        with pytest.raises(ValueError, match="wall_height"):
            _blueprint(wall_height=height)

    @pytest.mark.parametrize("offset", [0, 4])
    def test_door_on_corner_raises(self, offset):
        """角（またはその先）のドアは通さない。"""
        with pytest.raises(ValueError, match="door offset"):
            _blueprint(door_offset=offset)

    def test_door_offset_uses_that_walls_length(self):
        """東西の面のオフセットは奥行きで確かめる。"""
        _blueprint(width=5, depth=7, door_side=Side.EAST, door_offset=5)
        with pytest.raises(ValueError, match="east"):
            _blueprint(width=7, depth=5, door_side=Side.EAST, door_offset=5)


class TestHouseBlueprintBlocks:
    """置けるブロックへの展開のテスト。"""

    def test_5x5x3_block_count(self):
        """壁（16 x 3 から高さ 2 のドアの穴を除く）+ 屋根（25）+ ドア。"""
        blocks = _blueprint().blocks()

        assert len(blocks) == 16 * 3 - 2 + 25 + 1
        assert _blueprint().material_counts() == {BlockKind.PLANKS: 71, BlockKind.DOOR: 1}

    def test_7x7x4_block_count(self):
        """一番大きい家は、ブリッジで測った 144 ブロックと一致する。"""
        assert len(_blueprint(width=7, depth=7, wall_height=4, door_offset=3).blocks()) == 144

    def test_order_walls_then_roof_then_door(self):
        """壁が 1 段ずつ、次に屋根、最後にドア。"""
        blocks = _blueprint().blocks()
        ys = [b.y for b in blocks[:-1]]

        assert ys == sorted(ys)
        assert blocks[-1] == blocks[-1].__class__(2, 0, 0, BlockKind.DOOR)

    def test_roof_is_laid_from_edges_inward(self):
        """屋根のブロックはどれも、壁の上端か先に置いた屋根に接する。"""
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
        """ドアの下の 2 マスには壁のブロックを置かない。"""
        blocks = {(b.x, b.y, b.z) for b in _blueprint(door_side=Side.SOUTH).blocks()[:-1]}

        assert (2, 0, 4) not in blocks
        assert (2, 1, 4) not in blocks
        assert (2, 2, 4) in blocks

    def test_walls_have_no_openings_but_the_door(self):
        """壁のマスは、ドアの下の 2 マスを除いて全部置く。"""
        bp = _blueprint()
        blocks = {(b.x, b.y, b.z) for b in bp.blocks()[:-1]}
        wall = {
            (x, y, z)
            for y in range(bp.wall_height)
            for x in range(bp.width)
            for z in range(bp.depth)
            if x in (0, bp.width - 1) or z in (0, bp.depth - 1)
        }

        assert wall - blocks == {(2, 0, 0), (2, 1, 0)}

    def test_corner_pillars_use_logs(self):
        """角の柱は、四隅の列を原木にする。"""
        counts = _blueprint(corner_pillars=True).material_counts()

        assert counts[BlockKind.LOG] == 4 * 3
        assert counts[BlockKind.PLANKS] == 71 - 12


class TestGoalSpec:
    """小目標の語彙の形の確認のテスト。"""

    def test_to_dict_and_describe(self):
        assert PLANKS.to_dict() == {"predicate": "have", "item": "planks", "count": 12}
        assert PLANKS.describe() == "have(planks, 12)"
        assert GoalSpec(GoalPredicate.AT_HOME).describe() == "at_home()"
        lit = GoalSpec(GoalPredicate.LIT, distance=16)
        assert lit.to_dict() == {"predicate": "lit", "distance": 16}
        assert lit.describe() == "lit(16)"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"predicate": GoalPredicate.HAVE, "item": "planks"},
            {"predicate": GoalPredicate.HAVE, "count": 3},
            {"predicate": GoalPredicate.HAVE, "item": "planks", "count": 0},
            {"predicate": GoalPredicate.PLACED, "item": "bed"},
            {"predicate": GoalPredicate.EXPLORED},
            {"predicate": GoalPredicate.LIT},
            {"predicate": GoalPredicate.STORED, "item": "food"},
        ],
    )
    def test_missing_arguments_raise(self, kwargs):
        with pytest.raises(ValueError):
            GoalSpec(**kwargs)


class TestPlaySession:
    """小目標のライフサイクルのテスト。"""

    def _session(self, **kwargs) -> PlaySession:
        session = PlaySession(blueprint=_blueprint(), plan=_plan(), **kwargs)
        session.set_goal(Goal(PLANKS, "板材が要る", mid_goal_id="m1"), "day")
        return session

    def test_needs_goal_initially(self):
        session = PlaySession(blueprint=_blueprint(), plan=_plan())
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

    def test_getting_through_the_night_is_not_cut_short(self):
        session = self._session(max_steps_per_goal=2)
        session.set_goal(Goal(GoalSpec(GoalPredicate.THROUGH_NIGHT)), "day")
        session.record(OK)
        session.record(OK)
        assert not session.needs_new_goal(_obs(phase="dusk"))
        assert not session.needs_new_goal(_obs(phase="night"))
        assert session.goal_end_reason(_obs(phase="day", met=True)) == (
            "goal through_night() is met"
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
            PlaySession(blueprint=_blueprint(), plan=_plan(), **{field: 0})

    def test_goal_ends_when_its_mid_goal_has_ended(self):
        session = self._session()
        session.plan.drop("m1", "もっと大事なことがある")
        assert session.goal_end_reason(_obs()) == (
            "the mid goal m1 that goal have(planks, 12) served has ended"
        )

    def test_survival_goal_goes_on_whatever_the_mid_goals(self):
        session = self._session()
        session.set_goal(Goal(GoalSpec(GoalPredicate.THROUGH_NIGHT)), "night")
        session.plan.drop("m1", "理由")
        assert not session.needs_new_goal(_obs(phase="night"))

    def test_steps_are_charged_to_the_mid_goal(self):
        session = self._session()
        session.record(OK)
        session.record(FAILED)
        assert session.plan.get("m1").steps == 2

    def test_activity_is_seen_from_the_mission_down(self):
        session = self._session()
        activity = session.activity()
        assert activity.mission == MISSION
        assert [g.id for g in activity.mid_goals] == ["m1", "m2"]
        assert activity.goal.mid_goal_id == "m1"


class TestMidGoal:
    """中目標の条件のテスト。"""

    def test_conditions_must_be_judged_from_the_world(self):
        with pytest.raises(ValueError, match="explored cannot be a condition"):
            MidGoal("m1", "探検", (GoalSpec(GoalPredicate.EXPLORED, distance=30),))

    def test_needs_conditions_and_a_title(self):
        with pytest.raises(ValueError, match="at least one condition"):
            MidGoal("m1", "家", ())
        with pytest.raises(ValueError, match="title"):
            MidGoal("m1", "", (BUILT,))

    def test_summary_leaves_out_the_sub_steps(self):
        goal = MidGoal("m1", "家", (BUILT,), progress=("placed 3/70", "  have 12 log (0/12)"))
        assert goal.summary() == ("placed 3/70",)

    def test_survival_goals(self):
        assert is_survival(GoalSpec(GoalPredicate.THROUGH_NIGHT))
        assert is_survival(GoalSpec(GoalPredicate.HAVE, item="food", count=4))
        assert not is_survival(PLANKS)
        assert not is_survival(BUILT)


class TestMidGoalPlan:
    """中目標のリストと、コードが守る上限のテスト。"""

    def test_the_first_pending_is_worked_on(self):
        plan = _plan()
        assert plan.current.id == "m1"
        assert [g.title for g in plan.pending] == ["自分の家を作る", "夜に寝られるようにする"]

    def test_streamer_may_add_anywhere(self):
        plan = _plan()
        plan.add("剣", (SWORD,), position=0)
        assert plan.current.title == "剣"

    def test_viewer_goal_never_cuts_in(self):
        plan = _plan()
        goal = plan.add("剣", (SWORD,), requested_by="neko", position=0)
        assert [g.id for g in plan.pending] == ["m1", goal.id, "m2"]

    def test_viewer_goal_is_never_moved_first(self):
        plan = _plan()
        goal = plan.add("剣", (SWORD,), requested_by="neko")
        plan.move(goal.id, 0)
        assert plan.current.id == "m1"
        assert plan.pending[1].id == goal.id

    def test_viewer_goal_in_an_empty_list_is_first(self):
        plan = MidGoalPlan(mission=MISSION)
        plan.add("剣", (SWORD,), requested_by="neko", position=3)
        assert plan.current.requested_by == "neko"

    def test_one_request_per_viewer(self):
        plan = _plan()
        plan.add("剣", (SWORD,), requested_by="neko")
        with pytest.raises(ValueError, match="neko already has a request"):
            plan.add("探検", (PLANKS,), requested_by="neko")

    def test_viewer_requests_are_limited(self):
        plan = _plan(max_viewer_goals=2)
        plan.add("剣", (SWORD,), requested_by="neko")
        plan.add("板", (PLANKS,), requested_by="inu")
        with pytest.raises(ValueError, match="2 viewers' requests"):
            plan.add("ベッド", (BED,), requested_by="tori")

    def test_the_list_is_limited(self):
        plan = _plan(max_goals=3)
        plan.add("剣", (SWORD,))
        with pytest.raises(ValueError, match="full"):
            plan.add("板", (PLANKS,))

    def test_viewer_goal_over_budget_is_dropped(self):
        plan = _plan(viewer_budget=2)
        goal = plan.add("剣", (SWORD,), requested_by="neko")
        assert plan.charge(goal.id) is None
        dropped = plan.charge(goal.id)
        assert dropped.state == MidGoalState.DROPPED
        assert "over the budget" in dropped.ended_because
        assert plan.get(goal.id) is None

    def test_own_goal_has_no_budget(self):
        plan = _plan(viewer_budget=1)
        assert plan.charge("m1") is None
        assert plan.get("m1").steps == 1

    def test_a_town_stage_is_moved_not_dropped(self):
        plan = _plan()
        plan.define_town(TownDefinition("街", (TownStage("家", "w", conditions=(BUILT,)),)))
        stage = plan.add("家", (BUILT,), position=0, stage=0)
        with pytest.raises(ValueError, match="stage of the town"):
            plan.drop(stage.id, "やめた")
        plan.move(stage.id, 2)
        assert plan.stage_goal() == plan.get(stage.id)

    def test_completing_the_stage_moves_the_town_on(self):
        stages = (
            TownStage("家", "w", conditions=(BUILT,)),
            TownStage("倉庫", "w", unresolved=("x",)),
        )
        plan = _plan()
        plan.define_town(TownDefinition("街", stages))
        plan.complete(plan.add("家", (BUILT,), stage=0).id)
        assert (plan.town_stage, plan.current_stage, plan.town_complete) == (1, stages[1], False)
        assert plan.stage_goal() is None

    def test_a_stage_needs_something_to_do(self):
        with pytest.raises(ValueError, match="nothing to do"):
            TownStage("空", "w")
        with pytest.raises(ValueError, match="cannot be a condition"):
            TownStage("夜", "w", conditions=(GoalSpec(GoalPredicate.AT_HOME),))

    def test_dropping_needs_a_reason(self):
        with pytest.raises(ValueError, match="needs a reason"):
            _plan().drop("m1", "")

    def test_finished_are_kept_for_a_while(self):
        plan = _plan(finished_shown=1)
        plan.complete("m1")
        plan.drop("m2", "やめた")
        assert [(g.id, g.state) for g in plan.finished] == [("m2", MidGoalState.DROPPED)]
        assert plan.pending == ()

    def test_unknown_id_is_named(self):
        with pytest.raises(ValueError, match="no pending mid goal m9"):
            _plan().move("m9", 0)

    def test_restore_keeps_ids(self):
        plan = MidGoalPlan(mission=MISSION)
        plan.restore([MidGoal("m4", "剣", (SWORD,))], [], next_id=5)
        assert plan.add("板", (PLANKS,)).id == "m5"
