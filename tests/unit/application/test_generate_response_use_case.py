"""Tests for GenerateResponseUseCase."""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import ChatResponseGeneratedEvent
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    MessageRole,
    MessageType,
)


@pytest.fixture
def mock_text_generator():
    """Create mock text generator."""
    generator = AsyncMock()
    generator.generate.return_value = "nekoさん、ありがとう！"
    return generator


@pytest.fixture
def mock_prompt_builder():
    """Create mock prompt builder (sync methods)."""
    builder = Mock()
    builder.build_system_prompt.return_value = "System prompt"
    builder.build_chat_response_prompt.return_value = "Response prompt"
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
    """Create GenerateResponseUseCase with mocked dependencies."""
    return GenerateResponseUseCase(
        text_generator=mock_text_generator,
        prompt_builder=mock_prompt_builder,
        event_publisher=mock_event_publisher,
        conversation=conversation,
        character=CharacterProfile(),
    )


def _request(message: str = "がんばれ") -> GenerateResponseRequest:
    return GenerateResponseRequest(user_name="neko", message=message, user_id="42")


class TestGenerateResponseUseCase:
    """Tests for GenerateResponseUseCase."""

    @pytest.mark.asyncio
    async def test_execute_success(self, use_case, mock_text_generator):
        """Test successful chat response generation."""
        response = await use_case.execute(_request())

        assert response.success is True
        assert response.text == "nekoさん、ありがとう！"
        assert response.original_message == "がんばれ"
        assert response.user_name == "neko"
        mock_text_generator.generate.assert_called_once_with(
            prompt="Response prompt",
            system_instruction="System prompt",
        )

    @pytest.mark.asyncio
    async def test_execute_records_chat_and_reply(self, use_case, conversation):
        """Test both the viewer chat and the reply are recorded in order."""
        await use_case.execute(_request())

        chat, reply = conversation.recent_messages()
        assert chat.role == MessageRole.VIEWER
        assert chat.content == "がんばれ"
        assert chat.speaker_id == "42"
        assert reply.role == MessageRole.STREAMER
        assert reply.message_type == MessageType.RESPONSE

    @pytest.mark.asyncio
    async def test_history_excludes_current_chat(
        self, use_case, mock_prompt_builder, conversation
    ):
        """Test the chat being answered is not duplicated in the history."""
        conversation.add_streamer_message("洞窟だ！", MessageType.COMMENTARY)

        await use_case.execute(_request())

        kwargs = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs
        assert kwargs["user_name"] == "neko"
        assert kwargs["message"] == "がんばれ"
        assert [m.content for m in kwargs["context"].recent_messages] == ["洞窟だ！"]

    @pytest.mark.asyncio
    async def test_execute_publishes_event(self, use_case, mock_event_publisher):
        """Test ChatResponseGeneratedEvent is published."""
        await use_case.execute(_request())

        event = mock_event_publisher.publish.call_args.args[0]
        assert isinstance(event, ChatResponseGeneratedEvent)
        assert event.text == "nekoさん、ありがとう！"
        assert event.original_message == "がんばれ"
        assert event.user_name == "neko"

    @pytest.mark.asyncio
    async def test_execute_empty_text(
        self, use_case, mock_text_generator, mock_event_publisher, conversation
    ):
        """Test empty generation keeps the chat but records no reply."""
        mock_text_generator.generate.return_value = ""

        response = await use_case.execute(_request())

        assert response.success is False
        assert len(conversation) == 1  # only the viewer chat
        mock_event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_generation_error(self, use_case, mock_text_generator):
        """Test generation failure returns an error response with empty text."""
        mock_text_generator.generate.side_effect = TextGenerationError("API down")

        response = await use_case.execute(_request())

        assert response.success is False
        assert response.text == ""
        assert "API down" in response.error
        assert response.user_name == "neko"
