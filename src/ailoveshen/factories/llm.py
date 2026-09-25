"""LLM モジュールのファクトリー（Composition Root）。"""

from __future__ import annotations

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.generation_log import IGenerationLog
from ailoveshen.application.use_cases.generate_commentary import GenerateCommentaryUseCase
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.value_objects import CharacterProfile
from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import GeminiTextGenerator
from ailoveshen.infrastructure.adapters.prompts.prompt_template_builder import (
    PromptTemplateBuilder,
)
from ailoveshen.infrastructure.config import CharacterSettings, GeminiSettings
from ailoveshen.presentation.services.llm_service import LLMService


def create_character_profile(settings: CharacterSettings) -> CharacterProfile:
    """キャラクターの設定をドメインの値オブジェクトに変換する。"""
    return CharacterProfile(
        name=settings.name,
        description=settings.description,
        speech_style=settings.speech_style,
        first_person=settings.first_person,
        sentence_endings=tuple(settings.sentence_endings),
        personality_traits=tuple(settings.personality_traits),
    )


def create_llm_service(
    gemini: GeminiSettings,
    character: CharacterSettings,
    event_publisher: IEventPublisher,
    conversation: Conversation,
    mid_goals: MidGoalKeeper | None = None,
    history_limit: int = 10,
    generation_log: IGenerationLog | None = None,
) -> LLMService:
    """
    依存をすべてつないだ LLM サービスを作る。

    LLM モジュールの Composition Root。実況とチャットへの返答は main モデルの枠を
    使い、会話の履歴を 1つ共有する。ゲームの目標の決定もこの履歴を読む
    （create_game_service）。

    Args:
        gemini: Gemini の設定（settings.gemini）
        character: キャラクターの設定（settings.character）
        event_publisher: ドメインイベントの発行先
        conversation: 配信で話したこと。ゲームの目標の決定と共有する
        mid_goals: ゲームの中目標（GameService.mid_goals）。プレイ中の返答は、視聴者の
            頼みをここに受けることがある。None なら返答は話すだけ
        history_limit: モデルに渡す最近のメッセージの数
        generation_log: Gemini の呼び出しの記録（デバッグ用。create_game_service と共有する）

    Returns:
        設定済みで、すぐ使える LLMService

    Raises:
        ValueError: API キーがないか、thinking_level に対応していないとき

    Example:
        ```python
        from ailoveshen.infrastructure.config import load_settings
        from ailoveshen.infrastructure.events import AsyncEventBus

        settings = load_settings()
        llm_service = create_llm_service(
            gemini=settings.gemini,
            character=settings.character,
            event_publisher=AsyncEventBus(),
            conversation=Conversation(),
        )

        text = await llm_service.generate_commentary(recent_events=["洞窟を見つけた"])
        await llm_service.close()
        ```
    """
    # インフラのアダプターを作る
    text_generator = GeminiTextGenerator(
        api_key=gemini.api_key,
        model=gemini.main_model,
        thinking_level=gemini.main_thinking_level,
        max_output_tokens=gemini.max_output_tokens,
        retry_attempts=gemini.retry.max_attempts,
        retry_initial_delay_seconds=gemini.retry.base_delay_seconds,
        retry_max_delay_seconds=gemini.retry.max_delay_seconds,
        retry_exponential_base=gemini.retry.exponential_base,
        min_request_interval_seconds=gemini.rate_limit.min_interval_seconds,
        thinking_levels=gemini.thinking_levels,
        include_thoughts=gemini.include_thoughts,
        generation_log=generation_log,
    )
    prompt_builder = PromptTemplateBuilder()

    # ドメインのオブジェクトを作る
    character_profile = create_character_profile(character)

    # ユースケースを作る
    generate_commentary_use_case = GenerateCommentaryUseCase(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        event_publisher=event_publisher,
        conversation=conversation,
        character=character_profile,
        history_limit=history_limit,
    )
    generate_response_use_case = GenerateResponseUseCase(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        event_publisher=event_publisher,
        conversation=conversation,
        character=character_profile,
        mid_goals=mid_goals,
        history_limit=history_limit,
    )

    # プレゼンテーション層のサービスを作る
    return LLMService(
        generate_commentary_use_case=generate_commentary_use_case,
        generate_response_use_case=generate_response_use_case,
        text_generator=text_generator,
    )
