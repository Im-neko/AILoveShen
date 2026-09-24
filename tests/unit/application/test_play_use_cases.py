"""Tests for StartPlayUseCase and AdvancePlayUseCase."""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.play import (
    HOUSE_SCHEMA,
    AdvancePlayUseCase,
    StartPlayUseCase,
)
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
)
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    Candidate,
    CharacterProfile,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
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
PLANKS = {"predicate": "have", "item": "planks", "count": 4, "reason": "板材がない"}


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


def _session(goal: GoalSpec | None = None) -> PlaySession:
    session = PlaySession(blueprint=_blueprint(), max_stalled_steps=2)
    if goal is not None:
        session.set_goal(Goal(goal), "day")
    return session


HAVE_PLANKS = GoalSpec(GoalPredicate.HAVE, item="planks", count=4)


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
    return b


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

    def _use_case(self, text_generator, prompt_builder, bridge, events):
        return StartPlayUseCase(
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            bridge=bridge,
            event_publisher=events,
            character=CharacterProfile(),
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
        blocks, width, depth, height = bridge.set_build_plan.call_args.args
        assert blocks == project.blueprint.blocks()
        assert (width, depth, height) == (5, 6, 4)
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


class TestAdvancePlay:
    """Tests for AdvancePlayUseCase."""

    @pytest.fixture
    def use_case(self, bridge, text_generator, prompt_builder, selector, events):
        return AdvancePlayUseCase(
            bridge=bridge,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            action_selector=selector,
            event_publisher=events,
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
        """Test goals about the house and home are offered only when they make sense."""
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
            {"predicate": "have", "item": "unobtainium", "count": 1, "reason": ""},
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
            {"predicate": "have", "item": "planks", "reason": ""},
            {"predicate": "at_home", "reason": ""},
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
        text_generator.generate_json.return_value = {"predicate": "have", "reason": ""}

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
        self, use_case, text_generator, bridge, prompt_builder
    ):
        """Test a met goal ends; the LLM sees it in the recent goals with why it ended."""
        bridge.observe.return_value = _obs(met=True, remaining=0)
        text_generator.generate_json.return_value = {
            "predicate": "explored",
            "distance": 30,
            "reason": "",
        }
        session = _session(HAVE_PLANKS)

        report = await use_case.execute(session)

        assert report.goal_changed
        assert session.goal.spec.predicate == GoalPredicate.EXPLORED
        kwargs = prompt_builder.build_goal_prompt.call_args.kwargs
        assert kwargs["goal_ended_because"] == "goal have(planks, 4) is met"
        assert kwargs["recent_goals"][0].goal.spec == HAVE_PLANKS

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
