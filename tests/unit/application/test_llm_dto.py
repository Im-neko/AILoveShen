"""LLM の DTO のテスト。"""

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateCommentaryResponse,
    GenerateResponseRequest,
    GenerateResponseResponse,
)
from ailoveshen.domain.value_objects import EmotionState


class TestGenerateCommentaryDTOs:
    """実況の DTO のテスト。"""

    def test_request_defaults(self):
        """実況リクエストの既定値。"""
        request = GenerateCommentaryRequest()
        assert request.emotion_state == EmotionState()
        assert request.recent_events == []
        assert request.activity is None

    def test_request_default_lists_not_shared(self):
        """recent_events の既定のリストはインスタンスごとに別。"""
        a = GenerateCommentaryRequest()
        a.recent_events.append("event")
        assert GenerateCommentaryRequest().recent_events == []

    def test_ok_with_text(self):
        """テキストのある ok 応答は成功。"""
        response = GenerateCommentaryResponse.ok("洞窟だ！")
        assert response.success is True
        assert response.text == "洞窟だ！"

    def test_ok_with_empty_text_is_not_success(self):
        """テキストが空の ok 応答は成功ではない。"""
        response = GenerateCommentaryResponse.ok("")
        assert response.success is False
        assert response.error is None

    def test_error_response(self):
        """エラーの応答。"""
        response = GenerateCommentaryResponse.error_response("boom")
        assert response.success is False
        assert response.text == ""
        assert response.error == "boom"


class TestGenerateResponseDTOs:
    """コメントへの返答の DTO のテスト。"""

    def test_request_defaults(self):
        """返答リクエストの既定値。"""
        request = GenerateResponseRequest(user_name="neko", message="hi")
        assert request.user_id is None
        assert request.emotion_state == EmotionState()

    def test_ok(self):
        """ok 応答は元のメッセージとユーザーを持つ。"""
        response = GenerateResponseResponse.ok("やっほー", "hi", "neko")
        assert response.success is True
        assert response.original_message == "hi"
        assert response.user_name == "neko"

    def test_error_response(self):
        """エラーの応答も元のメッセージとユーザーを持つ。"""
        response = GenerateResponseResponse.error_response("boom", "hi", "neko")
        assert response.success is False
        assert response.text == ""
        assert response.error == "boom"
        assert response.user_name == "neko"
