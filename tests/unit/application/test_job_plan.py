"""大目標の計画（中目標と、その小目標の並び。前提つき）のテスト（docs/design/50 §18）。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.goal_chooser import ChosenGoal, GoalChooser
from ailoveshen.application.use_cases.goal_vocabulary import goal_schema
from ailoveshen.application.use_cases.job_planner import (
    JobPlanner,
    parse_plan,
    plan_schema,
    unplanned,
)
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.exceptions import GoalRejectedError
from ailoveshen.domain.value_objects import (
    ConditionStatus,
    Goal,
    GoalPredicate,
    GoalStatus,
    GoalSpec,
    Job,
    JobState,
    Mission,
    Step,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import format_activity
from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore
from ailoveshen.presentation.web.goal_board import _jobs
from tests.unit.application.test_play_use_cases import FakeJev, _judged, _obs, _session

IRON = GoalSpec(GoalPredicate.HAVE, item="iron_ingot", count=3)
BUCKET = GoalSpec(GoalPredicate.HAVE, item="bucket", count=1)
WATER = GoalSpec(GoalPredicate.HAVE, item="water_bucket", count=1)
HOE = GoalSpec(GoalPredicate.HAVE, item="hoe", count=1)
SEEDS = GoalSpec(GoalPredicate.HAVE, item="wheat_seeds", count=16)
FARM = GoalSpec(GoalPredicate.FACILITY, name="wheat_farm_3")
MISSION = Mission("街にしていく")

# 畑を広げる: 水のバケツ（バケツ（鉄））→ 鍬 → 種 → 畑
WATER_STEPS = (Step(WATER, "水を汲む", needs=(Step(BUCKET, "バケツ", needs=(Step(IRON, "鉄"),)),)),)


def _job() -> Job:
    return Job(WATER, "水の用意", title="水の確保", steps=WATER_STEPS)


def _plan(*jobs: Job) -> ObjectivePlan:
    plan = ObjectivePlan(mission=MISSION)
    plan.add("畑を広げる", (FARM,))
    if jobs:
        plan.set_jobs("m1", jobs)
    return plan


def _met(*specs: GoalSpec):
    return _judged(set(specs))


class TestStep:
    def test_prerequisites_come_first_and_only_when_the_step_is_not_met(self):
        job = _job()
        assert job.next_step({}).spec == IRON  # 何もない: 鉄から
        assert job.next_step({IRON.describe(): True}).spec == BUCKET
        assert job.next_step({IRON.describe(): True, BUCKET.describe(): True}).spec == WATER
        # バケツを持っていれば、その前提の鉄は飛ばす（鉄がなくても）
        assert job.next_step({BUCKET.describe(): True}).spec == WATER
        assert job.next_step({WATER.describe(): True}) is None

    def test_a_done_step_stays_done_when_its_condition_breaks_later(self):
        job = Job(WATER, "水", steps=WATER_STEPS, steps_done=(IRON.describe(), BUCKET.describe()))
        assert job.next_step({}).spec == WATER  # 鉄もバケツも今はなくても

    def test_pending_steps_are_listed_once_without_the_done_ones(self):
        job = Job(WATER, "水", steps=WATER_STEPS, steps_done=(IRON.describe(),))
        assert [s.spec for s in job.pending_steps()] == [BUCKET, WATER]

    def test_limits(self):
        two = Step(IRON, needs=(Step(IRON, needs=(Step(IRON),)),))
        assert two.depth == 2
        with pytest.raises(ValueError, match="2 levels"):
            Step(WATER, needs=(two,))
        with pytest.raises(ValueError, match="at most 3 prerequisites"):
            Step(WATER, needs=(Step(IRON),) * 4)
        with pytest.raises(ValueError, match="cannot be a step"):
            Step(GoalSpec(GoalPredicate.CUSTOM, name="x"))
        with pytest.raises(ValueError, match="at most 8 steps"):
            Job(WATER, steps=(Step(IRON),) * 9)


class TestPlanInThePlan:
    def test_rewriting_a_job_keeps_its_steps_and_the_ones_done_that_are_still_planned(self):
        plan = _plan(Job(WATER, "水", title="水の確保", steps=WATER_STEPS))
        plan.judge_steps("m1", {"j1": [IRON.describe()]})
        assert plan.current.jobs[0].steps_done == (IRON.describe(),)
        # 目標の決定が同じ条件の中目標を（計画なしで）書き直しても、計画と済みは残る
        plan.set_jobs("m1", (Job(WATER, "水を汲む"),))
        job = plan.current.jobs[0]
        assert job.title == "水の確保" and job.steps == WATER_STEPS
        assert job.steps_done == (IRON.describe(),)
        # 新しい計画では、残っていない小目標の済みは捨てる。死んだあとは全部捨てる
        replaced = Job(WATER, "水", steps=(Step(WATER),))
        plan.set_jobs("m1", (replaced,))
        assert plan.current.jobs[0].steps_done == ()
        plan.judge_steps("m1", {"j1": [WATER.describe()]})
        plan.set_jobs("m1", (replaced,), keep_done_steps=False)
        assert plan.current.jobs[0].steps_done == ()

    def test_planned_is_set_with_the_jobs_and_a_job_out_of_steps_needs_a_new_plan(self):
        plan = _plan()
        assert unplanned(plan.current)
        plan.set_jobs("m1", (_job(),), planned=True)
        assert plan.current.planned and not unplanned(plan.current)
        plan.judge_jobs("m1", [("j1", True, ())])  # 中目標が尽きたが大目標は済んでいない
        assert unplanned(plan.current)

    def test_a_done_step_key_that_is_not_in_the_plan_is_ignored(self):
        plan = _plan(_job())
        plan.judge_steps("m1", {"j1": ["have(sand, 9)"]})
        assert plan.current.jobs[0].steps_done == ()


class TestStore:
    def test_the_plan_survives_a_restart(self, tmp_path):
        store = JsonMissionStore(tmp_path / "mission.json")
        plan = _plan(_job())
        plan.set_jobs("m1", (_job(),), planned=True)
        plan.judge_steps("m1", {"j1": [IRON.describe()]})
        store.save(plan)
        loaded = store.load().pending[0]
        job = loaded.jobs[0]
        assert loaded.planned
        assert (job.title, job.steps, job.steps_done) == ("水の確保", WATER_STEPS, (IRON.describe(),))

    def test_a_file_without_plans_loads_as_unplanned(self, tmp_path):
        store = JsonMissionStore(tmp_path / "mission.json")
        store.save(_plan(Job(WATER, "水")))
        loaded = store.load().pending[0]
        assert not loaded.planned and loaded.jobs[0].steps == ()


class TestChooser:
    @pytest.fixture
    def bridge(self):
        b = AsyncMock()
        b.check.side_effect = _judged()
        return b

    @pytest.mark.asyncio
    async def test_the_next_step_of_the_plan_is_the_small_goal(self, bridge):
        plan = _plan(_job())
        picked = await GoalChooser(FakeJev("A"), bridge).choose(_session(plan=plan), _obs(), "met")
        assert isinstance(picked, ChosenGoal) and picked.spec == IRON
        assert picked.reason == "水の確保: 鉄" and picked.objective_id == "m1"

    @pytest.mark.asyncio
    async def test_what_is_already_held_is_skipped_and_the_step_is_remembered_done(self, bridge):
        plan = _plan(_job())
        bridge.check.side_effect = _met(BUCKET)
        picked = await GoalChooser(FakeJev("A"), bridge).choose(_session(plan=plan), _obs(), "met")
        assert picked.spec == WATER  # バケツはある: 鉄は飛ばす
        assert plan.current.jobs[0].steps_done == (BUCKET.describe(),)
        # 水の小目標がこの中目標そのものと同じ条件なので、今までの選択肢（中目標の理由）のまま
        assert picked.reason == "水の用意"

    @pytest.mark.asyncio
    async def test_a_step_that_is_the_job_itself_is_not_a_step(self, bridge):
        # 計画の最後の小目標が中目標と同じ条件なら、束や解決器の次の一歩を使う今までの選択肢
        job = Job(WATER, "水の用意", title="水", steps=(Step(WATER),))
        picked = await GoalChooser(FakeJev("A"), bridge).choose(
            _session(plan=_plan(job)), _obs(), "met"
        )
        assert picked.spec == WATER and picked.reason == "水の用意"

    @pytest.mark.asyncio
    async def test_a_job_without_a_plan_is_chosen_as_before(self, bridge):
        picked = await GoalChooser(FakeJev("A"), bridge).choose(
            _session(plan=_plan(Job(WATER, "水"))), _obs(), "met"
        )
        assert picked.spec == WATER

    @pytest.mark.asyncio
    async def test_the_small_goal_never_reads_the_same_as_the_job(self, bridge):
        # 中目標の名前（title）は段階の名前、小目標は条件: 同じ文にならない（元の不具合）
        plan = _plan(_job())
        picked = await GoalChooser(FakeJev("A"), bridge).choose(_session(plan=plan), _obs(), "met")
        job = plan.current.jobs[0]
        assert picked.spec.describe() != job.title and picked.spec != job.spec

    @pytest.mark.asyncio
    async def test_a_finished_job_leaves_the_plan_and_the_next_job_is_offered(self, bridge):
        plan = _plan(_job(), Job(FARM, "畑", title="畑", steps=(Step(HOE, "鍬"),)))
        bridge.check.side_effect = _met(WATER)
        picked = await GoalChooser(FakeJev("A"), bridge).choose(_session(plan=plan), _obs(), "met")
        assert picked.spec == HOE
        assert plan.current.jobs[0].state == JobState.DONE


def _data(**over):
    job = {
        "title": "水の確保",
        "predicate": "have",
        "item": "water_bucket",
        "count": 1,
        "reason": "畑に水",
        "steps": [
            {
                "predicate": "have",
                "item": "water_bucket",
                "count": 1,
                "reason": "水を汲む",
                "needs": [
                    {
                        "predicate": "have",
                        "item": "bucket",
                        "count": 1,
                        "reason": "バケツ",
                        "needs": [
                            {"predicate": "have", "item": "iron_ingot", "count": 3, "reason": "鉄"}
                        ],
                    }
                ],
            }
        ],
    }
    return {"outline": "水、農具、種、畑の順", "jobs": [{**job, **over}]}


class TestParse:
    def test_the_answer_becomes_jobs_with_titles_and_steps(self):
        (job,) = parse_plan(_data())
        assert job.title == "水の確保" and job.spec == WATER
        assert job.steps == WATER_STEPS

    @pytest.mark.parametrize(
        ("over", "why"),
        [
            ({"title": " "}, "needs a title"),
            ({"predicate": "custom", "name": "x"}, "cannot be a job here"),
            ({"steps": [{"predicate": "custom", "name": "x", "reason": "r"}]}, "cannot be a step"),
            ({"steps": [{"predicate": "have", "reason": "r"}]}, "step 1"),
        ],
    )
    def test_a_bad_plan_says_which_job_or_step(self, over, why):
        with pytest.raises(ValueError, match=why):
            parse_plan(_data(**over))

    def test_every_problem_is_named_at_once(self):
        """1 件ずつ差し戻すと、直したそばから次を踏んで書き直しが尽きる。"""
        data = _data()
        bad = {"title": " ", "predicate": "custom", "name": "x", "reason": "r", "steps": [{"predicate": "custom", "name": "x", "reason": "r"}, {"predicate": "have", "reason": "r"}]}
        data["jobs"] = [bad, dict(data["jobs"][0]), {**data["jobs"][0], "title": ""}]
        with pytest.raises(ValueError) as e:
            parse_plan(data)
        text = str(e.value)
        assert text.count("job 1:") == 1 and "job 3:" in text and "job 2:" not in text
        assert "needs a title" in text and "step 1: " in text and "step 2: " in text and "cannot be a job here" in text

    def test_no_jobs_is_refused(self):
        with pytest.raises(ValueError, match="at least one job"):
            parse_plan({"outline": "", "jobs": []})

    def test_the_schema_nests_prerequisites_two_levels_and_has_no_custom(self):
        schema = plan_schema()
        job = schema["properties"]["jobs"]["items"]
        step = job["properties"]["steps"]["items"]
        assert "custom" not in step["properties"]["predicate"]["enum"]
        level1 = step["properties"]["needs"]["items"]
        level2 = level1["properties"]["needs"]["items"]
        assert "needs" not in level2["properties"]
        assert schema["required"] == ["outline", "jobs"]

    def test_the_goal_decision_leaves_jobs_to_the_plan(self):
        args = ([GoalPredicate.HAVE], ["m1"])
        assert "jobs" in goal_schema(*args)["properties"]
        assert "jobs" not in goal_schema(*args, plan_owns_jobs=True)["properties"]


class TestPlanner:
    def _planner(self, answers, bridge=None):
        text = AsyncMock()
        text.generate_json.side_effect = answers
        bridge = bridge or AsyncMock()
        bridge.check.side_effect = _judged()
        store = Mock()
        keeper = ObjectiveKeeper(bridge=bridge, event_publisher=AsyncMock(), store=store)
        planner = JobPlanner(text, GamePromptTemplateBuilder(), bridge, keeper)
        return planner, text, bridge, store

    @pytest.mark.asyncio
    async def test_the_plan_is_written_saved_and_marks_the_objective(self):
        planner, text, bridge, store = self._planner([_data()])
        session = _session(plan=_plan())
        assert await planner.plan(session, session.activity()) is True
        assert text.generate_json.call_args.kwargs["purpose"] == "plan"
        current = session.plan.current
        assert current.planned and current.jobs[0].steps == WATER_STEPS
        store.save.assert_called()
        # 書いた条件（中目標と小目標、前提）は全部ブリッジに判定させる
        checked = {s.describe() for s in bridge.check.call_args.args[0]}
        assert {WATER.describe(), BUCKET.describe(), IRON.describe()} <= checked

    @pytest.mark.asyncio
    async def test_a_bad_answer_is_sent_back_with_the_reason(self):
        planner, text, *_ = self._planner([{"outline": "", "jobs": []}, _data()])
        session = _session(plan=_plan())
        assert await planner.plan(session, session.activity()) is True
        assert "at least one job" in text.generate_json.call_args_list[1].args[0]

    @pytest.mark.asyncio
    async def test_a_condition_the_bridge_rejects_is_sent_back(self):
        bridge = AsyncMock()
        planner, text, bridge, _ = self._planner([_data(), _data()], bridge)
        bridge.check.side_effect = [GoalRejectedError("no such facility x"), _judged()]
        # 2 回目の判定は、ふつうの判定に戻す
        calls = iter([GoalRejectedError("no such facility x"), None])

        async def check(specs, materials=False):
            if next(calls) is not None:
                raise GoalRejectedError("no such facility x")
            return [ConditionStatus(s, False) for s in specs]

        bridge.check.side_effect = check
        session = _session(plan=_plan())
        assert await planner.plan(session, session.activity()) is True
        assert "no such facility x" in text.generate_json.call_args_list[1].args[0]

    @pytest.mark.asyncio
    async def test_an_llm_that_does_not_answer_is_not_asked_again(self):
        from ailoveshen.domain.exceptions import TextGenerationError

        planner, text, *_ = self._planner([TextGenerationError("ReadTimeout after 180s")] * 3)
        session = _session(plan=_plan())
        assert await planner.plan(session, session.activity()) is False
        assert text.generate_json.call_count == 1 and not session.plan.current.planned

    @pytest.mark.asyncio
    async def test_giving_up_leaves_the_objective_unplanned(self):
        planner, text, *_ = self._planner([{"jobs": []}] * 3)
        session = _session(plan=_plan())
        assert await planner.plan(session, session.activity()) is False
        assert not session.plan.current.planned and text.generate_json.call_count == 3

    @pytest.mark.asyncio
    async def test_a_replan_shows_the_reason_and_can_drop_what_was_done(self):
        planner, text, *_ = self._planner([_data(steps=[])])
        plan = _plan(_job())
        plan.judge_steps("m1", {"j1": [IRON.describe()]})
        session = _session(plan=plan)
        await planner.plan(
            session, session.activity(), replan_reason="the streamer died", keep_done_steps=False
        )
        assert "the streamer died" in text.generate_json.call_args.args[0]
        assert plan.current.jobs[0].steps_done == ()


class TestShown:
    def test_the_prompt_shows_the_objective_and_asks_for_stages_and_prerequisites(self):
        plan = _plan(_job())
        prompt = GamePromptTemplateBuilder().build_plan_prompt(plan.current, _session(plan=plan).activity())
        assert "畑を広げる" in prompt and "前提（needs）" in prompt and "wheat_farm_3" in prompt

    def test_the_plan_and_goal_prompts_say_the_fence_and_gate_come_after_the_farm_cells(self):
        """畑の柵とゲートは最後（2026-10-04: 柵のための板が先に集められ、畑が 63 手進まなかった）。"""
        plan = _plan(_job())
        builder = GamePromptTemplateBuilder()
        prompt = builder.build_plan_prompt(plan.current, _session(plan=plan).activity())
        for text in (prompt, builder.build_goal_system()):
            assert "柵とゲートは、畑のマス" in text and "最後に" in text and "placed(oak_fence" in text

    def test_the_current_job_shows_its_plan_with_prerequisites_to_the_llm(self):
        plan = _plan(_job())
        plan.judge_steps("m1", {"j1": [IRON.describe()]})
        text = format_activity(_session(plan=plan).activity())
        assert "水の確保" in text
        assert "計画:" in text and "今:have(water_bucket, 1)<have(bucket, 1)" in text

    def test_the_board_shows_the_stage_name_and_the_steps_with_their_depth(self):
        plan = _plan(_job())
        plan.judge_steps("m1", {"j1": [IRON.describe()]})
        (job,) = _jobs(plan.current, True)
        assert job["label"] == "水の確保"
        assert [(s["depth"], s["state"]) for s in job["steps"]] == [
            (0, "waiting"),
            (1, "current"),
            (2, "done"),
        ]


class TestAdvancePlay:
    """`AdvancePlayUseCase` が、大目標が上に来たら計画を書き、失敗の後に書き直す。"""

    @pytest.fixture
    def parts(self):
        from ailoveshen.application.use_cases.notes import NoteKeeper
        from ailoveshen.application.use_cases.play import AdvancePlayUseCase
        from ailoveshen.domain.entities import Conversation

        bridge = AsyncMock()
        bridge.observe.return_value = _obs(met=True)
        bridge.set_goal.return_value = GoalStatus(met=False, remaining=3)
        bridge.check.side_effect = _judged()
        text = AsyncMock()
        builder = Mock()
        builder.build_goal_prompt.return_value = "goal prompt"
        builder.build_goal_system.return_value = "goal system"
        store = Mock()
        store.load.return_value = None
        keeper = ObjectiveKeeper(bridge=bridge, event_publisher=AsyncMock(), store=store)
        notes_store = Mock()
        notes_store.load.return_value = None
        clock = [0.0]
        planner = AsyncMock()

        async def plan(session, activity, replan_reason="", keep_done_steps=True):
            plan.calls.append((replan_reason, keep_done_steps))
            await keeper.set_jobs(session.plan, session.plan.current.id, (_job(),), planned=True)
            return True

        plan.calls = []
        planner.plan.side_effect = plan

        def make(with_planner=True):
            return AdvancePlayUseCase(
                bridge=bridge,
                text_generator=text,
                prompt_builder=builder,
                action_selector=AsyncMock(),
                event_publisher=AsyncMock(),
                conversation=Conversation(),
                objectives=keeper,
                town=AsyncMock(),
                notes=NoteKeeper(notes_store),
                chooser=GoalChooser(FakeJev("A"), bridge),
                clock=lambda: clock[0],
                planner=planner if with_planner else None,
            )

        return make, planner, text, clock, bridge

    @pytest.mark.asyncio
    async def test_an_unplanned_objective_is_planned_then_jev_picks_its_first_step(self, parts):
        make, planner, text, _, _ = parts
        session = _session(plan=_plan())
        await make()._change_goal(session, _obs(met=True))
        assert planner.plan.await_count == 1 and planner.plan.side_effect.calls == [("", True)]
        assert text.generate_json.await_count == 0  # Gemini の決定は呼ばない
        assert session.goal.spec == IRON and session.goal.reason == "水の確保: 鉄"
        # 次の境目には、もう計画済み: 書き直さない
        session.set_goal(session.goal, "day")
        await make()._change_goal(session, _obs(met=True))
        assert planner.plan.await_count == 1

    @pytest.mark.asyncio
    async def test_the_plan_does_not_see_an_ended_small_goal_of_another_objective(self, parts):
        """死んだときの食料と鉄の束を、剣の大目標の計画に見せない（真似て、差し戻されて 40 秒無駄になった: 2026-10-03）。"""
        make, planner, _, _, _ = parts
        seen = []
        inner = planner.plan.side_effect

        async def plan(session, activity, replan_reason="", keep_done_steps=True):
            seen.append(activity.goal)
            return await inner(session, activity, replan_reason, keep_done_steps)

        plan.calls = inner.calls
        planner.plan.side_effect = plan
        session = _session(plan=_plan())
        session.set_goal(Goal(IRON, objective_id=None), "day")  # 生存の小目標
        await make()._change_goal(session, _obs(met=True))
        assert seen == [None]

        # 同じ大目標の小目標は見せる（失敗の後の書き直しの手がかり）
        session = _session(plan=_plan())
        session.set_goal(Goal(IRON, objective_id=session.plan.current.id), "day")
        await make()._change_goal(session, _obs(met=True))
        assert seen[1] is not None

    @pytest.mark.asyncio
    async def test_without_a_planner_the_goal_decision_writes_the_jobs_as_before(self, parts):
        make, planner, text, _, _ = parts
        text.generate_json.return_value = {
            "predicate": "have",
            "item": "water_bucket",
            "count": 1,
            "serves": "current",
            "reason": "水",
            "review": "r",
            "trips": "t",
            "jobs": [{"predicate": "have", "item": "water_bucket", "count": 1, "reason": "水"}],
        }
        await make(with_planner=False)._change_goal(_session(plan=_plan()), _obs(met=True))
        assert planner.plan.await_count == 0 and text.generate_json.await_count == 1

    @pytest.mark.asyncio
    async def test_a_failed_planning_is_not_retried_at_every_boundary(self, parts):
        make, planner, text, clock, _ = parts
        planner.plan.side_effect = None
        planner.plan.return_value = False  # 書けなかった
        text.generate_json.return_value = {
            "predicate": "have",
            "item": "water_bucket",
            "count": 1,
            "serves": "current",
            "reason": "水",
            "review": "r",
            "trips": "t",
            "jobs": [{"predicate": "have", "item": "water_bucket", "count": 1, "reason": "水"}],
        }
        session = _session(plan=_plan())
        use_case = make()
        await use_case._change_goal(session, _obs(met=True))  # 書けない → Gemini が中目標を書く（今までどおり）
        assert planner.plan.await_count == 1 and text.generate_json.await_count == 1
        session.plan.set_jobs("m1", (), planned=False)
        await use_case._change_goal(session, _obs(met=True))
        assert planner.plan.await_count == 1  # 間隔の間は呼ばない
        clock[0] += 301.0
        session.plan.set_jobs("m1", (), planned=False)
        await use_case._change_goal(session, _obs(met=True))
        assert planner.plan.await_count == 2

    @pytest.mark.asyncio
    async def test_after_the_same_kind_of_failure_twice_the_plan_is_rewritten_with_the_reason(
        self, parts
    ):
        make, planner, text, clock, bridge = parts
        session = _session(plan=_plan())
        use_case = make()
        await use_case._change_goal(session, _obs(met=True))  # 初めての計画
        assert planner.plan.await_count == 1
        clock[0] += 400.0
        text.generate_json.return_value = {
            "predicate": "have",
            "item": "iron_ingot",
            "count": 3,
            "serves": "current",
            "reason": "鉄",
            "review": "r",
            "trips": "t",
            "diagnosis": "d",
            "remedy": "change",
        }
        reason = "the objective 畑を広げる has made no progress in 60 actions for it (x)"
        assert await use_case._plan_objective(session, session.activity(), reason, "why") is True
        replan, keep = planner.plan.side_effect.calls[-1]
        assert reason in replan and keep is True
        # 死んだときは、済みにした小目標も捨てる
        clock[0] += 400.0
        await use_case._plan_objective(session, session.activity(), "you died and respawned", "why")
        assert planner.plan.side_effect.calls[-1][1] is False

    @pytest.mark.asyncio
    async def test_a_single_failure_that_jev_handles_does_not_rewrite_the_plan(self, parts):
        make, planner, _, clock, _ = parts
        session = _session(plan=_plan())
        use_case = make()
        await use_case._change_goal(session, _obs(met=True))
        clock[0] += 400.0
        # Jev が別の中目標を選ぶ（Gemini に回さない）失敗では、計画は書き直さない
        assert await use_case._plan_objective(session, session.activity(), "goal x is stuck (y)", None) is False
        assert planner.plan.await_count == 1


class TestObjectiveWatch:
    def test_a_step_done_counts_as_progress(self):
        from ailoveshen.application.use_cases.play import _ObjectiveWatch

        plan = _plan(_job())
        before = _ObjectiveWatch.of(plan.current)
        plan.judge_steps("m1", {"j1": [IRON.describe()]})
        after = _ObjectiveWatch.of(plan.current)
        assert before.progressed_by(after)
        assert not after.progressed_by(_ObjectiveWatch.of(plan.current))


class TestDecisionCannotEraseThePlan:
    @pytest.mark.asyncio
    async def test_jobs_a_model_writes_anyway_are_dropped_while_the_objective_is_planned(self):
        from ailoveshen.application.use_cases.notes import NoteKeeper
        from ailoveshen.application.use_cases.play import AdvancePlayUseCase
        from ailoveshen.domain.entities import Conversation

        bridge = AsyncMock()
        bridge.set_goal.return_value = GoalStatus(met=False, remaining=3)
        bridge.check.side_effect = _judged()
        text = AsyncMock()
        text.generate_json.return_value = {
            "predicate": "have",
            "item": "iron_ingot",
            "count": 3,
            "serves": "current",
            "reason": "鉄",
            "review": "r",
            "trips": "t",
            # スキーマにない欄を書いてくる（別の中目標: 計画の中目標が落とされる書き方）
            "jobs": [{"predicate": "have", "item": "sand", "count": 9, "reason": "別"}],
        }
        builder = Mock()
        builder.build_goal_prompt.return_value = "goal prompt"
        builder.build_goal_system.return_value = "goal system"
        keeper = ObjectiveKeeper(bridge=bridge, event_publisher=AsyncMock(), store=Mock())
        notes_store = Mock()
        notes_store.load.return_value = None
        use_case = AdvancePlayUseCase(
            bridge=bridge,
            text_generator=text,
            prompt_builder=builder,
            action_selector=AsyncMock(),
            event_publisher=AsyncMock(),
            conversation=Conversation(),
            objectives=keeper,
            town=AsyncMock(),
            notes=NoteKeeper(notes_store),
            chooser=None,
        )
        plan = _plan(_job())
        plan.set_jobs("m1", (_job(),), planned=True)
        session = _session(plan=plan)
        await use_case._change_goal(session, _obs(met=True))
        job = session.plan.current.jobs[0]
        assert len(session.plan.current.jobs) == 1 and job.title == "水の確保"
        assert job.steps == WATER_STEPS


def test_the_schema_tells_the_count_limits_and_that_a_where_needs_all_three_numbers():
    """count 100 / 200 と、y のない座標が、3 回とも差し戻された（2026-10-09）。"""
    step = plan_schema()["properties"]["jobs"]["items"]["properties"]["steps"]["items"]["properties"]
    assert "farmed and placed: 1-64" in step["count"]["description"]
    assert "x,z alone is sent back" in step["where"]["description"]
    assert "placed(bed, home)" in step["where"]["description"]
