"""大目標の種類と、その手順（中目標）のテスト（docs/design/50）。"""

import pytest

from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import (
    MAX_OPEN_STEPS,
    GoalPredicate,
    GoalSpec,
    JobState,
    MidGoal,
    Mission,
    ObjectiveKind,
    PlannedStep,
    objective_kind,
)

BUILT = GoalSpec(GoalPredicate.BUILT, name="storehouse")
FACILITY = GoalSpec(GoalPredicate.FACILITY, name="wheat_farm")
LOG = GoalSpec(GoalPredicate.HAVE, item="log", count=12)
STONE = GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=32)
PLANKS = GoalSpec(GoalPredicate.HAVE, item="planks", count=4)
TORCH = GoalSpec(GoalPredicate.PLACED, item="torch", where="home")


def _plan(**kwargs) -> MidGoalPlan:
    plan = MidGoalPlan(mission=Mission("街にしていく"), **kwargs)
    plan.add("保管庫を建てる", (BUILT,))
    return plan


def _ids(plan: MidGoalPlan, goal_id: str = "m1") -> list[tuple[str, str]]:
    return [(s.id, s.spec.describe()) for s in plan.get(goal_id).plan_steps]


class TestKind:
    def test_the_kind_comes_from_the_conditions(self):
        assert objective_kind((BUILT,)) == ObjectiveKind.BUILD
        assert objective_kind((FACILITY,)) == ObjectiveKind.FACILITY
        assert objective_kind((LOG, TORCH)) == ObjectiveKind.INFRASTRUCTURE
        assert MidGoal("m1", "保管庫", (BUILT,)).kind == ObjectiveKind.BUILD

    @pytest.mark.parametrize("conditions", [(BUILT, LOG), (BUILT, FACILITY), (FACILITY, TORCH)])
    def test_a_build_or_facility_holds_only_that_kind_of_condition(self, conditions):
        with pytest.raises(ValueError, match="steps"):
            objective_kind(conditions)
        with pytest.raises(ValueError, match="steps"):
            MidGoal("m1", "保管庫", conditions)

    def test_the_plan_sends_a_mixed_mid_goal_back(self):
        with pytest.raises(ValueError, match="materials"):
            MidGoalPlan(mission=Mission("m")).add("保管庫", (BUILT, LOG))

    def test_town_stages_and_their_preparation_are_not_checked(self):
        """段階の条件は段階の定義が決める。準備は調査と引っ越しの組み合わせ。"""
        plan = MidGoalPlan(mission=Mission("m"))
        stage = plan.add("段階", (BUILT, TORCH), stage=0)
        prepare = plan.add("引っ越す", (BUILT, LOG), prepares_town=True)
        assert stage.kind == prepare.kind == ObjectiveKind.INFRASTRUCTURE


