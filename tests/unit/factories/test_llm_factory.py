"""LLM の Composition Root のテスト。"""

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("google.genai", reason="google-genai not installed")

from ailoveshen.domain.entities import Conversation  # noqa: E402
from ailoveshen.factories.llm import (  # noqa: E402
    create_character_profile,
    create_llm_service,
)
from ailoveshen.infrastructure.config import CharacterSettings, GeminiSettings  # noqa: E402
from ailoveshen.presentation.services.llm_service import LLMService  # noqa: E402


class TestCreateLLMService:
    """create_llm_service のテスト。"""

    @pytest.mark.asyncio
    async def test_creates_service(self):
        """正しい設定ならサービスが組み立てられる。"""
        service = create_llm_service(
            gemini=GeminiSettings(api_key="test-key"),
            character=CharacterSettings(),
            event_publisher=AsyncMock(),
            conversation=Conversation(),
        )

        assert isinstance(service, LLMService)
        await service.close()

    def test_missing_api_key_raises(self):
        """API キーがなければすぐ失敗する。"""
        with pytest.raises(ValueError, match="API key is required"):
            create_llm_service(
                gemini=GeminiSettings(api_key=""),
                character=CharacterSettings(),
                event_publisher=AsyncMock(),
                conversation=Conversation(),
            )

    def test_invalid_thinking_level_raises(self):
        """対応していない thinking level ならすぐ失敗する。"""
        with pytest.raises(ValueError, match="thinking_level"):
            create_llm_service(
                gemini=GeminiSettings(api_key="test-key", main_thinking_level="minimal"),
                character=CharacterSettings(),
                event_publisher=AsyncMock(),
                conversation=Conversation(),
            )


class TestCreateCharacterProfile:
    """create_character_profile のテスト。"""

    def test_converts_settings(self):
        """設定のリストは値オブジェクトのタプルになる。"""
        profile = create_character_profile(
            CharacterSettings(name="シェン", sentence_endings=["のだ"], personality_traits=["元気"])
        )

        assert profile.name == "シェン"
        assert profile.sentence_endings == ("のだ",)
        assert profile.personality_traits == ("元気",)
