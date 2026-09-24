"""Tests for GenerateCommentaryUseCase."""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateCommentaryRequest
from ailoveshen.application.use_cases.generate_commentary import GenerateCommentaryUseCase
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import CommentaryGeneratedEvent
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    EmotionState,
    EmotionType,
    MessageType,
)


@pytest.fixture
def mock_text_generator():
    """Create mock text generator."""
    generator = AsyncMock()
    generator.generate.return_value = "わー、洞窟を見つけたよ！"
    return generator


@pytest.fixture
def mock_prompt_builder():
    """Create mock prompt builder (sync methods)."""
    builder = Mock()
    builder.build_system_prompt.return_value = "System prompt"
    builder.build_commentary_prompt.return_value = "Commentary prompt"
    return builder


@pytest.fixture
def mock_event_publisher():
    """Create mock event publisher."""
    return AsyncMock()


@pytest.fixture
def conversation():
    """Create conversation history."""
    return Conversation()


@pytest.fixture
def use_case(mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation):
    """Create GenerateCommentaryUseCase with mocked dependencies."""
    return GenerateCommentaryUseCase(
        text_generator=mock_text_generator,
        prompt_builder=mock_prompt_builder,
        event_publisher=mock_event_publisher,
        conversation=conversation,
        character=CharacterProfile(),
    )


class TestGenerateCommentaryUseCase:
    """Tests for GenerateCommentaryUseCase."""

    @pytest.mark.asyncio
    async def test_execute_success(self, use_case, mock_text_generator):
        """Test successful commentary generation."""
        response = await use_case.execute(GenerateCommentaryRequest())

        assert response.success is True
        assert response.text == "わー、洞窟を見つけたよ！"
        mock_text_generator.generate.assert_called_once_with(
            prompt="Commentary prompt",
            system_instruction="System prompt",
        )

    @pytest.mark.asyncio
    async def test_execute_builds_context(self, use_case, mock_prompt_builder, conversation):
        """Test the context passed to the prompt builder."""
        conversation.add_viewer_message("がんばれ", "neko")
        happy = EmotionState(EmotionType.HAPPY, 0.8)
        request = GenerateCommentaryRequest(
            emotion_state=happy,
            recent_events=["e1", "e2", "e3", "e4"],
            game_state_summary="体力: 20/20",
        )

        await use_case.execute(request)

        context = mock_prompt_builder.build_commentary_prompt.call_args.args[0]
        assert context.emotion_state == happy
        assert context.game_state_summary == "体力: 20/20"
        assert context.recent_events == ("e2", "e3", "e4")  # latest 3
        assert [m.content for m in context.recent_messages] == ["がんばれ"]

    @pytest.mark.asyncio
    async def test_execute_records_commentary(self, use_case, conversation):
        """Test generated commentary is added to the conversation."""
        await use_case.execute(GenerateCommentaryRequest())

        last = conversation.recent_messages(1)[0]
        assert last.content == "わー、洞窟を見つけたよ！"
        assert last.message_type == MessageType.COMMENTARY

    @pytest.mark.asyncio
    async def test_execute_publishes_event(self, use_case, mock_event_publisher):
        """Test CommentaryGeneratedEvent is published."""
        await use_case.execute(GenerateCommentaryRequest())

        event = mock_event_publisher.publish.call_args.args[0]
        assert isinstance(event, CommentaryGeneratedEvent)
        assert event.text == "わー、洞窟を見つけたよ！"

    @pytest.mark.asyncio
    async def test_execute_empty_text(
        self, use_case, mock_text_generator, mock_event_publisher, conversation
    ):
        """Test empty generation is not recorded or published."""
        mock_text_generator.generate.return_value = ""

        response = await use_case.execute(GenerateCommentaryRequest())

        assert response.success is False
        assert response.text == ""
        assert response.error is None
        assert len(conversation) == 0
        mock_event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_generation_error(self, use_case, mock_text_generator, conversation):
        """Test generation failure returns an error response with empty text."""
        mock_text_generator.generate.side_effect = TextGenerationError("API down")

        response = await use_case.execute(GenerateCommentaryRequest())

        assert response.success is False
        assert response.text == ""
        assert "API down" in response.error
        assert len(conversation) == 0
