"""感情から Style-Bert-VITS2 のスタイルへの対応。"""

from __future__ import annotations

from typing import Dict, Optional

from ailoveshen.domain.value_objects import EmotionState, EmotionType


class EmotionStyleService:
    """
    ドメインの感情状態を Style-Bert-VITS2 の声のスタイルに対応させる。

    スタイル名は Style-Bert-VITS2 の語彙なので、この対応はドメインではなく
    TTS のアダプターに置く。TTS エンジンを差し替えるときは、アダプター側に
    新しい対応を用意するだけでよい。
    """

    # EmotionType から Style-Bert-VITS2 のスタイル名への既定の対応
    DEFAULT_STYLE_MAP: Dict[EmotionType, str] = {
        EmotionType.NEUTRAL: "Neutral",
        EmotionType.HAPPY: "Happy",
        EmotionType.SAD: "Sad",
        EmotionType.ANGRY: "Angry",
        EmotionType.SURPRISED: "Surprised",
        EmotionType.SCARED: "Sad",  # 代わり: 直接対応するスタイルがない
        EmotionType.EXCITED: "Happy",  # 代わり: 近いスタイルに対応させる
    }

    def __init__(
        self,
        style_map: Optional[Dict[EmotionType, str]] = None,
        default_style: str = "Neutral",
    ) -> None:
        """
        任意の独自の対応で初期化する。

        Args:
            style_map: 感情からスタイルへの独自の対応。既定の対応に上書きで統合する。
            default_style: 感情の種類が見つからないときに使うスタイル。
        """
        self._style_map = self.DEFAULT_STYLE_MAP.copy()
        if style_map:
            self._style_map.update(style_map)
        self._default_style = default_style

    def get_style_for_emotion(self, emotion_state: EmotionState) -> str:
        """
        感情状態に合う TTS のスタイルを返す。

        Args:
            emotion_state: 今の感情状態

        Returns:
            TTS のスタイル名（例: "Happy"、"Sad"、"Neutral"）
        """
        return self._style_map.get(emotion_state.primary, self._default_style)

    def get_style_weight(self, emotion_state: EmotionState) -> float:
        """
        感情の強さからスタイルの重みを返す。

        感情が強いほど、スタイルを強くかける。
        強さ（0.0〜1.0）をスタイルの重み（0.0〜10.0）に対応させる。

        Args:
            emotion_state: 今の感情状態

        Returns:
            TTS のスタイルの重み（0.0〜10.0）
        """
        return emotion_state.intensity * 10.0

    def get_available_styles(self) -> list[str]:
        """
        使えるスタイルの一覧を返す。

        Returns:
            対応に含まれるスタイル名（重複なし）。
        """
        return list(set(self._style_map.values()))
