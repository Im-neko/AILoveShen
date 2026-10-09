"""LLM の Composition Root のテスト。"""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.domain.entities import Conversation  # noqa: E402
from ailoveshen.factories.llm import (  # noqa: E402
    create_character_profile,
    create_llm_service,
)
from ailoveshen.infrastructure.config import (  # noqa: E402
    CharacterSettings,
    LiteLlmSettings,
    LlmSettings,
)
from ailoveshen.presentation.services.llm_service import LLMService  # noqa: E402


class TestCreateLLMService:
    """create_llm_service のテスト。"""

    @pytest.mark.asyncio
    async def test_creates_service(self):
        """正しい設定ならサービスが組み立てられる。"""
        service = create_llm_service(
            character=CharacterSettings(),
            event_publisher=AsyncMock(),
            conversation=Conversation(),
            llm=LlmSettings(litellm=LiteLlmSettings(model="haiku-5.5")),
        )

        assert isinstance(service, LLMService)
        await service.close()

    def test_missing_model_raises(self):
        """リモートのモデル名がなければすぐ失敗する。"""
        with pytest.raises(ValueError, match="LITELLM_MODEL"):
            create_llm_service(
                character=CharacterSettings(),
                event_publisher=AsyncMock(),
                conversation=Conversation(),
                llm=LlmSettings(),
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
