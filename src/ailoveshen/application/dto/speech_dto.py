"""発話の DTO（Data Transfer Object）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ailoveshen.domain.value_objects import EmotionState, SpeechPriority


@dataclass
class SpeakTextRequest:
    """
    発話のユースケースの入力 DTO。

    DTO は層の境界をまたいでデータを渡すための、ただの入れ物だ。
    業務のロジックは持たない。
    """

    text: str
    priority: SpeechPriority = SpeechPriority.NORMAL
    emotion: Optional[EmotionState] = None  # None なら今の感情を使う
    source: str = "unknown"  # 出どころの識別子（例: "commentary"、"chat"）
    language: str = "JP"
    speaker_id: int = 0


@dataclass
class SpeakTextResponse:
    """
    発話のユースケースの出力 DTO。

    音声合成と再生の結果を持つ。
    """

    success: bool
    queued: bool = False  # すぐに再生せず、キューに入れたら True
    message: str = ""
    duration_ms: Optional[int] = None  # 分かれば音声の長さ
    error: Optional[str] = None

    @classmethod
    def ok(
        cls,
        message: str = "Speech completed",
        duration_ms: Optional[int] = None,
    ) -> SpeakTextResponse:
        """成功のレスポンスを作る。"""
        return cls(
            success=True,
            message=message,
            duration_ms=duration_ms,
        )

    @classmethod
    def interrupted(cls) -> SpeakTextResponse:
        """割り込まれたときのレスポンスを作る。"""
        return cls(
            success=True,
            message="Speech interrupted",
        )

    @classmethod
    def queued_response(cls) -> SpeakTextResponse:
        """キューに入れたときのレスポンスを作る。"""
        return cls(
            success=True,
            queued=True,
            message="Speech queued",
        )

    @classmethod
    def error_response(cls, error: str) -> SpeakTextResponse:
        """エラーのレスポンスを作る。"""
        return cls(
            success=False,
            error=error,
            message=f"Speech failed: {error}",
        )
