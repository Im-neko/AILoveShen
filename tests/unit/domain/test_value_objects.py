"""domain の値オブジェクトの単体テスト。"""

import pytest

from ailoveshen.domain.value_objects import (
    EmotionState,
    EmotionType,
    FilterResult,
    Position,
    Rotation,
    SpeechPriority,
    SpeechRequest,
)


class TestEmotionState:
    """EmotionState 値オブジェクトのテスト。"""

    def test_default_values(self):
        """感情の状態の既定値。"""
        state = EmotionState()
        assert state.primary == EmotionType.NEUTRAL
        assert state.intensity == 0.5

    def test_create_with_values(self):
        """値を指定して作る。"""
        state = EmotionState(EmotionType.HAPPY, 0.8)
        assert state.primary == EmotionType.HAPPY
        assert state.intensity == 0.8

    def test_invalid_intensity_raises_error(self):
        """強さが不正なら ValueError。"""
        with pytest.raises(ValueError, match="intensity must be between"):
            EmotionState(EmotionType.HAPPY, 1.5)

        with pytest.raises(ValueError, match="intensity must be between"):
            EmotionState(EmotionType.SAD, -0.5)

    def test_with_intensity(self):
        """強さを変えた新しい状態を作る。"""
        original = EmotionState(EmotionType.HAPPY, 0.8)
        new_state = original.with_intensity(0.5)

        assert new_state.primary == EmotionType.HAPPY
        assert new_state.intensity == 0.5
        # 元は変わらない（不変）
        assert original.intensity == 0.8

    def test_with_intensity_clamps_values(self):
        """with_intensity は値を有効な範囲に収める。"""
        state = EmotionState(EmotionType.HAPPY, 0.5)

        high = state.with_intensity(1.5)
        assert high.intensity == 1.0

        low = state.with_intensity(-0.5)
        assert low.intensity == 0.0

    def test_decay_reduces_intensity(self):
        """decay は強さを下げる。"""
        state = EmotionState(EmotionType.EXCITED, 0.8)
        decayed = state.decay(0.2)

        assert decayed.primary == EmotionType.EXCITED
        assert decayed.intensity == pytest.approx(0.6)

    def test_decay_returns_neutral_when_low(self):
        """decay で強さが下がりすぎたら neutral を返す。"""
        state = EmotionState(EmotionType.SCARED, 0.3)
        decayed = state.decay(0.1)

        assert decayed.primary == EmotionType.NEUTRAL
        assert decayed.intensity == 0.5

    def test_immutable(self):
        """EmotionState は変更できない。"""
        state = EmotionState()
        with pytest.raises(AttributeError):
            state.primary = EmotionType.HAPPY


class TestSpeechRequest:
    """SpeechRequest 値オブジェクトのテスト。"""

    def test_default_values(self):
        """発話リクエストの既定値。"""
        request = SpeechRequest(text="Hello")
        assert request.text == "Hello"
        assert request.priority == SpeechPriority.NORMAL
        assert request.emotion == EmotionState()
        assert request.source == "unknown"

    def test_should_interrupt_low_priority(self):
        """low の優先度は割り込まない。"""
        request = SpeechRequest(text="Test", priority=SpeechPriority.LOW)
        assert not request.should_interrupt()

    def test_should_interrupt_normal_priority(self):
        """normal の優先度は割り込まない。"""
        request = SpeechRequest(text="Test", priority=SpeechPriority.NORMAL)
        assert not request.should_interrupt()

    def test_should_interrupt_high_priority(self):
        """high の優先度は割り込まない。"""
        request = SpeechRequest(text="Test", priority=SpeechPriority.HIGH)
        assert not request.should_interrupt()

    def test_should_interrupt_interrupt_priority(self):
        """interrupt の優先度は割り込む。"""
        request = SpeechRequest(text="Test", priority=SpeechPriority.INTERRUPT)
        assert request.should_interrupt()


class TestPosition:
    """Position 値オブジェクトのテスト。"""

    def test_default_values(self):
        """位置の既定値。"""
        pos = Position()
        assert pos.x == 0.0
        assert pos.y == 0.0
        assert pos.z == 0.0

    def test_distance_to_same_point(self):
        """同じ点までの距離は 0。"""
        pos1 = Position(1.0, 2.0, 3.0)
        pos2 = Position(1.0, 2.0, 3.0)
        assert pos1.distance_to(pos2) == 0.0

    def test_distance_to_different_point(self):
        """距離の計算。"""
        pos1 = Position(0.0, 0.0, 0.0)
        pos2 = Position(3.0, 4.0, 0.0)
        assert pos1.distance_to(pos2) == 5.0

    def test_str_representation(self):
        """文字列表現。"""
        pos = Position(1.5, 2.5, 3.5)
        assert str(pos) == "(1.5, 2.5, 3.5)"


class TestRotation:
    """Rotation 値オブジェクトのテスト。"""

    def test_default_values(self):
        """回転の既定値。"""
        rot = Rotation()
        assert rot.yaw == 0.0
        assert rot.pitch == 0.0

    def test_str_representation(self):
        """文字列表現。"""
        rot = Rotation(45.0, -30.0)
        assert str(rot) == "(yaw=45.0, pitch=-30.0)"

    def test_valid_edge_values(self):
        """回転の端の値は有効。"""
        rot = Rotation(yaw=-180.0, pitch=-90.0)
        assert rot.yaw == -180.0
        assert rot.pitch == -90.0

        rot = Rotation(yaw=180.0, pitch=90.0)
        assert rot.yaw == 180.0
        assert rot.pitch == 90.0

    def test_invalid_yaw_raises_error(self):
        """yaw が不正なら ValueError。"""
        with pytest.raises(ValueError, match="yaw must be between"):
            Rotation(yaw=181.0, pitch=0.0)

        with pytest.raises(ValueError, match="yaw must be between"):
            Rotation(yaw=-181.0, pitch=0.0)

    def test_invalid_pitch_raises_error(self):
        """pitch が不正なら ValueError。"""
        with pytest.raises(ValueError, match="pitch must be between"):
            Rotation(yaw=0.0, pitch=91.0)

        with pytest.raises(ValueError, match="pitch must be between"):
            Rotation(yaw=0.0, pitch=-91.0)


class TestFilterResult:
    """FilterResult 値オブジェクトのテスト。"""

    def test_accept_factory(self):
        """accept ファクトリメソッド。"""
        result = FilterResult.accept(0.8, "Interesting comment")
        assert result.should_respond is True
        assert result.score == 0.8
        assert result.reason == "Interesting comment"

    def test_reject_factory(self):
        """reject ファクトリメソッド。"""
        result = FilterResult.reject(0.2, "Spam detected")
        assert result.should_respond is False
        assert result.score == 0.2
        assert result.reason == "Spam detected"

    def test_invalid_score_raises_error(self):
        """スコアが不正なら ValueError。"""
        with pytest.raises(ValueError, match="score must be between"):
            FilterResult(should_respond=True, score=1.5, reason="test")

        with pytest.raises(ValueError, match="score must be between"):
            FilterResult(should_respond=False, score=-0.1, reason="test")
