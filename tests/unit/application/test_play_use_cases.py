"""Tests for StartPlayUseCase and AdvancePlayUseCase."""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.mission_store import SavedPlan
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.play import (
    HOUSE_SCHEMA,
    AdvancePlayUseCase,
    StartPlayUseCase,
)
from ailoveshen.domain.entities import Conversation, MidGoalPlan, PlaySession
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalEndedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
)
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    Candidate,
    CharacterProfile,
    ConditionStatus,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    MessageType,
    MidGoal,
    Mission,
    Side,
)

VALID_DESIGN = {
    "name": "ひだまり",
    "concept": "明るい小屋",
    "width": 5,
    "depth": 6,
    "wall_height": 3,
    "door_side": "south",
    "door_offset": 2,
    "corner_pillars": False,
}
PLANKS = {
    "predicate": "have",
    "item": "planks",
    "count": 4,
    "serves": "current",
    "reason": "板材がない",
}
MISSION = Mission("生き延びながら家を建て、街にしていく")


def _blueprint() -> HouseBlueprint:
    return HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2)


def _obs(
    candidates=("dig oak_log at 1,70,2", "wait"),
    remaining=3,
    met=False,
    goal=True,
    **kwargs,
) -> GameObservation:
    return GameObservation(
        state={"self": {"health": 20}},
        candidates=tuple(Candidate(c, {"verb": c.split()[0]}) for c in candidates),
        health=20.0,
        food=20,
        goal=GoalStatus(met=met, remaining=remaining) if goal else None,
        **kwargs,
    )


HAVE_PLANKS = GoalSpec(GoalPredicate.HAVE, item="planks", count=4)
BUILT = GoalSpec(GoalPredicate.BUILT)
BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
SWORD = GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1)


def _plan(**kwargs) -> MidGoalPlan:
    """The house (m1), then the bed (m2)."""
    plan = MidGoalPlan(mission=MISSION, **kwargs)
    plan.add("自分の家を作る", (BUILT,))
    plan.add("夜に寝られるようにする", (BED,))
    return plan


def _session(goal: GoalSpec | None = None, plan: MidGoalPlan | None = None) -> PlaySession:
    session = PlaySession(blueprint=_blueprint(), plan=plan or _plan(), max_stalled_steps=2)
    if goal is not None:
        session.set_goal(Goal(goal, mid_goal_id="m1"), "day")
    return session


def _judged(met: set[GoalSpec] = frozenset()):
    """bridge.check: the conditions in `met` hold, the others do not."""

    async def check(specs):
        return [ConditionStatus(s, s in met, (f"{s.describe()}: {s in met}",)) for s in specs]

    return check


@pytest.fixture
def text_generator():
    """Mock LLM with structured output."""
    return AsyncMock()


@pytest.fixture
def prompt_builder():
    """Mock game prompt builder (sync methods)."""
    builder = Mock()
    builder.build_house_design_prompt.return_value = "design prompt"
    builder.build_goal_prompt.return_value = "goal prompt"
    builder.build_action_context.return_value = ({"goal": "g"}, "instructions")
    return builder


@pytest.fixture
def bridge():
    """Mock Minecraft bridge."""
    b = AsyncMock()
    b.observe.return_value = _obs()
    b.set_goal.return_value = GoalStatus(met=False, remaining=3)
    b.act.side_effect = lambda action_id: ActionResult(action_id, True, "ok", 1.0)
    b.check.side_effect = _judged()
    return b


@pytest.fixture
def store():
    """Mock mission store (nothing saved yet)."""
    s = Mock()
    s.load.return_value = None
    return s


@pytest.fixture
def mid_goals(bridge, events, store):
    """The real keeper over the mocks."""
    return MidGoalKeeper(bridge=bridge, event_publisher=events, store=store)


@pytest.fixture
def selector():
    """Mock action selector."""
    s = AsyncMock()
    s.select.return_value = ActionDecision("dig oak_log at 1,70,2", 0.9)
    return s


@pytest.fixture
def events():
    """Mock event publisher."""
    return AsyncMock()


def _published(events, event_type):
    return [c.args[0] for c in events.publish.call_args_list if isinstance(c.args[0], event_type)]


