"""Tests for LLM DTOs."""

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateCommentaryResponse,
    GenerateResponseRequest,
    GenerateResponseResponse,
)
from ailoveshen.domain.value_objects import EmotionState


class TestGenerateCommentaryDTOs:
    """Tests for commentary DTOs."""

    def test_request_defaults(self):
        """Test commentary request defaults."""
        request = GenerateCommentaryRequest()
        assert request.emotion_state == EmotionState()
        assert request.recent_events == []
        assert request.activity is None

    def test_request_default_lists_not_shared(self):
        """Test default recent_events lists are independent."""
        a = GenerateCommentaryRequest()
        a.recent_events.append("event")
        assert GenerateCommentaryRequest().recent_events == []

    def test_ok_with_text(self):
        """Test ok response with text is successful."""
        response = GenerateCommentaryResponse.ok("洞窟だ！")
        assert response.success is True
        assert response.text == "洞窟だ！"

    def test_ok_with_empty_text_is_not_success(self):
        """Test ok response with empty text is not successful."""
        response = GenerateCommentaryResponse.ok("")
        assert response.success is False
        assert response.error is None

    def test_error_response(self):
        """Test error response."""
        response = GenerateCommentaryResponse.error_response("boom")
        assert response.success is False
        assert response.text == ""
        assert response.error == "boom"


class TestGenerateResponseDTOs:
    """Tests for chat response DTOs."""

    def test_request_defaults(self):
        """Test chat response request defaults."""
        request = GenerateResponseRequest(user_name="neko", message="hi")
        assert request.user_id is None
        assert request.emotion_state == EmotionState()

    def test_ok(self):
        """Test ok response keeps original message and user."""
        response = GenerateResponseResponse.ok("やっほー", "hi", "neko")
        assert response.success is True
        assert response.original_message == "hi"
        assert response.user_name == "neko"

    def test_error_response(self):
        """Test error response keeps original message and user."""
        response = GenerateResponseResponse.error_response("boom", "hi", "neko")
        assert response.success is False
        assert response.text == ""
        assert response.error == "boom"
        assert response.user_name == "neko"
