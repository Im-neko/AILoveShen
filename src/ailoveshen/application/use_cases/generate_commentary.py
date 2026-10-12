"""実況生成のユースケースの実装。"""

from __future__ import annotations

import re

from loguru import logger

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateCommentaryResponse,
)
from ailoveshen.application.ports.input.generate_commentary import IGenerateCommentary
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.prompt_builder import IPromptBuilder
from ailoveshen.application.ports.output.text_generator import ITextGenerator, with_rules
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import CommentaryGeneratedEvent
from ailoveshen.domain.value_objects import CharacterProfile, GenerationContext, MessageType


# 実況の前に書いた、自分への段取りのメモの行（「実況の状況: …述べる。」）。読み上げられた
# （2026-10-10 19:58: 視聴者に「心の声が出ちゃってるよ」と言われた）
PREAMBLE = re.compile(r"^\s*(実況の状況|状況|方針|方向性|メモ|思考|考え|ポイント)\s*[:：]")


def without_preamble(text: str) -> str:
    """実況の本文だけにする（段取りのメモの行を落とす）。本文がなければ空。"""
    if not text:
        return text
    lines = [line for line in text.splitlines() if not PREAMBLE.match(line)]
    return "\n".join(lines).strip() if len(lines) != len(text.splitlines()) else text


class GenerateCommentaryUseCase(IGenerateCommentary):
    """
    ゲーム実況を生成するユースケース（メインループ）。

    次のことをまとめる:
    - リクエストと会話履歴から生成の文脈を作る
    - アダプターでプロンプトを組み立てる
    - アダプターでテキストを生成する
    - 発言を記録し、ドメインイベントを発行する
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IPromptBuilder,
        event_publisher: IEventPublisher,
        conversation: Conversation,
        character: CharacterProfile,
        max_recent_events: int = 3,
        history_limit: int = 10,
    ) -> None:
        """
        依存を受け取ってユースケースを初期化する（依存性の注入）。

        Args:
            text_generator: LLM のテキスト生成のアダプター
            prompt_builder: プロンプト組み立てのアダプター
            event_publisher: ドメインイベントの発行器
            conversation: チャット返答のユースケースと共有する会話履歴
            character: 配信者のキャラクターのプロフィール
            max_recent_events: モデルに渡す最近のゲームのイベントの数
            history_limit: モデルに渡す最近の会話のメッセージの数
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._event_publisher = event_publisher
        self._conversation = conversation
        self._character = character
        self._max_recent_events = max_recent_events
        self._history_limit = history_limit

    async def execute(self, request: GenerateCommentaryRequest) -> GenerateCommentaryResponse:
        """
        実況生成のユースケースを実行する。

        流れ:
        1. 生成の文脈を作る
        2. プロンプトを組み立てる
        3. テキストを生成する
        4. 実況を記録し、CommentaryGeneratedEvent を発行する
        """
        try:
            context = GenerationContext(
                emotion_state=request.emotion_state,
                activity=request.activity,
                recent_events=tuple(request.recent_events[-self._max_recent_events :]),
                recent_messages=self._conversation.recent_messages(self._history_limit),
            )

            system_prompt = self._prompt_builder.build_system_prompt(self._character, "commentary")
            prompt = self._prompt_builder.build_commentary_prompt(context)

            logger.debug("実況を生成する")
            text = await self._text_generator.generate(
                prompt=with_rules(system_prompt, prompt),
                purpose=request.purpose,
            )

            text = without_preamble(text)
            if text and request.record:
                self._conversation.add_streamer_message(text, MessageType.COMMENTARY)
            if text:
                await self._event_publisher.publish(CommentaryGeneratedEvent(text=text))

            return GenerateCommentaryResponse.ok(text)

        except Exception as e:
            logger.error(f"実況の生成に失敗した: {e}")
            return GenerateCommentaryResponse.error_response(str(e))
