"""JsonMissionStore: 手順（中目標）の状態と、読めない形の始め直し（docs/design/50 §7）。"""

import json

from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import (
    GoalPredicate,
    GoalSpec,
    JobState,
    Mission,
    PlannedStep,
)
from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore

BUILT = GoalSpec(GoalPredicate.BUILT)
LOG = GoalSpec(GoalPredicate.HAVE, item="log", count=12)
STONE = GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=8)
SWORD = GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1)


def test_steps_keep_their_state_origin_budget_and_progress(tmp_path):
    plan = MidGoalPlan(mission=Mission("街にしていく"))
    plan.add("家", (BUILT,))
    plan.set_steps("m1", (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石", together=(LOG,))))
    plan.add_step("m1", PlannedStep(SWORD, "剣", requested_by="neko"))
    plan.judge_steps("m1", [("j1", True, ()), ("j2", False, ("cobblestone (3/8)",))])
    plan.charge("m1", SWORD)
    store = JsonMissionStore(tmp_path / "mission.json")

    store.save(plan)
    saved = store.load()

    assert saved.pending == plan.pending
    assert saved.next_step_id == plan.next_step_id == 4
    steps = saved.pending[0].plan_steps
    assert [(s.id, s.state) for s in steps] == [
        ("j1", JobState.DONE),
        ("j2", JobState.PENDING),
        ("j3", JobState.PENDING),
    ]
    assert steps[1].progress == ("cobblestone (3/8)",)
    assert (steps[2].requested_by, steps[2].steps) == ("neko", 1)


def test_a_file_in_another_format_starts_over_and_is_kept_aside(tmp_path):
    path = tmp_path / "mission.json"
    old = (
        '{"mission": "街にしていく", "pending": [], "finished": [], "next_id": 3,'
        ' "town": null, "town_stage": 0, "stage_met": [], "site": null, "town_map": null}'
    )
    path.write_text(old, encoding="utf-8")

    assert JsonMissionStore(path).load() is None
    assert (tmp_path / "mission.json.unreadable").read_text(encoding="utf-8") == old


def test_a_broken_file_starts_over_too(tmp_path):
    path = tmp_path / "mission.json"
    path.write_text("{not json", encoding="utf-8")

    assert JsonMissionStore(path).load() is None
    assert (tmp_path / "mission.json.unreadable").exists()


def test_a_mid_goal_that_mixes_a_build_with_materials_is_unreadable(tmp_path):
    plan = MidGoalPlan(mission=Mission("街にしていく"))
    plan.add("家", (BUILT,))
    store = JsonMissionStore(tmp_path / "mission.json")
    store.save(plan)
    data = json.loads((tmp_path / "mission.json").read_text(encoding="utf-8"))
    data["pending"][0]["conditions"].append({"predicate": "have", "item": "log", "count": 4})
    (tmp_path / "mission.json").write_text(json.dumps(data), encoding="utf-8")

    assert store.load() is None
