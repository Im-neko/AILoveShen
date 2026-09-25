"""Style-Bert-VITS2 の声の設定。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceConfig:
    """
    声の設定の値オブジェクト。

    TTS の合成に必要な設定をすべて持つ。

    Raises:
        ValueError: speaker_id が負のとき。
    """

    model_name: str = "default"
    speaker_id: int = 0
    language: str = "JP"
    sdp_ratio: float = 0.2
    noise: float = 0.6
    noisew: float = 0.8
    length: float = 1.0

    def __post_init__(self) -> None:
        """設定を検証する。"""
        if self.speaker_id < 0:
            raise ValueError(f"speaker_id must be non-negative, got {self.speaker_id}")
