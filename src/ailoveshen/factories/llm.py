"""LLM module factory (Composition Root)."""

from __future__ import annotations

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.use_cases.generate_commentary import GenerateCommentaryUseCase
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.value_objects import CharacterProfile
from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import GeminiTextGenerator
from ailoveshen.infrastructure.adapters.prompts.prompt_template_builder import (
    PromptTemplateBuilder,
)
from ailoveshen.infrastructure.config import CharacterSettings, GeminiSettings
from ailoveshen.presentation.services.llm_service import LLMService


def create_character_profile(settings: CharacterSettings) -> CharacterProfile:
    """Convert character settings into the domain value object."""
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
    max_history: int = 20,
    history_limit: int = 10,
) -> LLMService:
    """
    Create LLM service with all dependencies wired up.

    This is the Composition Root for the LLM module. Commentary and chat
    responses use the main model slot and share one conversation history.

    Args:
        gemini: Gemini settings (settings.gemini)
        character: Character settings (settings.character)
        event_publisher: Event publisher for domain events
        max_history: Number of messages the conversation keeps
        history_limit: Number of recent messages given to the model

    Returns:
        Configured LLMService ready to use

    Raises:
        ValueError: If the API key is missing or thinking_level is unsupported

    Example:
        ```python
        from ailoveshen.infrastructure.config import load_settings
        from ailoveshen.infrastructure.events import AsyncEventBus

        settings = load_settings()
        llm_service = create_llm_service(
            gemini=settings.gemini,
            character=settings.character,
            event_publisher=AsyncEventBus(),
        )

        text = await llm_service.generate_commentary(recent_events=["洞窟を見つけた"])
        await llm_service.close()
        ```
    """
    # Create infrastructure adapters
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
    )
    prompt_builder = PromptTemplateBuilder()

    # Create domain objects
    conversation = Conversation(max_history=max_history)
    character_profile = create_character_profile(character)

    # Create use cases
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
        history_limit=history_limit,
    )

    # Create presentation service
    return LLMService(
        generate_commentary_use_case=generate_commentary_use_case,
        generate_response_use_case=generate_response_use_case,
        text_generator=text_generator,
    )