class TestStartPlay:
    """Tests for StartPlayUseCase."""

    def _use_case(self, text_generator, prompt_builder, bridge, events, store=None, plan=None):
        if store is None:
            store = Mock()
            store.load.return_value = None
        return StartPlayUseCase(
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            bridge=bridge,
            event_publisher=events,
            character=CharacterProfile(),
            plan=plan or _plan(),
            store=store,
            max_steps_per_goal=7,
            max_stalled_steps=5,
        )

    @pytest.mark.asyncio
    async def test_design_is_sent_to_bridge(self, text_generator, prompt_builder, bridge, events):
        """Test a valid design starts the session and its plan is sent."""
        text_generator.generate_json.return_value = VALID_DESIGN

        project = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert project.blueprint.name == "ひだまり"
        assert project.blueprint.door_side == Side.SOUTH
        assert project.max_steps_per_goal == 7
        assert project.max_stalled_steps == 5
        assert text_generator.generate_json.call_args.args[1] is HOUSE_SCHEMA
        bridge.set_build_plan.assert_awaited_once_with(project.blueprint)
        assert _published(events, HouseDesignedEvent)[0].name == "ひだまり"

    @pytest.mark.asyncio
    async def test_invalid_design_is_retried_with_error(
        self, text_generator, prompt_builder, bridge, events
    ):
        """Test a rejected design is sent back with the validation error."""
        text_generator.generate_json.side_effect = [{**VALID_DESIGN, "width": 9}, VALID_DESIGN]

        project = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert project.blueprint.width == 5
        second = prompt_builder.build_house_design_prompt.call_args_list[1]
        assert "width" in second.kwargs["previous_error"]

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(
        self, text_generator, prompt_builder, bridge, events
    ):
        """Test repeated invalid designs raise TextGenerationError without a plan."""
        text_generator.generate_json.return_value = {"name": "x"}

        with pytest.raises(TextGenerationError, match="no valid house design"):
            await self._use_case(text_generator, prompt_builder, bridge, events).execute()
        bridge.set_build_plan.assert_not_called()

    @pytest.mark.asyncio
    async def test_built_home_is_used_without_designing(
        self, text_generator, prompt_builder, bridge, events
    ):
        """Test a home built in an earlier run is kept: nothing designed, no plan sent."""
        bridge.observe.return_value = _obs(has_plan=True, house_complete=True, has_home=True)

        session = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert session.blueprint is None  # its design was not kept (built before designs were)
        assert session.completion_announced
        text_generator.generate_json.assert_not_called()
        bridge.set_build_plan.assert_not_called()
        assert _published(events, HouseDesignedEvent) == []

    @pytest.mark.asyncio
    async def test_built_home_keeps_its_design(
        self, text_generator, prompt_builder, bridge, events
    ):
        """Test the home's design kept by the bridge comes back (its name is not lost)."""
        home = _obs(has_plan=True, house_complete=True, has_home=True)
        home.state["home"] = {"name": "ひだまり", "design": VALID_DESIGN}
        bridge.observe.return_value = home

        session = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert session.blueprint.name == "ひだまり"
        assert session.blueprint.door_side == Side.SOUTH
        text_generator.generate_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_plan_comes_from_the_configuration_and_is_saved(
        self, text_generator, prompt_builder, bridge, events, store
    ):
        """Test with nothing saved, the configured mid goals start (and are saved)."""
        text_generator.generate_json.return_value = VALID_DESIGN
        plan = _plan()

        session = await self._use_case(
            text_generator, prompt_builder, bridge, events, store, plan
        ).execute()

        assert session.plan is plan
        assert [g.id for g in session.plan.pending] == ["m1", "m2"]
        store.save.assert_called_once_with(plan)

    @pytest.mark.asyncio
    async def test_saved_plan_carries_on_for_the_same_mission(
        self, text_generator, prompt_builder, bridge, events, store
    ):
        """Test the mid goals carry on across restarts."""
        text_generator.generate_json.return_value = VALID_DESIGN
        store.load.return_value = SavedPlan(
            mission=MISSION,
            pending=(MidGoal("m7", "剣", (SWORD,), requested_by="neko", steps=5),),
            finished=(),
            next_id=8,
        )

        session = await self._use_case(
            text_generator, prompt_builder, bridge, events, store
        ).execute()

        assert [(g.id, g.requested_by, g.steps) for g in session.plan.pending] == [
            ("m7", "neko", 5)
        ]
        assert session.plan.next_id == 8

    @pytest.mark.asyncio
    async def test_saved_plan_for_another_mission_is_not_used(
        self, text_generator, prompt_builder, bridge, events, store
    ):
        """Test a changed mission starts the configured mid goals anew."""
        text_generator.generate_json.return_value = VALID_DESIGN
        store.load.return_value = SavedPlan(
            mission=Mission("村を作る"),
            pending=(MidGoal("m7", "剣", (SWORD,)),),
            finished=(),
            next_id=8,
        )

        session = await self._use_case(
            text_generator, prompt_builder, bridge, events, store
        ).execute()

        assert [g.id for g in session.plan.pending] == ["m1", "m2"]
        store.save.assert_called_once()


