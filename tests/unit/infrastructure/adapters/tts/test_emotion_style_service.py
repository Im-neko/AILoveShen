"""EmotionStyleService ドメインサービスのテスト。"""

import pytest

from ailoveshen.domain.value_objects import EmotionState, EmotionType
from ailoveshen.infrastructure.adapters.tts.emotion_style_service import EmotionStyleService


class TestEmotionStyleService:
    """EmotionStyleService のテスト。"""

    @pytest.fixture
    def service(self) -> EmotionStyleService:
        """既定の EmotionStyleService を作る。"""
        return EmotionStyleService()

    def test_get_style_for_neutral_emotion(self, service: EmotionStyleService):
        """neutral の感情のスタイル。"""
        emotion = EmotionState(primary=EmotionType.NEUTRAL, intensity=0.5)
        style = service.get_style_for_emotion(emotion)
        assert style == "Neutral"

    def test_get_style_for_happy_emotion(self, service: EmotionStyleService):
        """happy の感情のスタイル。"""
        emotion = EmotionState(primary=EmotionType.HAPPY, intensity=0.8)
        style = service.get_style_for_emotion(emotion)
        assert style == "Happy"

    def test_get_style_for_sad_emotion(self, service: EmotionStyleService):
        """sad の感情のスタイル。"""
        emotion = EmotionState(primary=EmotionType.SAD, intensity=0.7)
        style = service.get_style_for_emotion(emotion)
        assert style == "Sad"

    def test_get_style_for_angry_emotion(self, service: EmotionStyleService):
        """angry の感情のスタイル。"""
        emotion = EmotionState(primary=EmotionType.ANGRY, intensity=0.9)
        style = service.get_style_for_emotion(emotion)
        assert style == "Angry"

    def test_get_style_for_surprised_emotion(self, service: EmotionStyleService):
        """surprised の感情のスタイル。"""
        emotion = EmotionState(primary=EmotionType.SURPRISED, intensity=0.6)
        style = service.get_style_for_emotion(emotion)
        assert style == "Surprised"

    def test_get_style_for_scared_fallback(self, service: EmotionStyleService):
        """scared の感情は Sad に落とす。"""
        emotion = EmotionState(primary=EmotionType.SCARED, intensity=0.7)
        style = service.get_style_for_emotion(emotion)
        assert style == "Sad"

    def test_get_style_for_excited_fallback(self, service: EmotionStyleService):
        """excited の感情は Happy に落とす。"""
        emotion = EmotionState(primary=EmotionType.EXCITED, intensity=0.9)
        style = service.get_style_for_emotion(emotion)
        assert style == "Happy"

    def test_custom_style_map(self):
        """スタイルの対応を指定する。"""
        custom_map = {
            EmotionType.NEUTRAL: "Custom_Neutral",
            EmotionType.HAPPY: "Custom_Happy",
        }
        service = EmotionStyleService(style_map=custom_map)

        # 指定した対応が既定を上書きする
        emotion = EmotionState(primary=EmotionType.NEUTRAL)
        assert service.get_style_for_emotion(emotion) == "Custom_Neutral"

        # 上書きしていないものは既定を使う
        emotion = EmotionState(primary=EmotionType.SAD)
        assert service.get_style_for_emotion(emotion) == "Sad"

    def test_default_style_for_unknown(self):
        """知らない感情には既定のスタイルを使う。"""
        service = EmotionStyleService(default_style="MyDefault")
        # EmotionType は全部対応があるので、空の対応を渡して確かめる
        service._style_map = {}
        emotion = EmotionState(primary=EmotionType.NEUTRAL)
        assert service.get_style_for_emotion(emotion) == "MyDefault"

    def test_get_style_weight_low_intensity(self, service: EmotionStyleService):
        """強さが低いときのスタイルの重み。"""
        emotion = EmotionState(primary=EmotionType.HAPPY, intensity=0.1)
        weight = service.get_style_weight(emotion)
        assert weight == pytest.approx(1.0)

    def test_get_style_weight_high_intensity(self, service: EmotionStyleService):
        """強さが高いときのスタイルの重み。"""
        emotion = EmotionState(primary=EmotionType.HAPPY, intensity=1.0)
        weight = service.get_style_weight(emotion)
        assert weight == pytest.approx(10.0)

    def test_get_style_weight_mid_intensity(self, service: EmotionStyleService):
        """強さが中くらいのときのスタイルの重み。"""
        emotion = EmotionState(primary=EmotionType.NEUTRAL, intensity=0.5)
        weight = service.get_style_weight(emotion)
        assert weight == pytest.approx(5.0)

    def test_get_available_styles(self, service: EmotionStyleService):
        """使えるスタイルを取る。"""
        styles = service.get_available_styles()
        # スタイルは重ならない
        assert "Neutral" in styles
        assert "Happy" in styles
        assert "Sad" in styles
        assert "Angry" in styles
        assert "Surprised" in styles
        # 重複がない
        assert len(styles) == len(set(styles))
