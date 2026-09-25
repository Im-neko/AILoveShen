"""発話の値オブジェクトのテスト。"""

import pytest
from datetime import timezone

from ailoveshen.domain.value_objects import (
    SpeechResult,
    SpeechStatus,
)


class TestSpeechStatus:
    """SpeechStatus 列挙型のテスト。"""

    def test_status_values(self):
        """状態の値が全部ある。"""
        assert SpeechStatus.QUEUED == "queued"
        assert SpeechStatus.SYNTHESIZING == "synthesizing"
        assert SpeechStatus.PLAYING == "playing"
        assert SpeechStatus.COMPLETED == "completed"
        assert SpeechStatus.INTERRUPTED == "interrupted"
        assert SpeechStatus.FAILED == "failed"

    def test_status_is_string_enum(self):
        """状態は文字列として使える。"""
        assert str(SpeechStatus.COMPLETED) == "SpeechStatus.COMPLETED"
        assert SpeechStatus.COMPLETED.value == "completed"


class TestSpeechResult:
    """SpeechResult 値オブジェクトのテスト。"""

    def test_create_basic_result(self):
        """基本の結果を作る。"""
        result = SpeechResult(
            request_text="hello",
            status=SpeechStatus.COMPLETED,
        )
        assert result.request_text == "hello"
        assert result.status == SpeechStatus.COMPLETED
        assert result.audio_duration_ms is None
        assert result.error_message is None

    def test_completed_at_uses_utc(self):
        """completed_at はタイムゾーンつきの UTC。"""
        result = SpeechResult(
            request_text="test",
            status=SpeechStatus.COMPLETED,
        )
        assert result.completed_at.tzinfo == timezone.utc

    def test_completed_factory(self):
        """completed ファクトリメソッド。"""
        result = SpeechResult.completed("hello", 1000)
        assert result.request_text == "hello"
        assert result.status == SpeechStatus.COMPLETED
        assert result.audio_duration_ms == 1000
        assert result.is_success is True

    def test_interrupted_factory(self):
        """interrupted ファクトリメソッド。"""
        result = SpeechResult.interrupted("hello")
        assert result.status == SpeechStatus.INTERRUPTED
        assert result.is_success is False

    def test_failed_factory(self):
        """failed ファクトリメソッド。"""
        result = SpeechResult.failed("hello", "Connection error")
        assert result.status == SpeechStatus.FAILED
        assert result.error_message == "Connection error"
        assert result.is_success is False

    def test_queued_factory(self):
        """queued ファクトリメソッド。"""
        result = SpeechResult.queued("hello")
        assert result.status == SpeechStatus.QUEUED
        assert result.is_success is False

    def test_is_immutable(self):
        """SpeechResult は変更できない。"""
        result = SpeechResult.completed("test", 500)
        with pytest.raises(AttributeError):
            result.request_text = "modified"  # type: ignore
