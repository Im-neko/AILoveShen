"""Tests for StartHouseProjectUseCase and AdvanceHouseProjectUseCase."""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.build_house import (
    HOUSE_SCHEMA,
    AdvanceHouseProjectUseCase,
    StartHouseProjectUseCase,
)
from ailoveshen.domain.entities import HouseProject
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
)
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    AvailableAction,
    BuildStatus,
    CharacterProfile,
    GameObservation,
    Goal,
    GoalType,
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
    "windows": [{"side": "east", "offset": 2}],
    "corner_pillars": False,
}


def _blueprint() -> HouseBlueprint:
    return HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2)


def _obs(actions=("collect_log", "pickup_drop", "explore"), complete=False) -> GameObservation:
    # Not complete: no build status yet, so the whole blueprint is still needed
    build = BuildStatus(total=72, placed=72, complete=True, site_chosen=True) if complete else None
    return GameObservation(
        state={"self": {"health": 20}},
        inventory={},
        actions=tuple(AvailableAction(a, f"do {a}") for a in actions),
        health=20.0,
        food=20,
        build=build,
    )


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
    builder.build_action_instructions.return_value = "instructions"
    return builder


@pytest.fixture
def bridge():
    """Mock Minecraft bridge."""
    b = AsyncMock()
    b.observe.return_value = _obs()
    b.act.side_effect = lambda action_id: ActionResult(action_id, True, "ok", 1.0)
    return b


@pytest.fixture
def selector():
    """Mock action selector."""
    s = AsyncMock()
    s.select.return_value = ActionDecision("collect_log", 0.9, {"collect_log": 0.9})
    return s


@pytest.fixture
def events():
    """Mock event publisher."""
    return AsyncMock()


def _published(events, event_type):
    return [c.args[0] for c in events.publish.call_args_list if isinstance(c.args[0], event_type)]


class TestStartHouseProject:
    """Tests for StartHouseProjectUseCase."""

    def _use_case(self, text_generator, prompt_builder, bridge, events):
        return StartHouseProjectUseCase(
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            bridge=bridge,
            event_publisher=events,
            character=CharacterProfile(),
            max_steps_per_goal=7,
        )

    @pytest.mark.asyncio
    async def test_design_is_sent_to_bridge(self, text_generator, prompt_builder, bridge, events):
        """Test a valid design becomes the project and its plan is sent."""
        text_generator.generate_json.return_value = VALID_DESIGN

        project = await self._use_case(text_generator, prompt_builder, bridge, events).execute()

        assert project.blueprint.name == "ひだまり"
        assert project.blueprint.door_side == Side.SOUTH
        assert project.max_steps_per_goal == 7
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


class TestAdvanceHouseProject:
    """Tests for AdvanceHouseProjectUseCase."""

    @pytest.fixture
    def use_case(self, bridge, text_generator, prompt_builder, selector, events):
        return AdvanceHouseProjectUseCase(
            bridge=bridge,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            action_selector=selector,
            event_publisher=events,
        )

    @pytest.mark.asyncio
    async def test_first_step_decides_goal_then_acts(
        self, use_case, text_generator, bridge, events
    ):
        """Test the LLM picks a goal, the selector an action, and the bridge runs it."""
        text_generator.generate_json.return_value = {"goal": "gather_wood", "reason": "木がない"}
        project = HouseProject(blueprint=_blueprint())

        report = await use_case.execute(project)

        assert report.goal_changed
        assert project.goal.goal_type == GoalType.GATHER_WOOD
        assert report.decision.action_id == "collect_log"
        bridge.act.assert_awaited_once_with("collect_log")
        assert project.steps_in_goal == 1
        assert _published(events, GoalSetEvent)[0].goal_type == "gather_wood"
        assert _published(events, GameActionExecutedEvent)[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_goal_choices_are_goals_with_executable_actions(self, use_case, text_generator):
        """Test only goals with an available goal action are offered to the LLM."""
        text_generator.generate_json.return_value = {"goal": "gather_wood", "reason": ""}

        await use_case.execute(HouseProject(blueprint=_blueprint()))

        schema = text_generator.generate_json.call_args.args[1]
        # collect_log/pickup_drop -> gather_wood, explore -> explore; nothing to craft or build
        assert schema["properties"]["goal"]["enum"] == ["gather_wood", "explore"]

    @pytest.mark.asyncio
    async def test_unavailable_goal_raises(self, use_case, text_generator):
        """Test a goal outside the offered set is rejected."""
        text_generator.generate_json.return_value = {"goal": "craft", "reason": ""}

        with pytest.raises(TextGenerationError, match="no executable action"):
            await use_case.execute(HouseProject(blueprint=_blueprint()))

    @pytest.mark.asyncio
    async def test_candidates_filtered_by_goal(self, use_case, selector, bridge):
        """Test the selector only sees actions the goal allows."""
        bridge.observe.return_value = _obs(("collect_log", "explore", "idle", "flee_hostile"))
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.GATHER_WOOD))

        report = await use_case.execute(project)

        candidates = [a.action_id for a in selector.select.call_args.args[1]]
        assert candidates == ["collect_log", "flee_hostile"]
        assert not report.goal_changed

    @pytest.mark.asyncio
    async def test_single_candidate_skips_selector(self, use_case, selector, bridge):
        """Test a lone candidate is taken without a model call."""
        bridge.observe.return_value = _obs(("build_step", "idle"))
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.BUILD_SHELTER))

        report = await use_case.execute(project)

        selector.select.assert_not_called()
        assert report.decision == ActionDecision("build_step", 1.0)
        bridge.act.assert_awaited_once_with("build_step")

    @pytest.mark.asyncio
    async def test_no_candidate_blocks_goal(self, use_case, bridge, selector):
        """Test a goal with nothing executable is marked stuck without acting."""
        bridge.observe.return_value = _obs(("explore", "idle"))
        project = HouseProject(blueprint=_blueprint())
        project.set_goal(Goal(GoalType.CRAFT))

        report = await use_case.execute(project)

        assert report.result is None
        bridge.act.assert_not_called()
        assert project.needs_new_goal(_obs())

    @pytest.mark.asyncio
    async def test_complete_house_stops(self, use_case, bridge, events):
        """Test a complete build reports completion and publishes the event."""
        bridge.observe.return_value = _obs(complete=True)

        report = await use_case.execute(HouseProject(blueprint=_blueprint()))

        assert report.complete
        bridge.act.assert_not_called()
        assert _published(events, HouseCompletedEvent)[0].name == "小屋"
