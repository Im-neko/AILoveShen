"""TTS の DTO のテスト。"""

import pytest

from ailoveshen.domain.value_objects import EmotionState, EmotionType, SpeechPriority
from ailoveshen.application.dto.speech_dto import (
    SpeakTextRequest,
    SpeakTextResponse,
)


class TestSpeakTextRequest:
    """SpeakTextRequest DTO のテスト。"""

    def test_create_with_defaults(self):
        """既定値でリクエストを作る。"""
        request = SpeakTextRequest(text="hello")
        assert request.text == "hello"
        assert request.priority == SpeechPriority.NORMAL
        assert request.emotion is None
        assert request.source == "unknown"
        assert request.language == "JP"
        assert request.speaker_id == 0

    def test_create_with_all_fields(self):
        """全項目を指定してリクエストを作る。"""
        request = SpeakTextRequest(
            text="hello world",
            priority=SpeechPriority.HIGH,
            emotion=EmotionState(EmotionType.HAPPY, 0.8),
            source="chat",
            language="EN",
            speaker_id=1,
        )
        assert request.text == "hello world"
        assert request.priority == SpeechPriority.HIGH
        assert request.emotion == EmotionState(EmotionType.HAPPY, 0.8)
        assert request.source == "chat"
        assert request.language == "EN"
        assert request.speaker_id == 1


class TestSpeakTextResponse:
    """SpeakTextResponse DTO のテスト。"""

    def test_create_basic_response(self):
        """基本の応答を作る。"""
        response = SpeakTextResponse(success=True)
        assert response.success is True
        assert response.queued is False
        assert response.message == ""
        assert response.duration_ms is None
        assert response.error is None

    def test_ok_factory(self):
        """ok ファクトリメソッド。"""
        response = SpeakTextResponse.ok("Speech done", duration_ms=1500)
        assert response.success is True
        assert response.message == "Speech done"
        assert response.duration_ms == 1500
        assert response.queued is False
        assert response.error is None

    def test_ok_factory_default_message(self):
        """ok ファクトリの既定のメッセージ。"""
        response = SpeakTextResponse.ok()
        assert response.message == "Speech completed"

    def test_interrupted_factory(self):
        """interrupted ファクトリメソッド。"""
        response = SpeakTextResponse.interrupted()
        assert response.success is True
        assert response.message == "Speech interrupted"
        assert response.duration_ms is None

    def test_queued_response_factory(self):
        """queued_response ファクトリメソッド。"""
        response = SpeakTextResponse.queued_response()
        assert response.success is True
        assert response.queued is True
        assert response.message == "Speech queued"

    def test_error_response_factory(self):
        """error_response ファクトリメソッド。"""
        response = SpeakTextResponse.error_response("Connection failed")
        assert response.success is False
        assert response.error == "Connection failed"
        assert "Connection failed" in response.message
