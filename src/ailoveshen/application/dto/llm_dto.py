"""LLM の DTO（Data Transfer Object）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.value_objects import Activity, EmotionState, MidGoal


@dataclass
class GenerateCommentaryRequest:
    """
    実況生成のユースケースの入力 DTO。

    `recent_events` は直前に起きたこと（最新のものが話す題材）、
    `activity` は配信者が今していることとその理由（プレイしていない間は None）。
    """

    emotion_state: EmotionState = field(default_factory=EmotionState)
    recent_events: list[str] = field(default_factory=list)
    activity: Optional[Activity] = None


@dataclass
class GenerateCommentaryResponse:
    """実況生成のユースケースの出力 DTO。"""

    success: bool
    text: str = ""
    error: Optional[str] = None

    @classmethod
    def ok(cls, text: str) -> GenerateCommentaryResponse:
        """レスポンスを作る。テキストが空なら、モデルが使えるものを出さなかったということ。"""
        return cls(success=bool(text), text=text)

    @classmethod
    def error_response(cls, error: str) -> GenerateCommentaryResponse:
        """エラーのレスポンスを作る。"""
        return cls(success=False, error=error)


@dataclass
class GenerateResponseRequest:
    """
    チャット返答生成のユースケースの入力 DTO。

    プレイの `session` があると、返答は配信者が今していることを見て、
    視聴者の頼みを中目標として受けることがある。
    """

    user_name: str
    message: str
    user_id: Optional[str] = None
    emotion_state: EmotionState = field(default_factory=EmotionState)
    session: Optional[PlaySession] = None


@dataclass
class GenerateResponseResponse:
    """チャット返答生成のユースケースの出力 DTO（`mid_goal`: 受けた頼み）。"""

    success: bool
    original_message: str
    user_name: str
    text: str = ""
    mid_goal: Optional[MidGoal] = None
    error: Optional[str] = None

    @classmethod
    def ok(
        cls,
        text: str,
        original_message: str,
        user_name: str,
        mid_goal: Optional[MidGoal] = None,
    ) -> GenerateResponseResponse:
        """レスポンスを作る。テキストが空なら、モデルが使えるものを出さなかったということ。"""
        return cls(
            success=bool(text),
            text=text,
            original_message=original_message,
            user_name=user_name,
            mid_goal=mid_goal,
        )

    @classmethod
    def error_response(
        cls,
        error: str,
        original_message: str,
        user_name: str,
    ) -> GenerateResponseResponse:
        """エラーのレスポンスを作る。"""
        return cls(
            success=False,
            error=error,
            original_message=original_message,
            user_name=user_name,
        )
