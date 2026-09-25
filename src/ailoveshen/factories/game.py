"""Game agent factory (Composition Root)."""

from __future__ import annotations

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.use_cases.play import AdvancePlayUseCase, StartPlayUseCase
from ailoveshen.domain.entities import Conversation
from ailoveshen.factories.llm import create_character_profile
from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import GeminiTextGenerator
from ailoveshen.infrastructure.adapters.jev.jev_action_selector import JevActionSelector
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
    MineflayerBridgeClient,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)
from ailoveshen.infrastructure.config import (
    CharacterSettings,
    GeminiSettings,
    JevSettings,
    MinecraftSettings,
)
from ailoveshen.presentation.services.game_service import GameService


def create_game_service(
    gemini: GeminiSettings,
    jev: JevSettings,
    minecraft: MinecraftSettings,
    character: CharacterSettings,
    event_publisher: IEventPublisher,
    conversation: Conversation,
) -> GameService:
    """
    Create the game agent service with all dependencies wired up.

    Gemini (main slot) designs the house and sets goals, the Mineflayer
    bridge sidecar judges them and grounds the candidates, Jev picks each one.

    Args:
        gemini: Gemini settings (settings.gemini)
        jev: Jev settings (settings.jev)
        minecraft: Bridge and agent settings (settings.minecraft)
        character: Character settings (settings.character)
        event_publisher: Event publisher for domain events
        conversation: What is said on stream (shared with create_llm_service): the goal
            decision reads it so goals do not contradict what was said

    Returns:
        Configured GameService

    Raises:
        ValueError: If an API key is missing or thinking_level is unsupported

    Example:
        ```python
        settings = load_settings()
        game = create_game_service(
            settings.gemini, settings.jev, settings.minecraft, settings.character, AsyncEventBus(),
            Conversation(),
        )
        outcome = await game.play()
        await game.close()
        ```
    """
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
    action_selector = JevActionSelector(
        api_key=jev.api_key, model=jev.model, timeout_seconds=jev.timeout_seconds
    )
    bridge = MineflayerBridgeClient(
        host=minecraft.bridge_host,
        port=minecraft.bridge_port,
        timeout_seconds=minecraft.request_timeout_seconds,
    )
    prompt_builder = GamePromptTemplateBuilder()

    start = StartPlayUseCase(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        bridge=bridge,
        event_publisher=event_publisher,
        character=create_character_profile(character),
        max_steps_per_goal=minecraft.max_steps_per_goal,
        max_consecutive_failures=minecraft.max_consecutive_failures,
        max_stalled_steps=minecraft.max_stalled_steps,
    )
    advance = AdvancePlayUseCase(
        bridge=bridge,
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        action_selector=action_selector,
        event_publisher=event_publisher,
        conversation=conversation,
    )
    return GameService(
        start_play=start,
        advance_play=advance,
        bridge=bridge,
        text_generator=text_generator,
        action_selector=action_selector,
    )
