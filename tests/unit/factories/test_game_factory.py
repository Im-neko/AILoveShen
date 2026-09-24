"""Tests for the game agent Composition Root."""

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("google.genai", reason="google-genai not installed")
pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")

from ailoveshen.factories.game import create_game_service  # noqa: E402
from ailoveshen.infrastructure.config import (  # noqa: E402
    CharacterSettings,
    GeminiSettings,
    JevSettings,
    MinecraftSettings,
)
from ailoveshen.presentation.services.game_service import GameService  # noqa: E402


def _create(gemini_key="g", jev_key="j"):
    return create_game_service(
        gemini=GeminiSettings(api_key=gemini_key),
        jev=JevSettings(api_key=jev_key),
        minecraft=MinecraftSettings(),
        character=CharacterSettings(),
        event_publisher=AsyncMock(),
    )


class TestCreateGameService:
    """Tests for create_game_service."""

    @pytest.mark.asyncio
    async def test_creates_service(self):
        """Test the service is wired with valid settings."""
        service = _create()

        assert isinstance(service, GameService)
        await service.close()

    def test_missing_gemini_key_raises(self):
        """Test a missing Gemini key fails fast."""
        with pytest.raises(ValueError, match="Gemini API key"):
            _create(gemini_key="")

    def test_missing_jev_key_raises(self):
        """Test a missing TypeSafe key fails fast."""
        with pytest.raises(ValueError, match="TypeSafe API key"):
            _create(jev_key="")