class TestAdvancePlay:
    """Tests for AdvancePlayUseCase."""

    @pytest.fixture
    def conversation(self):
        return Conversation()

    @pytest.fixture
    def use_case(
        self, bridge, text_generator, prompt_builder, selector, events, conversation, mid_goals
    ):
        return AdvancePlayUseCase(
            bridge=bridge,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            action_selector=selector,
            event_publisher=events,
            conversation=conversation,
            mid_goals=mid_goals,
        )

    @pytest.mark.asyncio
    async def test_first_step_sets_goal_then_acts(
        self, use_case, text_generator, bridge, selector, prompt_builder, events
    ):
        """Test the LLM sets a goal, the bridge takes it, the selector picks, the bridge runs."""
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        report = await use_case.execute(session)

        assert report.goal_changed
        assert session.goal.spec == HAVE_PLANKS
        bridge.set_goal.assert_awaited_once_with(HAVE_PLANKS)
        assert bridge.observe.await_count == 2  # again after the goal: candidates for it
        state, candidates, instructions = selector.select.call_args.args
        assert state == {"goal": "g"} and instructions == "instructions"
        assert [c.action_id for c in candidates] == ["dig oak_log at 1,70,2", "wait"]
        prompt_builder.build_action_context.assert_called_once()
        bridge.act.assert_awaited_once_with("dig oak_log at 1,70,2")
        assert session.steps_in_goal == 1
        assert _published(events, GoalSetEvent)[0].goal == "have(planks, 4)"
        assert _published(events, GameActionExecutedEvent)[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_predicates_offered_follow_the_house(self, use_case, text_generator, bridge):
        """Test goals about the house, home and chests are offered only when they make sense."""
        text_generator.generate_json.return_value = PLANKS

        await use_case.execute(_session())
        schema = text_generator.generate_json.call_args.args[1]
        assert schema["properties"]["predicate"]["enum"] == ["have", "explored"]

        bridge.observe.return_value = _obs(goal=False, has_plan=True)
        await use_case.execute(_session())
        schema = text_generator.generate_json.call_args.args[1]
        assert schema["properties"]["predicate"]["enum"] == ["built", "have", "explored"]

        bridge.observe.return_value = _obs(
            goal=False, has_plan=True, house_complete=True, has_home=True
        )
        await use_case.execute(_session())
        schema = text_generator.generate_json.call_args.args[1]
        assert schema["properties"]["predicate"]["enum"] == [
            "have",
            "at_home",
            "through_night",
            "cleared",
            "placed",
            "stored",
            "explored",
        ]

        # Clearing the door is a daytime goal: at night more keep spawning
        bridge.observe.return_value = _obs(
            goal=False, has_plan=True, house_complete=True, has_home=True, time_phase="night"
        )
        await use_case.execute(_session())
        schema = text_generator.generate_json.call_args.args[1]
        assert "cleared" not in schema["properties"]["predicate"]["enum"]

    @pytest.mark.asyncio
    async def test_goal_rejected_by_bridge_is_retried_with_reason(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """Test the bridge's rejection goes back to the LLM."""
        text_generator.generate_json.side_effect = [
            {**PLANKS, "item": "unobtainium", "count": 1},
            PLANKS,
        ]
        bridge.set_goal.side_effect = [
            GoalRejectedError("unknown item: unobtainium"),
            GoalStatus(False, 3),
        ]
        session = _session()

        await use_case.execute(session)

        assert session.goal.spec == HAVE_PLANKS
        second = prompt_builder.build_goal_prompt.call_args_list[1]
        assert "unobtainium" in second.kwargs["previous_error"]

    @pytest.mark.asyncio
    async def test_malformed_or_unoffered_goal_is_retried(
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """Test a goal missing arguments, or not offered, is sent back."""
        text_generator.generate_json.side_effect = [
            {"predicate": "have", "item": "planks", "serves": "current", "reason": ""},
            {"predicate": "at_home", "serves": "survival", "reason": ""},
            PLANKS,
        ]

        session = _session()
        await use_case.execute(session)

        errors = [
            c.kwargs["previous_error"] for c in prompt_builder.build_goal_prompt.call_args_list
        ]
        assert "count" in errors[1]
        assert "not one of the goals offered" in errors[2]
        bridge.set_goal.assert_awaited_once_with(HAVE_PLANKS)

    @pytest.mark.asyncio
    async def test_gives_up_after_max_goal_attempts(self, use_case, text_generator, bridge):
        """Test repeated rejections raise TextGenerationError without acting."""
        text_generator.generate_json.return_value = {
            "predicate": "have",
            "serves": "current",
            "reason": "",
        }

        with pytest.raises(TextGenerationError, match="no acceptable goal"):
            await use_case.execute(_session())
        bridge.act.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_candidate_skips_selector(self, use_case, selector, bridge):
        """Test a lone candidate is taken without a model call."""
        bridge.observe.return_value = _obs(("wait inside",))

        report = await use_case.execute(_session(HAVE_PLANKS))

        selector.select.assert_not_called()
        assert report.decision == ActionDecision("wait inside", 1.0)
        assert not report.goal_changed

    @pytest.mark.asyncio
    async def test_met_goal_is_replaced_and_remembered(
        self, use_case, text_generator, bridge, prompt_builder, events
    ):
        """Test a met goal ends (announced, remembered); the LLM sees it with why it ended."""
        bridge.observe.return_value = _obs(met=True, remaining=0)
        text_generator.generate_json.return_value = {
            "predicate": "explored",
            "distance": 30,
            "serves": "current",
            "reason": "",
        }
        session = _session(HAVE_PLANKS)

        report = await use_case.execute(session)

        assert report.goal_changed
        assert session.goal.spec.predicate == GoalPredicate.EXPLORED
        kwargs = prompt_builder.build_goal_prompt.call_args.kwargs
        assert kwargs["goal_ended_because"] == "goal have(planks, 4) is met"
        assert kwargs["activity"].goal.spec == HAVE_PLANKS
        assert session.recent_goals[0].goal.spec == HAVE_PLANKS
        assert session.recent_goals[0].met
        ended = _published(events, GoalEndedEvent)[0]
        assert (ended.goal, ended.met) == ("have(planks, 4)", True)

    @pytest.mark.asyncio
    async def test_stalled_goal_is_replaced(self, use_case, text_generator, bridge, prompt_builder):
        """Test a goal whose remaining work stops going down ends as stalled."""
        text_generator.generate_json.return_value = PLANKS
        session = _session(HAVE_PLANKS)  # max_stalled_steps=2; remaining stays 3

        assert not (await use_case.execute(session)).goal_changed  # first reading of the work
        assert not (await use_case.execute(session)).goal_changed  # no progress: 1
        report = await use_case.execute(session)  # no progress: 2

        assert report.goal_changed
        reason = prompt_builder.build_goal_prompt.call_args.kwargs["goal_ended_because"]
        assert reason == "goal have(planks, 4) stalled (no progress in 2 steps)"

    @pytest.mark.asyncio
    async def test_completion_is_announced_once_and_play_goes_on(self, use_case, bridge, events):
        """Test completion publishes one event and the session keeps taking steps."""
        bridge.observe.return_value = _obs(has_plan=True, house_complete=True)
        session = _session(HAVE_PLANKS)

        first = await use_case.execute(session)
        await use_case.execute(session)

        assert first.house_complete
        assert len(_published(events, HouseCompletedEvent)) == 1
        assert bridge.act.await_count == 2

    @pytest.mark.asyncio
    async def test_busy_bridge_skips_the_step(self, use_case, text_generator, bridge):
        """Test nothing is decided or run while the bridge's reflex is busy."""
        bridge.observe.return_value = replace(_obs(), busy=True)

        report = await use_case.execute(_session())

        assert report.waiting
        text_generator.generate_json.assert_not_called()
        bridge.act.assert_not_called()

    @pytest.mark.asyncio
    async def test_goal_decision_sees_what_was_said(
        self, use_case, text_generator, prompt_builder, conversation
    ):
        """Test the goal decision gets the conversation (what the streamer said and was asked)."""
        conversation.add_viewer_message("家まだ？", "neko")
        conversation.add_streamer_message("もうすぐ完成するよ", MessageType.RESPONSE)
        text_generator.generate_json.return_value = PLANKS

        await use_case.execute(_session())

        messages = prompt_builder.build_goal_prompt.call_args.kwargs["recent_messages"]
        assert [m.content for m in messages] == ["家まだ？", "もうすぐ完成するよ"]

    @pytest.mark.asyncio
    async def test_small_goal_serves_the_top_mid_goal(self, use_case, text_generator, events):
        """Test the goal is set for the mid goal worked on now, and says so."""
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        await use_case.execute(session)

        assert session.goal.mid_goal_id == "m1"
        assert _published(events, GoalSetEvent)[0].mid_goal == "自分の家を作る"

    @pytest.mark.asyncio
    async def test_new_goal_is_seen_with_its_own_status(self, use_case, text_generator, bridge):
        """Test what the goal-set narration sees is the new goal's status, not the ended one's."""
        bridge.set_goal.return_value = GoalStatus(False, 5, lines=("have 4 planks (0/4)",))
        seen = []

        async def publish(event):
            if isinstance(event, GoalSetEvent):
                seen.append(session.activity().observation.goal.lines)

        use_case._event_publisher.publish.side_effect = publish
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        await use_case.execute(session)

        assert seen == [("have 4 planks (0/4)",)]

    @pytest.mark.asyncio
    async def test_mid_goals_are_judged_before_deciding(
        self, use_case, text_generator, bridge, prompt_builder, events, store
    ):
        """Test a mid goal already done (the house from an earlier run) is completed first."""
        bridge.check.side_effect = _judged({BUILT})
        text_generator.generate_json.return_value = PLANKS
        session = _session()

        await use_case.execute(session)

        assert [g.id for g in session.plan.pending] == ["m2"]
        assert _published(events, MidGoalCompletedEvent)[0].title == "自分の家を作る"
        activity = prompt_builder.build_goal_prompt.call_args.kwargs["activity"]
        assert [g.id for g in activity.mid_goals if g.state.value == "pending"] == ["m2"]
        assert session.goal.mid_goal_id == "m2"
        assert session.plan.get("m2").progress == ("placed(bed, home): False",)
        store.save.assert_called()

    @pytest.mark.asyncio
    async def test_survival_goal_serves_no_mid_goal(self, use_case, text_generator, events):
        """Test a survival goal is set for no mid goal."""
        text_generator.generate_json.return_value = {
            "predicate": "have",
            "item": "food",
            "count": 4,
            "serves": "survival",
            "reason": "お腹が空いた",
        }
        session = _session()

        await use_case.execute(session)

        assert session.goal.mid_goal_id is None
        assert _published(events, GoalSetEvent)[0].mid_goal == ""

    @pytest.mark.asyncio
    async def test_only_survival_goals_may_skip_the_mid_goals(
        self, use_case, text_generator, prompt_builder
    ):
        """Test a goal that is not about staying alive must serve the top mid goal."""
        text_generator.generate_json.side_effect = [{**PLANKS, "serves": "survival"}, PLANKS]
        session = _session()

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "not a survival goal" in error
        assert session.goal.mid_goal_id == "m1"

    @pytest.mark.asyncio
    async def test_no_mid_goal_left_needs_one_added(self, use_case, text_generator, prompt_builder):
        """Test with the list empty, a goal must add a mid goal or be for survival."""
        text_generator.generate_json.side_effect = [
            PLANKS,
            {
                **PLANKS,
                "plan_changes": [
                    {
                        "op": "add",
                        "title": "剣を持つ",
                        "conditions": [{"predicate": "have", "item": "wooden_sword", "count": 1}],
                        "reason": "身を守る",
                    }
                ],
            },
        ]
        session = _session(plan=MidGoalPlan(mission=MISSION))

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "no mid goal left" in error
        assert session.plan.current.title == "剣を持つ"
        assert session.goal.mid_goal_id == session.plan.current.id

    @pytest.mark.asyncio
    async def test_plan_changes_are_applied_with_the_goal(
        self, use_case, text_generator, prompt_builder, events, store
    ):
        """Test the streamer's own edits: added (told), moved, then the goal serves the top."""
        text_generator.generate_json.return_value = {
            **PLANKS,
            "plan_changes": [
                {
                    "op": "add",
                    "title": "剣を持つ",
                    "conditions": [{"predicate": "have", "item": "wooden_sword", "count": 1}],
                    "position": 3,
                    "reason": "夜に備える",
                },
                {"op": "move", "id": "m2", "position": 1, "reason": "夜が近い"},
            ],
        }
        session = _session()

        await use_case.execute(session)

        assert [g.title for g in session.plan.pending] == [
            "夜に寝られるようにする",
            "自分の家を作る",
            "剣を持つ",
        ]
        assert session.goal.mid_goal_id == "m2"
        added = _published(events, MidGoalAddedEvent)[0]
        assert (added.title, added.position, added.requested_by) == ("剣を持つ", 3, "")
        schema = text_generator.generate_json.call_args.args[1]
        change = schema["properties"]["plan_changes"]["items"]
        assert change["properties"]["id"]["enum"] == ["m1", "m2"]
        store.save.assert_called_with(session.plan)

    @pytest.mark.asyncio
    async def test_unusable_edit_is_retried_and_nothing_is_applied(
        self, use_case, text_generator, prompt_builder, bridge
    ):
        """Test an edit breaking the rules goes back with the reason; the plan is untouched."""
        text_generator.generate_json.side_effect = [
            {
                **PLANKS,
                "plan_changes": [
                    {"op": "move", "id": "m2", "position": 1, "reason": "先に"},
                    {"op": "drop", "id": "m1", "reason": ""},
                ],
            },
            PLANKS,
        ]
        session = _session()

        await use_case.execute(session)

        error = prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"]
        assert "needs a reason" in error
        assert [g.id for g in session.plan.pending] == ["m1", "m2"]

    @pytest.mark.asyncio
    async def test_new_mid_goal_the_bridge_cannot_judge_is_retried(
        self, use_case, text_generator, prompt_builder, bridge
    ):
        """Test an added mid goal's conditions are checked by the bridge first."""
        judged = _judged()

        async def check(specs):
            if any(s.item == "diamond" for s in specs):
                raise GoalRejectedError("conditions rejected: unknown item diamond")
            return await judged(specs)

        bridge.check.side_effect = check
        add_diamond = {
            "op": "add",
            "title": "ダイヤ",
            "conditions": [{"predicate": "have", "item": "diamond", "count": 1}],
            "reason": "欲しい",
        }
        text_generator.generate_json.side_effect = [
            {**PLANKS, "plan_changes": [add_diamond]},
            PLANKS,
        ]
        session = _session()

        await use_case.execute(session)

        assert (
            "unknown item diamond"
            in (prompt_builder.build_goal_prompt.call_args.kwargs["previous_error"])
        )
        assert len(session.plan.pending) == 2

    @pytest.mark.asyncio
    async def test_request_accepted_during_a_goal_decision_is_kept(self, use_case, text_generator):
        """Test a viewer's mid goal added while the LLM decides is not overwritten by its edits."""
        session = _session()

        async def decide(*args, **kwargs):
            # The chat reply accepts a request meanwhile
            session.plan.add("剣", (SWORD,), requested_by="neko", position=1)
            return {
                **PLANKS,
                "plan_changes": [
                    {
                        "op": "add",
                        "title": "食料",
                        "conditions": [{"predicate": "have", "item": "food", "count": 8}],
                        "reason": "空腹",
                    }
                ],
            }

        text_generator.generate_json.side_effect = decide

        await use_case.execute(session)

        assert [g.title for g in session.plan.pending] == [
            "自分の家を作る",
            "剣",
            "夜に寝られるようにする",
            "食料",
        ]

    @pytest.mark.asyncio
    async def test_viewer_mid_goal_over_budget_is_dropped_and_told(
        self, use_case, text_generator, bridge, events, store
    ):
        """Test a request past its step budget is dropped (told) and its goal ends next step."""
        plan = MidGoalPlan(mission=MISSION, viewer_budget=1)
        request = plan.add("剣", (SWORD,), requested_by="neko")
        session = PlaySession(blueprint=_blueprint(), plan=plan)
        session.set_goal(Goal(HAVE_PLANKS, mid_goal_id=request.id), "day")

        await use_case.execute(session)

        dropped = _published(events, MidGoalDroppedEvent)[0]
        assert (dropped.title, dropped.requested_by) == ("剣", "neko")
        assert "over the budget" in dropped.reason
        assert "served has ended" in session.goal_end_reason(bridge.observe.return_value)
        store.save.assert_called_with(plan)
