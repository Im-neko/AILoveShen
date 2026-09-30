"""管理画面の目標ツリー（設計書 54）のテスト。"""

import pytest

pytest.importorskip("fastapi", reason="ailoveshen[stream] not installed")

from ailoveshen.domain.entities import ObjectivePlan, PlaySession  # noqa: E402
from ailoveshen.domain.value_objects import (  # noqa: E402
    Goal,
    GoalPredicate,
    GoalSpec,
    HouseBlueprint,
    Job,
    Mission,
    Side,
)
from ailoveshen.presentation.web.admin import goal_tree  # noqa: E402
from ailoveshen.presentation.web.goal_board import goals_snapshot  # noqa: E402

LOG = GoalSpec(GoalPredicate.HAVE, item="log", count=12)
STONE = GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=32)
BUILT = GoalSpec(GoalPredicate.BUILT, name="storehouse")
NIGHT = GoalSpec(GoalPredicate.THROUGH_NIGHT)


def _session() -> PlaySession:
    plan = ObjectivePlan(mission=Mission("街にしていく"))
    plan.add("保管庫を建てる", (BUILT,))
    plan.set_jobs("m1", (Job(LOG, "原木"), Job(STONE, "石")))
    plan.judge_jobs("m1", [("j1", True, ()), ("j2", False, ("cobblestone (3/32)",))])
    return PlaySession(blueprint=HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2), plan=plan)


def _tree(session):
    return goal_tree(goals_snapshot(session.activity()))


def test_before_playing_there_is_no_tree():
    assert goal_tree(goals_snapshot(None)) is None


def test_the_tree_goes_from_the_mission_to_objectives_and_jobs():
    tree = _tree(_session())

    assert (tree["layer"], tree["label"]) == ("mission", "街にしていく")
    [objective] = tree["children"]
    assert (objective["layer"], objective["state"]) == ("objective", "current")
    assert [(j["layer"], j["state"]) for j in objective["children"]] == [
        ("job", "done"),
        ("job", "current"),
    ]
    assert "cobblestone (3/32)" in objective["children"][1]["notes"]


def test_the_small_goal_hangs_under_the_current_job_of_the_objective_it_serves():
    session = _session()
    session.set_goal(
        Goal(GoalSpec(GoalPredicate.HAVE, item="oak_log", count=4), "木が要る", "m1"), "day"
    )

    [objective] = _tree(session)["children"]
    [now] = objective["children"][1]["children"]
    assert (now["layer"], now["state"]) == ("goal", "current")
    assert "いまの小目標" in now["notes"]


def test_a_survival_small_goal_hangs_under_the_mission():
    session = _session()
    session.set_goal(Goal(NIGHT, "夜になる", None), "night")

    tree = _tree(session)
    first = tree["children"][0]
    assert first["layer"] == "goal" and "生存のため（大目標を待てない）" in first["notes"]
    assert tree["children"][1]["layer"] == "objective"
