"""大目標と、その手順（中目標）の仕組みのテスト（docs/design/50 §4・§5）。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.application.use_cases.goal_chooser import ChosenGoal, GoalChooser
from ailoveshen.application.use_cases.goal_vocabulary import parse_proposal
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase, _MidWatch
from ailoveshen.domain.entities import Conversation, MidGoalPlan
from ailoveshen.domain.events import MidGoalDroppedEvent
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    CharacterProfile,
    ConditionStatus,
    Goal,
    GoalPredicate,
    GoalSpec,
    JobState,
    Mission,
    PlannedStep,
)
from tests.unit.application.test_play_use_cases import (
    LOG,
    PLANKS,
    FakeJev,
    _judged,
    _obs,
    _session,
)

BUILT = GoalSpec(GoalPredicate.BUILT)
STONE = GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=32)
PLANKS_SPEC = GoalSpec(GoalPredicate.HAVE, item="planks", count=4)
TORCH = GoalSpec(GoalPredicate.PLACED, item="torch", where="home")
BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
MISSION = Mission("街にしていく")


def _plan(*steps: PlannedStep, **kwargs) -> MidGoalPlan:
    """家（m1）と、その手順。"""
    plan = MidGoalPlan(mission=MISSION, **kwargs)
    plan.add("自分の家を作る", (BUILT,))
    plan.add("夜に寝られるようにする", (BED,))
    if steps:
        plan.set_steps("m1", steps)
    return plan


def _checked(met: set[GoalSpec], lines: dict[GoalSpec, tuple[str, ...]] | None = None):
    async def check(specs):
        return [ConditionStatus(s, s in met, (lines or {}).get(s, ())) for s in specs]

    return check


@pytest.fixture
def events():
    return AsyncMock()


@pytest.fixture
def store():
    s = Mock()
    s.load.return_value = None
    return s


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.check.side_effect = _judged()
    return b


@pytest.fixture
def keeper(bridge, events, store):
    return MidGoalKeeper(bridge=bridge, event_publisher=events, store=store)


class TestJudgeLatchesSteps:
    @pytest.mark.asyncio
    async def test_a_step_met_once_stays_done_when_the_materials_are_used(
        self, keeper, bridge, store
    ):
        plan = _plan(PlannedStep(LOG, "原木"), PlannedStep(STONE, "石"))
        bridge.check.side_effect = _checked({LOG}, {STONE: ("cobblestone (3/32)",)})
        await keeper.judge(plan)
        first, second = plan.current.plan_steps
        assert (first.state, second.state) == (JobState.DONE, JobState.PENDING)
        assert second.progress == ("cobblestone (3/32)",)
        store.save.assert_called_with(plan)

        bridge.check.side_effect = _checked(set())  # 材料は使って、原木はもうない
        await keeper.judge(plan)
        assert plan.current.plan_steps[0].state == JobState.DONE
        assert [s.spec for s in plan.current.open_steps] == [STONE]

    @pytest.mark.asyncio
    async def test_a_bundle_step_is_done_only_when_every_member_holds(self, keeper, bridge):
        plan = _plan(PlannedStep(LOG, "森へ", together=(STONE,)))
        bridge.check.side_effect = _checked({LOG})
        await keeper.judge(plan)
        assert plan.current.plan_steps[0].state == JobState.PENDING
        bridge.check.side_effect = _checked({LOG, STONE})
        await keeper.judge(plan)
        assert plan.current.plan_steps[0].state == JobState.DONE

    @pytest.mark.asyncio
    async def test_a_check_that_cannot_be_made_keeps_the_steps(self, keeper, bridge):
        from ailoveshen.domain.exceptions import GameBridgeError

        plan = _plan(PlannedStep(LOG, "原木"))

        async def check(specs):
            if specs == [LOG]:
                raise GameBridgeError("down")
            return [ConditionStatus(s, False) for s in specs]

        bridge.check.side_effect = check
        await keeper.judge(plan)
        assert plan.current.plan_steps[0].state == JobState.PENDING


class TestAcceptAsJob:
    def _proposal(self, **extra):
        return parse_proposal(
            {
                "title": "明るく",
                "conditions": [{"predicate": "placed", "item": "torch"}],
                "level": "job",
                "reason": "暗いので",
                **extra,
            }
        )

    @pytest.mark.asyncio
    async def test_a_viewer_job_goes_right_behind_the_step_in_progress(self, keeper, store):
        plan = _plan(PlannedStep(LOG, "原木"), PlannedStep(STONE, "石"))
        goal = await keeper.accept(plan, self._proposal(), requested_by="neko")
        assert goal.id == "m1"  # 今の大目標
        steps = plan.current.open_steps
        assert [(s.spec, s.requested_by) for s in steps] == [
            (LOG, None),
            (TORCH, "neko"),
            (STONE, None),
        ]
        assert len(plan.pending) == 2  # 大目標は増えない
        store.save.assert_called_with(plan)

    @pytest.mark.asyncio
    async def test_now_puts_it_first(self, keeper):
        plan = _plan(PlannedStep(LOG, "原木"))
        await keeper.accept(plan, self._proposal(when="now"), requested_by="neko")
        assert plan.current.open_steps[0].spec == TORCH

    @pytest.mark.asyncio
    async def test_advice_becomes_her_own_job_beyond_the_viewer_limits(self, keeper):
        plan = _plan(PlannedStep(LOG, "原木"), max_viewer_goals=1)
        await keeper.accept(plan, self._proposal(), requested_by="neko")
        other = parse_proposal(
            {
                "title": "石",
                "conditions": [{"predicate": "have", "item": "cobblestone", "count": 32}],
                "level": "job",
            }
        )
        await keeper.accept(plan, other, requested_by="inu", advice=True)
        assert [(s.requested_by, s.suggested_by) for s in plan.current.open_steps[1:]] == [
            ("neko", None),
            (None, "inu"),
        ]

    @pytest.mark.asyncio
    async def test_the_limit_counts_a_viewers_objective_and_job_together(self, keeper):
        plan = _plan(PlannedStep(LOG, "原木"))
        plan.add("ベッド", (BED,), requested_by="neko")
        with pytest.raises(ValueError, match="already has a request"):
            await keeper.accept(plan, self._proposal(), requested_by="neko")
        assert [s.spec for s in plan.current.open_steps] == [LOG]

    @pytest.mark.asyncio
    async def test_the_condition_must_be_judged_before_it_is_promised(self, keeper, bridge):
        from ailoveshen.domain.exceptions import GoalRejectedError

        plan = _plan(PlannedStep(LOG, "原木"))
        bridge.check.side_effect = GoalRejectedError("nothing like that")
        with pytest.raises(GoalRejectedError):
            await keeper.accept(plan, self._proposal(), requested_by="neko")
        assert [s.spec for s in plan.current.open_steps] == [LOG]

    @pytest.mark.asyncio
    async def test_the_requester_withdraws_their_job(self, keeper, events):
        plan = _plan(PlannedStep(LOG, "原木"))
        await keeper.accept(plan, self._proposal(), requested_by="neko")
        goal = await keeper.withdraw(plan, "neko")
        assert goal.id == "m1"
        assert [s.spec for s in plan.current.open_steps] == [LOG]
        dropped = events.publish.call_args.args[0]
        assert isinstance(dropped, MidGoalDroppedEvent)
        assert (dropped.requested_by, "withdrew" in dropped.reason) == ("neko", True)
        assert await keeper.withdraw(plan, "neko") is None

    @pytest.mark.asyncio
    async def test_a_job_over_its_budget_is_dropped_and_said(self, keeper, events):
        plan = _plan(PlannedStep(LOG, "原木"), viewer_budget=2)
        await keeper.accept(plan, self._proposal(), requested_by="neko")
        assert plan.charge("m1", TORCH) is None
        assert plan.charge("m1", TORCH) is None
        await keeper.step_counted(plan, None)
        dropped = [
            c.args[0]
            for c in events.publish.call_args_list
            if isinstance(c.args[0], MidGoalDroppedEvent)
        ]
        assert [(d.requested_by, "over the budget" in d.reason) for d in dropped] == [
            ("neko", True)
        ]
        assert [s.spec for s in plan.current.open_steps] == [LOG]

    def test_a_job_needs_a_valid_bundle(self):
        with pytest.raises(ValueError, match="at most"):
            parse_proposal(
                {
                    "title": "たくさん",
                    "level": "job",
                    "conditions": [
                        {"predicate": "have", "item": f"item{i}", "count": 1} for i in range(5)
                    ],
                }
            )

    def test_an_objective_that_builds_cannot_hold_materials(self):
        with pytest.raises(ValueError, match="steps"):
            parse_proposal(
                {
                    "title": "保管庫",
                    "conditions": [
                        {"predicate": "built", "name": "storehouse"},
                        {"predicate": "have", "item": "cobblestone", "count": 64},
                    ],
                }
            )
        # 手順としてなら足せる
        parse_proposal(
            {
                "title": "保管庫と石",
                "level": "job",
                "conditions": [
                    {"predicate": "built", "name": "storehouse"},
                    {"predicate": "have", "item": "cobblestone", "count": 64},
                ],
            }
        )


class TestReplyAsJob:
    @pytest.mark.asyncio
    async def test_the_reply_may_add_a_step_of_the_mid_goal_worked_on_now(
        self, bridge, events, store
    ):
        plan = _plan(PlannedStep(LOG, "原木"))
        session = _session(plan=plan)
        session.set_goal(Goal(LOG, "原木", mid_goal_id="m1"), "day")
        generator = AsyncMock()
        generator.generate_json.return_value = {
            "reply": "丸太を先に集めるね",
            "request": "accept",
            "level": "job",
            "title": "石も",
            "conditions": [{"predicate": "have", "item": "cobblestone", "count": 32}],
            "reason": "家の土台",
        }
        builder = Mock()
        builder.build_system_prompt.return_value = "system"
        builder.build_chat_response_prompt.return_value = "prompt"
        use_case = GenerateResponseUseCase(
            text_generator=generator,
            prompt_builder=builder,
            event_publisher=events,
            conversation=Conversation(),
            character=CharacterProfile(),
            mid_goals=MidGoalKeeper(bridge=bridge, event_publisher=events, store=store),
        )

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="石も集めて", user_id="1", session=session)
        )

        assert response.mid_goal.id == "m1"
        assert [(s.spec, s.requested_by) for s in plan.current.open_steps] == [
            (LOG, None),
            (STONE, "neko"),
        ]
        assert len(plan.pending) == 2


class TestOrderCheckUsesTheLatch:
    @pytest.fixture
    def use_case(self, bridge, events, keeper):
        return AdvancePlayUseCase(
            bridge=bridge,
            text_generator=AsyncMock(),
            prompt_builder=Mock(),
            action_selector=AsyncMock(),
            event_publisher=events,
            conversation=Conversation(),
            mid_goals=keeper,
            town=AsyncMock(),
            notes=NoteKeeper(Mock(load=Mock(return_value=None))),
        )

    @pytest.mark.asyncio
    async def test_a_step_already_done_is_not_a_disorder_when_the_materials_are_gone(
        self, use_case, bridge
    ):
        plan = _plan(PlannedStep(LOG, "原木"), PlannedStep(STONE, "石"))
        plan.judge_steps("m1", [("j1", True, ())])
        bridge.check.side_effect = _checked({STONE})  # 原木は使ったので今は満たしていない
        rewritten = (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石"), PlannedStep(BUILT, "建てる"))
        # 順番が前後しても、済みにした手順は数えない（石は済み、原木の手順は済みのまま残る）
        await use_case._check_step_order(rewritten, plan.current)

    @pytest.mark.asyncio
    async def test_a_later_step_met_while_an_earlier_one_is_not_is_still_sent_back(
        self, use_case, bridge
    ):
        plan = _plan(PlannedStep(LOG, "原木"))
        bridge.check.side_effect = _checked({STONE})
        with pytest.raises(ValueError, match="already met now"):
            await use_case._check_step_order(
                (PlannedStep(LOG, "原木"), PlannedStep(STONE, "石")), plan.current
            )


class TestMidGoalProgress:
    def test_a_step_done_or_a_number_changed_is_progress(self):
        base = _MidWatch("m1", 0, (3,), "j1", (2,))
        assert not base.progressed_by(base)
        assert base.progressed_by(_MidWatch("m1", 1, (3,), "j2", ()))  # 手順が 1 つ済んだ
        assert base.progressed_by(_MidWatch("m1", 0, (4,), "j1", (2,)))  # 条件の数が動いた
        assert base.progressed_by(_MidWatch("m1", 0, (3,), "j1", (3,)))  # 今の手順の数が動いた
        assert base.progressed_by(_MidWatch("m2", 0, (), "", ()))  # 別の中目標

    def test_a_rewrite_that_only_changes_the_step_in_progress_is_not_progress(self):
        base = _MidWatch("m1", 0, (3,), "j1", (2,))
        assert not base.progressed_by(_MidWatch("m1", 0, (3,), "j5", (7,)))

    def _use_case(self, keeper, bridge, events, stall=4):
        return AdvancePlayUseCase(
            bridge=bridge,
            text_generator=AsyncMock(),
            prompt_builder=Mock(),
            action_selector=AsyncMock(),
            event_publisher=events,
            conversation=Conversation(),
            mid_goals=keeper,
            town=AsyncMock(),
            notes=NoteKeeper(Mock(load=Mock(return_value=None))),
            mid_goal_stall_steps=stall,
        )

    def test_the_stall_is_counted_per_step_and_a_rewrite_does_not_restart_it(
        self, keeper, bridge, events
    ):
        use_case = self._use_case(keeper, bridge, events)
        plan = _plan(PlannedStep(LOG, "原木"), PlannedStep(STONE, "石"))
        session = _session(plan=plan)
        assert use_case._mid_goal_stuck(session) == ""  # 見張りの始まり
        use_case._mid_idle_steps = 3
        assert use_case._mid_goal_stuck(session) == ""

        # 手順を書き直して今の手順が変わっただけでは、数え直さない
        plan.set_steps("m1", (PlannedStep(STONE, "石が先"), PlannedStep(LOG, "原木")))
        assert use_case._mid_goal_stuck(session) == ""
        assert use_case._mid_idle_steps == 3
        use_case._mid_idle_steps = 4
        assert "has made no progress in 4 actions" in use_case._mid_goal_stuck(session)

    def test_a_finished_step_restarts_the_count(self, keeper, bridge, events):
        use_case = self._use_case(keeper, bridge, events)
        plan = _plan(PlannedStep(LOG, "原木"), PlannedStep(STONE, "石"))
        session = _session(plan=plan)
        use_case._mid_goal_stuck(session)
        use_case._mid_idle_steps = 3
        plan.judge_steps("m1", [("j1", True, ())])
        assert use_case._mid_goal_stuck(session) == ""
        assert use_case._mid_idle_steps == 0

    def test_the_progress_of_the_step_in_progress_restarts_the_count(self, keeper, bridge, events):
        use_case = self._use_case(keeper, bridge, events)
        plan = _plan(PlannedStep(LOG, "原木"))
        session = _session(plan=plan)
        plan.judge_steps("m1", [("j1", False, ("have log (3/12)",))])
        use_case._mid_goal_stuck(session)
        use_case._mid_idle_steps = 3
        plan.judge_steps("m1", [("j1", False, ("have log (4/12)",))])
        use_case._mid_goal_stuck(session)
        assert use_case._mid_idle_steps == 0


class TestChooserUsesOpenSteps:
    @pytest.mark.asyncio
    async def test_a_done_step_is_never_offered_even_when_its_condition_broke(self, bridge):
        plan = _plan(PlannedStep(LOG, "原木"), PlannedStep(STONE, "石"), PlannedStep(BUILT, "建てる"))
        plan.judge_steps("m1", [("j1", True, ())])
        session = _session(plan=plan)
        bridge.check.side_effect = _checked(set())  # 原木は使ったので今は満たしていない
        jev = FakeJev("A")
        picked = await GoalChooser(jev, bridge).choose(session, _obs(), "the last goal is met")
        assert isinstance(picked, ChosenGoal) and picked.spec == STONE
        state, questions = jev.asked[0]
        assert state["steps"][0].endswith(": done")
        assert set(questions[0].criteria) == {"A", "B"}  # 石と建てる。原木は出ない

    @pytest.mark.asyncio
    async def test_a_step_found_met_is_kept_done(self, bridge):
        plan = _plan(PlannedStep(LOG, "原木"), PlannedStep(STONE, "石"))
        session = _session(plan=plan)
        bridge.check.side_effect = _checked({LOG})
        picked = await GoalChooser(FakeJev("A"), bridge).choose(session, _obs(), "met")
        assert picked.spec == STONE
        assert plan.current.plan_steps[0].state == JobState.DONE

    @pytest.mark.asyncio
    async def test_when_every_step_is_done_but_the_mid_goal_is_not(self, bridge):
        plan = _plan(PlannedStep(LOG, "原木"))
        plan.judge_steps("m1", [("j1", True, ())])
        bridge.check.side_effect = _checked(set())
        picked = await GoalChooser(FakeJev("A"), bridge).choose(_session(plan=plan), _obs(), "met")
        assert "all the steps are done" in picked.why


class TestDecisionRewrite:
    @pytest.mark.asyncio
    async def test_a_rewrite_that_would_overfill_the_steps_is_sent_back(
        self, bridge, keeper, events
    ):
        """助言の手順は残るので、書き直しがそれと合わせて上限を超えるなら差し戻す。"""
        prompt_builder = Mock()
        prompt_builder.build_goal_prompt.return_value = "goal prompt"
        prompt_builder.build_goal_system.return_value = "goal system"
        prompt_builder.build_action_context.return_value = ({"goal": "g"}, "instructions")
        selector = AsyncMock()
        selector.select.return_value = ActionDecision("dig oak_log at 1,70,2", 0.9)
        bridge.act.side_effect = lambda action_id: ActionResult(action_id, True, "ok", 1.0)
        text_generator = AsyncMock()
        plan = _plan()
        for i in range(3):
            plan.add_step(
                "m1",
                PlannedStep(
                    GoalSpec(GoalPredicate.HAVE, item=f"advice{i}", count=1), suggested_by=f"v{i}"
                ),
            )

        def decision(n: int) -> dict:
            return {
                **PLANKS,
                "review": "そのまま",
                "steps": [
                    {"predicate": "have", "item": f"thing{i}", "count": 2, "reason": "x"}
                    for i in range(n)
                ],
            }

        text_generator.generate_json.side_effect = [decision(6), decision(5)]
        use_case = AdvancePlayUseCase(
            bridge=bridge,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            action_selector=selector,
            event_publisher=events,
            conversation=Conversation(),
            mid_goals=keeper,
            town=AsyncMock(),
            notes=NoteKeeper(Mock(load=Mock(return_value=None))),
        )
        bridge.observe.return_value = _obs()
        bridge.set_goal.return_value = _obs().goal
        session = _session(plan=plan)

        await use_case.execute(session)

        assert text_generator.generate_json.await_count == 2
        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "at most 8" in error
        assert len(plan.current.open_steps) == 8