class TestSetSteps:
    def test_new_steps_get_ids_in_order(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石")))
        assert _ids(plan) == [("j1", "have(log, 12)"), ("j2", "have(cobblestone, 32)")]
        assert plan.next_step_id == 3

    def test_a_rewrite_keeps_the_identity_of_the_same_step(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石")))
        plan.judge_steps("m1", [("j1", False, ("log (3/12)",))])
        plan.set_steps("m1", (PlannedStep(STONE, "石が先"), PlannedStep(LOG, "原木")))
        assert _ids(plan) == [("j2", "have(cobblestone, 32)"), ("j1", "have(log, 12)")]
        assert plan.get("m1").plan_steps[1].progress == ("log (3/12)",)

    def test_a_done_step_stays_done_and_is_not_added_again(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石")))
        plan.judge_steps("m1", [("j1", True, ())])
        plan.set_steps("m1", (PlannedStep(LOG, "また"), PlannedStep(STONE, "石"), PlannedStep(PLANKS)))
        goal = plan.get("m1")
        assert [(s.id, s.state) for s in goal.plan_steps] == [
            ("j1", JobState.DONE),
            ("j2", JobState.PENDING),
            ("j3", JobState.PENDING),
        ]
        assert [s.spec for s in goal.open_steps] == [STONE, PLANKS]
        assert [s.spec for s in goal.done_steps] == [LOG]

    def test_viewer_and_advice_steps_survive_a_rewrite_that_omits_them(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"),))
        plan.add_step("m1", PlannedStep(TORCH, "明るく", requested_by="neko"))
        plan.add_step("m1", PlannedStep(PLANKS, "板", suggested_by="inu"))
        plan.set_steps("m1", (PlannedStep(STONE, "石"),))
        specs = [(s.spec, s.requested_by, s.suggested_by) for s in plan.get("m1").open_steps]
        assert specs == [(STONE, None, None), (TORCH, "neko", None), (PLANKS, None, "inu")]

    def test_at_most_eight_steps_are_left_to_do(self):
        plan = _plan()
        many = tuple(
            PlannedStep(GoalSpec(GoalPredicate.HAVE, item=f"item{i}", count=1))
            for i in range(MAX_OPEN_STEPS + 1)
        )
        with pytest.raises(ValueError, match="at most 8"):
            plan.set_steps("m1", many)
        assert plan.get("m1").plan_steps == ()  # 反映していない


class TestLatch:
    def test_a_met_step_stays_done_when_the_condition_breaks_later(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石")))
        assert [s.id for s in plan.judge_steps("m1", [("j1", True, ())])] == ["j1"]
        # 材料を使って条件が崩れても、判定するのは済んでいない手順だけ: 済みのまま
        plan.judge_steps("m1", [("j1", False, ()), ("j2", False, ("cobblestone (1/32)",))])
        goal = plan.get("m1")
        assert [s.state for s in goal.plan_steps] == [JobState.DONE, JobState.PENDING]
        assert goal.plan_steps[1].progress == ("cobblestone (1/32)",)

    def test_a_rewrite_does_not_move_the_count_of_done_steps_back(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石")))
        plan.judge_steps("m1", [("j1", True, ())])

        def counts():
            goal = plan.get("m1")
            return len(goal.done_steps), len(goal.plan_steps)

        assert counts() == (1, 2)
        # 材料を使って条件が崩れたあとで、Gemini が残りを同じ内容で書き直しても
        plan.set_steps("m1", (PlannedStep(STONE, "石"),))
        assert counts() == (1, 2)
        plan.set_steps("m1", (PlannedStep(LOG, "また"), PlannedStep(STONE, "石")))
        assert counts() == (1, 2)

    def test_dropping_a_step_needs_a_reason(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"),))
        with pytest.raises(ValueError, match="reason"):
            plan.drop_step("m1", "j1", "")
        dropped = plan.drop_step("m1", "j1", "not needed")
        assert (dropped.state, dropped.ended_because) == (JobState.DROPPED, "not needed")
        assert plan.get("m1").plan_steps == ()


class TestViewerJobs:
    def test_a_viewer_step_goes_behind_the_step_in_progress(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石")))
        plan.add_step("m1", PlannedStep(TORCH, "明るく", requested_by="neko"), position=0)
        assert [s.spec for s in plan.get("m1").open_steps] == [LOG, TORCH, STONE]  # 先頭には入らない

    def test_without_a_position_it_goes_last(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石")))
        plan.add_step("m1", PlannedStep(TORCH, "明るく", requested_by="neko"))
        assert [s.spec for s in plan.get("m1").open_steps] == [LOG, STONE, TORCH]

    def test_now_puts_it_first(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"),))
        plan.add_step("m1", PlannedStep(TORCH, "今", requested_by="neko"), position=0, now=True)
        assert plan.get("m1").open_steps[0].spec == TORCH

    def test_the_same_step_is_not_added_twice(self):
        plan = _plan()
        plan.set_steps("m1", (PlannedStep(LOG, "原木"),))
        same = plan.add_step("m1", PlannedStep(LOG, "また", requested_by="neko"))
        assert same.id == "j1" and len(plan.get("m1").plan_steps) == 1

    def test_the_limits_count_objectives_and_steps_together(self):
        plan = _plan(max_viewer_goals=2)
        plan.add("松明", (TORCH,), requested_by="neko")
        with pytest.raises(ValueError, match="already has a request"):
            plan.add_step("m1", PlannedStep(LOG, "原木", requested_by="neko"))
        plan.add_step("m1", PlannedStep(LOG, "原木", requested_by="inu"))
        with pytest.raises(ValueError, match="2 viewers"):
            plan.add_step("m1", PlannedStep(STONE, "石", requested_by="tori"))
        with pytest.raises(ValueError, match="already has a request"):
            plan.check_room("inu")
        assert sorted(plan.viewers()) == ["inu", "neko"]

    def test_a_step_does_not_need_room_in_the_list(self):
        plan = _plan(max_goals=1)
        plan.add_step("m1", PlannedStep(LOG, "原木", requested_by="neko"))
        with pytest.raises(ValueError, match="full"):
            plan.check_room("inu")

    def test_the_budget_is_counted_for_the_viewers_step_and_drops_it(self):
        plan = _plan(viewer_budget=3)
        plan.add_step("m1", PlannedStep(LOG, "原木", requested_by="neko"))
        assert plan.charge("m1", LOG) is None
        assert plan.charge("m1", LOG) is None
        assert plan.get("m1").plan_steps[0].steps == 2
        assert plan.charge("m1", LOG) is None  # 手順だけが断念され、大目標は続く
        dropped = plan.take_dropped_steps()
        assert [(s.spec, s.state) for s in dropped] == [(LOG, JobState.DROPPED)]
        assert "over the budget" in dropped[0].ended_because
        assert plan.get("m1").plan_steps == () and plan.get("m1").steps == 3
        assert plan.take_dropped_steps() == ()

    def test_the_steps_of_the_streamer_have_no_budget(self):
        plan = _plan(viewer_budget=2)
        plan.set_steps("m1", (PlannedStep(LOG, "原木"),))
        for _ in range(5):
            assert plan.charge("m1", LOG) is None
        assert len(plan.get("m1").plan_steps) == 1
