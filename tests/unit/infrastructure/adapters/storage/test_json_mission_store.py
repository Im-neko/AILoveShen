"""Tests for JsonMissionStore adapter."""

from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import GoalPredicate, GoalSpec, MidGoalState, Mission
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
