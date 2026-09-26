"""JsonMissionStore アダプタのテスト。"""

from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import (
    GoalPredicate,
    GoalSpec,
    MidGoalState,
    Mission,
    TownDefinition,
    TownSite,
    TownStage,
)
from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore

BUILT = GoalSpec(GoalPredicate.BUILT)
BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
SWORD = GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1)


class TestJsonMissionStore:
    """再起動をまたいで計画を残すことのテスト。"""

    def test_nothing_saved(self, tmp_path):
        """ファイルがなければ、何も保存していない。"""
        assert JsonMissionStore(tmp_path / "mission.json").load() is None

    def test_round_trip(self, tmp_path):
        """保存したものがそのまま戻る（id、視聴者、ステップ、終わったもの）。"""
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
        """街、済んだ段階、段階の小目標がそのまま戻る。"""
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
        """能力を待つ段階の、満たした条件が戻る。"""
        lit = GoalSpec(GoalPredicate.LIT, distance=16)
        plan = MidGoalPlan(mission=Mission("街にしていく"))
        plan.define_town(TownDefinition("街", (TownStage("明かり", "夜", (lit,), ("柵",)),)))
        plan.complete(plan.add("明かり", (lit,), stage=0).id)
        store = JsonMissionStore(tmp_path / "mission.json")

        store.save(plan)
        saved = store.load()

        assert (saved.town_stage, saved.stage_met) == (0, (lit,))

    def test_site_and_town_goals_round_trip(self, tmp_path):
        """選んだ場所と、街の準備の中目標（やめられない）が戻る。"""
        plan = MidGoalPlan(mission=Mission("街にしていく"))
        plan.choose_site(TownSite("E", 96, 0, "石が多い", "いしのまち"))
        plan.add("引っ越す", (BUILT, BED), prepares_town=True)
        store = JsonMissionStore(tmp_path / "mission.json")

        store.save(plan)
        saved = store.load()

        assert saved.site == plan.site
        assert saved.pending[0].prepares_town


def test_the_steps_of_a_mid_goal_survive_a_restart(tmp_path):
    """Gemini が書いた手順（docs/design/26）と、名前付きの建物の条件も残る。"""
    from ailoveshen.domain.value_objects import PlannedStep

    plan = MidGoalPlan(mission=Mission("街にしていく"))
    annex = GoalSpec(GoalPredicate.BUILT, name="annex")
    goal = plan.add("増築", (annex,))
    steps = (
        PlannedStep(GoalSpec(GoalPredicate.HAVE, item="log", count=12), "原木"),
        PlannedStep(annex, "建てる"),
    )
    plan.set_steps(goal.id, steps)
    store = JsonMissionStore(tmp_path / "mission.json")
    store.save(plan)
    saved = store.load().pending[0]
    assert saved.plan_steps == steps
    assert saved.conditions == (annex,)
