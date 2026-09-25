"""プレゼンテーション層の LLM サービス。"""

from __future__ import annotations

from typing import Optional

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateResponseRequest,
)
from ailoveshen.application.ports.input.generate_commentary import IGenerateCommentary
from ailoveshen.application.ports.input.generate_response import IGenerateResponse
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.value_objects import Activity, EmotionState


class LLMService:
    """
    LLM の操作をまとめるプレゼンテーション層のサービス。

    他のコンポーネント（オーケストレーター、チャットの処理）が実況とチャットへの
    返答を得るための簡単な窓口。生成に渡す今の感情を持つ。生成に失敗したときは
    空文字列を返す。
    """

    def __init__(
        self,
        generate_commentary_use_case: IGenerateCommentary,
        generate_response_use_case: IGenerateResponse,
        text_generator: ITextGenerator,
    ) -> None:
        """
        LLM サービスを初期化する。

        Args:
            generate_commentary_use_case: ゲーム実況のユースケース
            generate_response_use_case: チャットへの返答のユースケース
            text_generator: テキスト生成器。サービスと一緒に閉じる
        """
        self._generate_commentary = generate_commentary_use_case
        self._generate_response = generate_response_use_case
        self._text_generator = text_generator
        self._current_emotion = EmotionState()

    async def generate_commentary(
        self,
        recent_events: Optional[list[str]] = None,
        activity: Optional[Activity] = None,
    ) -> str:
        """
        ゲーム実況を生成する。

        Args:
            recent_events: 最近のゲーム内の出来事の説明（最後が最新で、話す内容）
            activity: 配信者が何をしていて、なぜか（PlaySession.activity()）

        Returns:
            生成した実況。失敗したときは空文字列
        """
        response = await self._generate_commentary.execute(
            GenerateCommentaryRequest(
                emotion_state=self._current_emotion,
                recent_events=recent_events or [],
                activity=activity,
            )
        )
        return response.text if response.success else ""

    async def generate_response(
        self,
        user_name: str,
        message: str,
        user_id: Optional[str] = None,
        session: Optional[PlaySession] = None,
    ) -> str:
        """
        視聴者のチャットへの返答を生成する。

        Args:
            user_name: 視聴者の表示名
            message: チャットの内容
            user_id: 視聴者のプラットフォーム上の ID（分かれば）
            session: プレイ中ならそのプレイセッション。返答は配信者が何をしているかを
                見て、視聴者の頼みを次の目標として受けることがある

        Returns:
            生成した返答。失敗したときは空文字列
        """
        response = await self._generate_response.execute(
            GenerateResponseRequest(
                user_name=user_name,
                message=message,
                user_id=user_id,
                emotion_state=self._current_emotion,
                session=session,
            )
        )
        return response.text if response.success else ""

    def update_emotion(self, emotion_state: EmotionState) -> None:
        """以降の生成で使う感情を更新する。"""
        self._current_emotion = emotion_state

    def get_current_emotion(self) -> EmotionState:
        """生成で使う感情を返す。"""
        return self._current_emotion

    async def close(self) -> None:
        """テキスト生成器が持つリソースを解放する。"""
        await self._text_generator.close()
