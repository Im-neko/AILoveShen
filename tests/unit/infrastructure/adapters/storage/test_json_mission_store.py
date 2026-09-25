"""Tests for JsonMissionStore adapter."""

from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import (
    GoalPredicate,
    GoalSpec,
    MidGoalState,
    Mission,
    TownDefinition,
    TownStage,
)
from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore

BUILT = GoalSpec(GoalPredicate.BUILT)
BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
SWORD = GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1)


class TestJsonMissionStore:
    """Tests for keeping the plan across restarts."""

    def test_nothing_saved(self, tmp_path):
        """Test a missing file means nothing was saved."""
        assert JsonMissionStore(tmp_path / "mission.json").load() is None

    def test_round_trip(self, tmp_path):
        """Test what is saved comes back as it was (ids, viewers, steps, finished)."""
        plan = MidGoalPlan(mission=Mission("街にしていく"))
        plan.add("家", (BUILT,))
        request = plan.add("ベッド", (BED,), reason="頼まれた", requested_by="neko")
        plan.add("剣", (SWORD,))
        plan.charge(request.id)
        plan.judged(request.id, ("a bed in the house: no",))
        plan.complete("m1")
        store = JsonMissionStore(tmp_path / "sub" / "mission.json")

        store.save(plan)
        saved = store.load()

        assert saved.mission == Mission("街にしていく")
        assert saved.pending == plan.pending
        assert saved.finished == plan.finished
        assert saved.finished[0].state == MidGoalState.DONE
        assert saved.pending[0].steps == 1
        assert saved.next_id == 4
        assert not (tmp_path / "sub" / "mission.json.tmp").exists()

    def test_town_round_trip(self, tmp_path):
        """Test the town, its stage done and the stage goal come back as they were."""
        town = TownDefinition(
            "小さな街",
            (
                TownStage("家", "住む", conditions=(BUILT,)),
                TownStage("明かり", "夜", conditions=(GoalSpec(GoalPredicate.LIT, distance=16),)),
                TownStage("倉庫", "街らしく", unresolved=("2 軒目",)),
            ),
        )
        plan = MidGoalPlan(mission=Mission("街にしていく"))
        plan.define_town(town)
        plan.add("家", (BUILT,), stage=0)
        plan.complete("m1")
        plan.add("明かり", town.stages[1].conditions, stage=1)
        store = JsonMissionStore(tmp_path / "mission.json")

        store.save(plan)
        saved = store.load()

        assert saved.town == town
        assert saved.town_stage == 1
        assert saved.pending[0].stage == 1
        assert saved.finished[0].stage == 0
        assert saved.stage_met == ()

    def test_stage_progress_round_trip(self, tmp_path):
        """Test the conditions met of a stage waiting for an ability come back."""
        lit = GoalSpec(GoalPredicate.LIT, distance=16)
        plan = MidGoalPlan(mission=Mission("街にしていく"))
        plan.define_town(TownDefinition("街", (TownStage("明かり", "夜", (lit,), ("柵",)),)))
        plan.complete(plan.add("明かり", (lit,), stage=0).id)
        store = JsonMissionStore(tmp_path / "mission.json")

        store.save(plan)
        saved = store.load()

        assert (saved.town_stage, saved.stage_met) == (0, (lit,))
