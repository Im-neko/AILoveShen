"""計画の検査（`plan_lint`）と、全大目標の世界との照合・終わった大目標の中目標を閉じる（docs/design/50 §19）。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.job_planner import JobPlanner
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.plan_lint import check_plan, unregistered_builds
from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.exceptions import GameBridgeError, GoalRejectedError
from ailoveshen.domain.value_objects import (
    ConditionStatus,
    GoalPredicate,
    GoalSpec,
    Job,
    JobState,
    Mission,
    Step,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)
from ailoveshen.presentation.web.admin import goal_tree
from ailoveshen.presentation.web.goal_board import _jobs
from tests.unit.application.test_job_plan import _data
from tests.unit.application.test_play_use_cases import _judged, _session

HOUSE = GoalSpec(GoalPredicate.BUILT)  # 家そのもの: 名前なし
HOUSE_NAMED = GoalSpec(GoalPredicate.BUILT, name="house")  # そんな名前の建物はない
TOWER = GoalSpec(GoalPredicate.BUILT, name="tower")
PLANKS = GoalSpec(GoalPredicate.HAVE, item="oak_planks", count=59)
DOOR = GoalSpec(GoalPredicate.HAVE, item="oak_door", count=1)
STONE_PICK = GoalSpec(GoalPredicate.HAVE, item="stone_pickaxe", count=1)
WOOD_PICK = GoalSpec(GoalPredicate.HAVE, item="wooden_pickaxe", count=1)
COBBLE = GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=3)


def _objective(*conditions: GoalSpec):
    plan = ObjectivePlan(mission=Mission("街にする"))
    plan.add("自分の家を作る", conditions or (HOUSE,))
    return plan, plan.current


class TestLint:
    def test_a_named_build_that_does_not_exist_is_refused(self):
        _, objective = _objective()
        jobs = (Job(PLANKS, title="板材"), Job(HOUSE_NAMED, title="家の建築"))
        with pytest.raises(ValueError, match=r"built\(house\).*does not exist.*built\(\), with no name"):
            check_plan(jobs, objective, builds=set())

    def test_a_registered_build_or_the_objectives_own_named_build_is_fine(self):
        _, objective = _objective(TOWER)
        check_plan((Job(HOUSE_NAMED, title="家"),), objective, builds={"house"})
        check_plan((Job(TOWER, title="塔"),), objective, builds=set())
        assert unregistered_builds([TOWER], objective, set()) == []

    def test_a_step_naming_a_missing_build_is_refused_too(self):
        _, objective = _objective()
        job = Job(PLANKS, title="板材", steps=(Step(HOUSE_NAMED, "建てる"),))
        with pytest.raises(ValueError, match="does not exist"):
            check_plan((job,), objective, builds=set())

    def test_the_builds_are_not_checked_when_they_are_unknown(self):
        _, objective = _objective()
        check_plan((Job(HOUSE_NAMED, title="家"),), objective, builds=None)

    def test_the_objectives_own_condition_is_fine_as_the_last_job_only(self):
        _, objective = _objective()
        check_plan((Job(PLANKS, title="板材"), Job(HOUSE, title="家の建築")), objective, builds=set())
        with pytest.raises(ValueError, match="not the last job"):
            check_plan((Job(HOUSE, title="家の建築"), Job(PLANKS, title="板材")), objective, builds=set())

    def test_two_jobs_with_the_same_condition_or_the_same_name_are_refused(self):
        _, objective = _objective()
        with pytest.raises(ValueError, match="same condition"):
            check_plan((Job(PLANKS, title="a"), Job(PLANKS, title="b")), objective)
        with pytest.raises(ValueError, match="both named 石材の確保と工具作成"):
            check_plan(
                (Job(WOOD_PICK, title="石材の確保と工具作成"), Job(STONE_PICK, title="石材の確保と工具作成")),
                objective,
            )

    def test_a_name_of_a_job_already_done_is_refused_but_the_same_job_again_is_not(self):
        plan, objective = _objective()
        plan.set_jobs("m1", (Job(WOOD_PICK, title="石材の確保と工具作成"),))
        plan.judge_jobs("m1", [("j1", True, ())])
        objective = plan.current
        with pytest.raises(ValueError, match="already done"):
            check_plan((Job(STONE_PICK, title="石材の確保と工具作成"),), objective)
        # 済んだ中目標をそのまま書いてきたのは、足されずに済みのまま残る（誤りではない）
        check_plan((Job(WOOD_PICK, title="石材の確保と工具作成"), Job(DOOR, title="扉")), objective)


class TestPlannerAppliesTheLint:
    @pytest.mark.asyncio
    async def test_a_plan_with_a_missing_build_is_sent_back_with_the_reason(self):
        text = AsyncMock()
        text.generate_json.side_effect = [
            _data(predicate="built", item=None, count=None, name="house", steps=[]),
            _data(),
        ]
        bridge = AsyncMock()
        bridge.check.side_effect = _judged()
        bridge.builds.return_value = []
        keeper = ObjectiveKeeper(bridge=bridge, event_publisher=AsyncMock(), store=Mock())
        planner = JobPlanner(text, GamePromptTemplateBuilder(), bridge, keeper)
        plan, _ = _objective(GoalSpec(GoalPredicate.FACILITY, name="wheat_farm_3"))
        session = _session(plan=plan)
        assert await planner.plan(session, session.activity()) is True
        assert "does not exist" in text.generate_json.call_args_list[1].args[0]
        assert session.plan.current.jobs[0].spec.predicate == GoalPredicate.HAVE


def _keeper(bridge):
    return ObjectiveKeeper(bridge=bridge, event_publisher=AsyncMock(), store=Mock())


def _two_objectives():
    """家（中目標 3 つ: 木のツルハシ、石のツルハシ、家）と、その次の大目標（塔）。"""
    plan = ObjectivePlan(mission=Mission("街にする"))
    plan.add("自分の家を作る", (HOUSE,))
    plan.add("塔を建てる", (TOWER,))
    plan.set_jobs(
        "m1",
        (
            Job(WOOD_PICK, title="木の道具"),
            Job(STONE_PICK, title="石の道具", steps=(Step(COBBLE, "石"), Step(STONE_PICK, "作る"))),
            Job(HOUSE, title="家の建築"),
        ),
    )
    plan.set_jobs("m2", (Job(DOOR, title="扉", steps=(Step(PLANKS, "板"),)),))
    return plan


class TestReconcile:
    @pytest.mark.asyncio
    async def test_every_objectives_jobs_and_steps_are_checked_against_the_world(self):
        plan = _two_objectives()
        bridge = AsyncMock()
        bridge.check.side_effect = _judged({STONE_PICK, COBBLE, PLANKS})
        await _keeper(bridge)._reconcile(plan)
        first, second = plan.pending
        assert first.jobs[1].met_now == (STONE_PICK.describe(), COBBLE.describe())
        assert first.jobs[0].met_now == () and first.jobs[2].met_now == ()
        # 一番上でない大目標の中目標も
        assert second.jobs[0].met_now == (PLANKS.describe(),)
        # 済み（ラッチ）は変えない
        assert first.jobs[1].state == JobState.PENDING and first.jobs[1].steps_done == ()

    @pytest.mark.asyncio
    async def test_a_done_job_is_checked_too_and_a_broken_condition_is_not_met_now(self):
        plan = _two_objectives()
        plan.judge_jobs("m1", [("j2", True, ())])  # 石のツルハシは済み（ラッチ）
        bridge = AsyncMock()
        bridge.check.side_effect = _judged(set())  # 今は何も持っていない
        await _keeper(bridge)._reconcile(plan)
        job = plan.current.jobs[1]
        assert job.state == JobState.DONE and job.met_now == ()

    @pytest.mark.asyncio
    async def test_a_condition_the_bridge_rejects_is_not_met_and_does_not_hide_the_others(self):
        plan = _two_objectives()
        bridge = AsyncMock()

        async def check(specs):
            if any(s == TOWER or s == PLANKS for s in specs):
                raise GoalRejectedError("no")
            return [ConditionStatus(s, True) for s in specs]

        bridge.check.side_effect = check
        await _keeper(bridge)._reconcile(plan)
        first, second = plan.pending
        assert first.jobs[0].met_now == (WOOD_PICK.describe(),)
        assert second.jobs[0].met_now == (DOOR.describe(),)  # 板材だけが拒否された

    @pytest.mark.asyncio
    async def test_an_unreachable_bridge_keeps_the_last_reconcile(self):
        plan = _two_objectives()
        bridge = AsyncMock()
        bridge.check.side_effect = _judged({WOOD_PICK})
        keeper = _keeper(bridge)
        await keeper._reconcile(plan)
        bridge.check.side_effect = GameBridgeError("down")
        await keeper._reconcile(plan)
        assert plan.current.jobs[0].met_now == (WOOD_PICK.describe(),)

    @pytest.mark.asyncio
    async def test_a_job_that_names_a_build_that_does_not_exist_is_dropped(self):
        plan, _ = _objective()
        plan.set_jobs("m1", (Job(PLANKS, title="板材"), Job(HOUSE_NAMED, title="家の建築")))
        bridge = AsyncMock()
        bridge.builds.return_value = []
        await _keeper(bridge)._drop_unjudgeable(plan)
        assert [j.title for j in plan.current.jobs] == ["板材"]

    @pytest.mark.asyncio
    async def test_a_viewers_job_is_not_dropped_and_the_bridge_is_not_asked_without_a_suspect(self):
        plan, _ = _objective()
        plan.set_jobs("m1", (Job(PLANKS, title="板材"),))
        bridge = AsyncMock()
        await _keeper(bridge)._drop_unjudgeable(plan)
        bridge.builds.assert_not_awaited()
        plan.add_job("m1", Job(HOUSE_NAMED, requested_by="viewer"))
        await _keeper(bridge)._drop_unjudgeable(plan)
        assert len(plan.current.jobs) == 2


class TestClosing:
    def test_when_an_objective_is_done_its_jobs_left_are_closed(self):
        plan = _two_objectives()
        plan.judge_jobs("m1", [("j1", True, ())])
        plan.complete("m1")
        (done,) = plan.finished
        assert [j.state for j in done.jobs] == [JobState.DONE, JobState.CLOSED, JobState.CLOSED]
        assert not done.open_jobs

    def test_a_dropped_objective_closes_its_jobs_too(self):
        plan = _two_objectives()
        plan.drop("m1", "not needed")
        assert all(j.state == JobState.CLOSED for j in plan.finished[0].jobs)

    def test_an_objective_finished_before_this_change_is_shown_closed(self):
        plan = _two_objectives()
        old = plan.current
        plan.complete("m1")
        plan._finished.clear()
        plan._finished.append(old)  # 閉じないまま保存されていた
        assert all(j.state == JobState.CLOSED for j in plan.finished[0].jobs)


class TestShown:
    def test_a_step_met_now_is_checked_and_the_next_one_is_the_first_not_met(self):
        plan = _two_objectives()
        plan.reconcile("m1", {"j2": [COBBLE.describe()]})
        jobs = _jobs(plan.current, True)
        assert [(s["label"], s["state"]) for s in jobs[1]["steps"]] == [
            ("丸石を 3 個そろえる", "met"),
            ("石のツルハシを手に入れる", "waiting"),  # 一番先の中目標ではないので今は付けない
        ]
        assert jobs[0]["state"] == "current"

    def test_a_job_met_now_is_met_and_the_next_job_is_current(self):
        plan = _two_objectives()
        plan.reconcile("m1", {"j1": [WOOD_PICK.describe()]})
        states = [j["state"] for j in _jobs(plan.current, True)]
        assert states == ["met", "current", "waiting"]
        # 現在の中目標の小目標だけ「今」が付く
        assert [s["state"] for s in _jobs(plan.current, True)[1]["steps"]] == ["current", "waiting"]
        assert [s["state"] for s in _jobs(plan.current, False)[1]["steps"]] == ["waiting", "waiting"]

    def test_the_steps_of_a_done_job_are_all_done_even_when_never_judged(self):
        plan = _two_objectives()
        plan.judge_jobs("m1", [("j2", True, ())])
        steps = _jobs(plan.current, True)[1]["steps"]
        assert [s["state"] for s in steps] == ["done", "done"]

    def test_closed_jobs_show_the_steps_they_never_met_as_closed_and_do_not_count(self):
        plan = _two_objectives()
        plan.reconcile("m1", {"j2": [COBBLE.describe()]})
        plan.complete("m1")
        (done,) = plan.finished
        jobs = _jobs(done)
        assert [j["state"] for j in jobs] == ["closed", "closed", "closed"]
        assert [s["state"] for s in jobs[1]["steps"]] == ["met", "closed"]  # 最後に照合したとき満たしていた分

    def test_the_admin_tree_carries_the_new_states(self):
        plan = _two_objectives()
        plan.reconcile("m1", {"j1": [WOOD_PICK.describe()], "j2": [COBBLE.describe()]})
        goals = {
            "playing": True,
            "mission": "街にする",
            "goal": None,
            "objectives": [
                {
                    "id": g.id,
                    "title": g.title,
                    "state": "current" if i == 0 else "pending",
                    "jobs": _jobs(g, i == 0),
                    "conditions": [],
                    "progress": [],
                }
                for i, g in enumerate(plan.pending)
            ],
        }
        tree = goal_tree(goals)
        first = tree["children"][0]
        assert [c["state"] for c in first["children"]] == ["met", "current", "waiting"]
        assert [c["state"] for c in first["children"][1]["children"]] == ["met", "current"]


class TestJudge:
    @pytest.mark.asyncio
    async def test_judging_reconciles_every_objective_and_closes_the_one_that_is_done(self):
        plan = _two_objectives()
        bridge = AsyncMock()
        bridge.builds.return_value = []
        bridge.check.side_effect = _judged({HOUSE, DOOR})  # 家は建った。扉は持っている
        await _keeper(bridge).judge(plan)
        assert [g.id for g in plan.pending] == ["m2"]
        assert plan.pending[0].jobs[0].met_now == (DOOR.describe(),)
        (done,) = plan.finished
        assert done.state.value == "done" and not done.open_jobs
        assert all(j.state == JobState.CLOSED for j in done.jobs)


def test_closed_jobs_survive_saving_and_loading(tmp_path):
    from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore

    plan = _two_objectives()
    plan.reconcile("m1", {"j1": [WOOD_PICK.describe()]})
    plan.complete("m1")
    store = JsonMissionStore(tmp_path / "mission.json")
    store.save(plan)
    saved = store.load()
    assert [j.state for j in saved.finished[0].jobs] == [JobState.CLOSED] * 3
    assert all(j.met_now == () for j in saved.pending[0].jobs)  # 照合は保存しない（古い真偽を残さない）
