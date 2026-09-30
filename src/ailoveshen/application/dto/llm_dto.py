"""LLM の DTO（Data Transfer Object）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.value_objects import Activity, EmotionState, Objective


@dataclass
class GenerateCommentaryRequest:
    """
    実況生成のユースケースの入力 DTO。

    `recent_events` は直前に起きたこと（最新のものが話す題材）、
    `activity` は配信者が今していることとその理由（プレイしていない間は None）。
    `purpose` は生成の用途（振り分けと思考の深さ、費用の集計）、`record` は会話の履歴に残すか
    （行動の途中の一言は残さない: 返事の文脈から視聴者のコメントを押し出さない。設計書 43）。
    """

    emotion_state: EmotionState = field(default_factory=EmotionState)
    recent_events: list[str] = field(default_factory=list)
    activity: Optional[Activity] = None
    purpose: str = "commentary"
    record: bool = True


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
    視聴者の頼みを大目標として受けることがある。
    """

    user_name: str
    message: str
    user_id: Optional[str] = None
    emotion_state: EmotionState = field(default_factory=EmotionState)
    session: Optional[PlaySession] = None
    # 返事の前の仕分け（Jev）が答えたコメントの種類（request / advice / ...）。None: 分からない
    kind: Optional[str] = None
    # 配信の運営（管理画面のディレクター）の指示: 視聴者ではない。従い、大目標は視聴者の上限に数えず、
    # 今やるなら小目標も決め直す（docs/design/54_admin_panel.md）
    producer: bool = False


@dataclass
class GenerateResponseResponse:
    """チャット返答生成のユースケースの出力 DTO（`objective`: 受けた頼み）。"""

    success: bool
    original_message: str
    user_name: str
    text: str = ""
    objective: Optional[Objective] = None
    error: Optional[str] = None

    @classmethod
    def ok(
        cls,
        text: str,
        original_message: str,
        user_name: str,
        objective: Optional[Objective] = None,
    ) -> GenerateResponseResponse:
        """レスポンスを作る。テキストが空なら、モデルが使えるものを出さなかったということ。"""
        return cls(
            success=bool(text),
            text=text,
            original_message=original_message,
            user_name=user_name,
            objective=objective,
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
