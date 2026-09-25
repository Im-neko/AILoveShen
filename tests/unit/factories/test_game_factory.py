"""Tests for the game agent Composition Root."""

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("google.genai", reason="google-genai not installed")
pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")

from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper  # noqa: E402
from ailoveshen.domain.entities import Conversation  # noqa: E402
from ailoveshen.domain.value_objects import GoalPredicate  # noqa: E402
from ailoveshen.factories.game import create_game_service, create_mid_goal_plan  # noqa: E402
from ailoveshen.infrastructure.config import (  # noqa: E402
    CharacterSettings,
    GeminiSettings,
    JevSettings,
    MinecraftSettings,
    MissionSettings,
)
from ailoveshen.presentation.services.game_service import GameService  # noqa: E402


def _create(gemini_key="g", jev_key="j"):
    return create_game_service(
        gemini=GeminiSettings(api_key=gemini_key),
        jev=JevSettings(api_key=jev_key),
        minecraft=MinecraftSettings(),
        character=CharacterSettings(),
        event_publisher=AsyncMock(),
        conversation=Conversation(),
    )


class TestCreateGameService:
    """Tests for create_game_service."""

    @pytest.mark.asyncio
    async def test_creates_service(self):
        """Test the service is wired with valid settings."""
        service = _create()

        assert isinstance(service, GameService)
        assert isinstance(service.mid_goals, MidGoalKeeper)
        await service.close()

    def test_missing_gemini_key_raises(self):
        """Test a missing Gemini key fails fast."""
        with pytest.raises(ValueError, match="Gemini API key"):
            _create(gemini_key="")

    def test_missing_jev_key_raises(self):
        """Test a missing TypeSafe key fails fast."""
        with pytest.raises(ValueError, match="TypeSafe API key"):
            _create(jev_key="")


class TestCreateMidGoalPlan:
    """Tests for the plan made from the mission settings."""

    def test_default_mission_and_mid_goals(self):
        """Test the configured mid goals are the list, in order, with the limits."""
        plan = create_mid_goal_plan(MissionSettings(viewer_budget_steps=50))

        assert plan.mission.text == "生き延びながら家を建て、街にしていく"
        assert [g.describe() for g in plan.pending] == [
            "自分の家を作る (built())",
            "夜に寝られるようにする (placed(bed, home))",
            "身を守る道具を持つ (have(wooden_sword, 1))",
            "食料を蓄える (have(food, 8))",
        ]
        assert plan.viewer_budget == 50

    def test_condition_not_judged_from_the_world_is_rejected(self):
        """Test a mid goal that the bridge could not judge fails at start."""
        settings = MissionSettings(
            mid_goals=[{"title": "探検", "conditions": [{"predicate": "explored", "distance": 30}]}]
        )
        with pytest.raises(ValueError, match="explored cannot be a condition"):
            create_mid_goal_plan(settings)

    def test_conditions_are_parsed(self):
        """Test placed means placed at home."""
        plan = create_mid_goal_plan(
            MissionSettings(
                mid_goals=[
                    {"title": "寝る", "conditions": [{"predicate": "placed", "item": "bed"}]}
                ]
            )
        )
        (condition,) = plan.current.conditions
        assert (condition.predicate, condition.where) == (GoalPredicate.PLACED, "home")
