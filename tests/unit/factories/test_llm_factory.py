"""Tests for the LLM Composition Root."""

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("google.genai", reason="google-genai not installed")

from ailoveshen.factories.llm import (  # noqa: E402
    create_character_profile,
    create_llm_service,
)
from ailoveshen.infrastructure.config import CharacterSettings, GeminiSettings  # noqa: E402
from ailoveshen.presentation.services.llm_service import LLMService  # noqa: E402


class TestCreateLLMService:
    """Tests for create_llm_service."""

    @pytest.mark.asyncio
    async def test_creates_service(self):
        """Test the service is wired with a valid configuration."""
        service = create_llm_service(
            gemini=GeminiSettings(api_key="test-key"),
            character=CharacterSettings(),
            event_publisher=AsyncMock(),
        )

        assert isinstance(service, LLMService)
        await service.close()

    def test_missing_api_key_raises(self):
        """Test a missing API key fails fast."""
        with pytest.raises(ValueError, match="API key is required"):
            create_llm_service(
                gemini=GeminiSettings(api_key=""),
                character=CharacterSettings(),
                event_publisher=AsyncMock(),
            )

    def test_invalid_thinking_level_raises(self):
        """Test an unsupported thinking level fails fast."""
        with pytest.raises(ValueError, match="thinking_level"):
            create_llm_service(
                gemini=GeminiSettings(api_key="test-key", main_thinking_level="minimal"),
                character=CharacterSettings(),
                event_publisher=AsyncMock(),
            )


class TestCreateCharacterProfile:
    """Tests for create_character_profile."""

    def test_converts_settings(self):
        """Test settings lists become value object tuples."""
        profile = create_character_profile(
            CharacterSettings(name="シェン", sentence_endings=["のだ"], personality_traits=["元気"])
        )

        assert profile.name == "シェン"
        assert profile.sentence_endings == ("のだ",)
        assert profile.personality_traits == ("元気",)
