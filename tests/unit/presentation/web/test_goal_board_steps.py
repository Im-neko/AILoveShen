"""ゴールボードと「今していること」の書式に、手順（中目標）の状態が出ることのテスト（docs/design/50）。"""

import pytest

pytest.importorskip("fastapi", reason="ailoveshen[stream] not installed")

from ailoveshen.domain.entities import MidGoalPlan, PlaySession  # noqa: E402
from ailoveshen.domain.value_objects import (  # noqa: E402
    GoalPredicate,
    GoalSpec,
    HouseBlueprint,
    Mission,
    PlannedStep,
    Side,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import format_activity  # noqa: E402
from ailoveshen.presentation.web.goal_board import goals_snapshot  # noqa: E402

LOG = GoalSpec(GoalPredicate.HAVE, item="log", count=12)
STONE = GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=32)
TORCH = GoalSpec(GoalPredicate.PLACED, item="torch", where="home")
BUILT = GoalSpec(GoalPredicate.BUILT, name="storehouse")


def _session() -> PlaySession:
    plan = MidGoalPlan(mission=Mission("街にしていく"))
    plan.add("保管庫を建てる", (BUILT,))
    plan.set_steps("m1", (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石")))
    plan.add_step("m1", PlannedStep(TORCH, "暗い", requested_by="neko"))
    plan.judge_steps("m1", [("j1", True, ()), ("j2", False, ("cobblestone (3/32)",))])
    return PlaySession(blueprint=HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2), plan=plan)


def test_the_board_lists_the_steps_done_current_and_waiting():
    goal = goals_snapshot(_session().activity())["mid_goals"][0]

    assert goal["kind"] == "build"
    assert [(s["id"], s["state"]) for s in goal["steps"]] == [
        ("j1", "done"),
        ("j2", "current"),
        ("j3", "waiting"),
    ]
    assert goal["steps_done"] == 1
    assert goal["steps"][1]["progress"] == ["cobblestone (3/32)"]
    assert goal["steps"][2]["requested_by"] == "neko"
    assert goal["steps"][0]["label"] == "原木を 12 個そろえる"


def test_the_prompts_show_done_now_and_waiting_with_who_asked():
    text = format_activity(_session().activity())

    assert "手順: 済:have(log, 12) → 今:have(cobblestone, 32) → 待:placed(torch, home)" in text
    assert "（nekoさんの頼み）" in text
