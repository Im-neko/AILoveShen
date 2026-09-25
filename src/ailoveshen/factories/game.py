"""Game agent factory (Composition Root)."""

from __future__ import annotations

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.use_cases.goal_vocabulary import parse_spec
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase, StartPlayUseCase
from ailoveshen.application.use_cases.town import TownPlanner
from ailoveshen.domain.entities import Conversation, MidGoalPlan
from ailoveshen.domain.value_objects import Mission
from ailoveshen.factories.llm import create_character_profile
from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import GeminiTextGenerator
from ailoveshen.infrastructure.adapters.jev.jev_action_selector import JevActionSelector
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
    MineflayerBridgeClient,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)
from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore
from ailoveshen.infrastructure.config import (
    CharacterSettings,
    GeminiSettings,
    JevSettings,
    MinecraftSettings,
    MissionSettings,
)
from ailoveshen.presentation.services.game_service import GameService


def create_mid_goal_plan(settings: MissionSettings) -> MidGoalPlan:
    """
    The mission with its first mid goals and limits, from the configuration.

    Raises:
        ValueError: If a mid goal or a limit in the configuration is invalid
    """
    plan = MidGoalPlan(
        mission=Mission(text=settings.text),
        max_goals=settings.max_mid_goals,
        max_viewer_goals=settings.max_viewer_mid_goals,
        viewer_budget=settings.viewer_budget_steps,
    )
    for goal in settings.mid_goals:
        plan.add(
            title=goal["title"],
            conditions=tuple(parse_spec(c) for c in goal["conditions"]),
            reason=goal.get("reason", ""),
        )
    return plan


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
    The mission and its mid goals come from settings.minecraft.mission and
    carry on across restarts (JSON at its store_path).

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
    store = JsonMissionStore(minecraft.mission.store_path)
    mid_goals = MidGoalKeeper(bridge=bridge, event_publisher=event_publisher, store=store)
    profile = create_character_profile(character)
    town = TownPlanner(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        bridge=bridge,
        event_publisher=event_publisher,
        character=profile,
        store=store,
    )

    start = StartPlayUseCase(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        bridge=bridge,
        event_publisher=event_publisher,
        character=profile,
        plan=create_mid_goal_plan(minecraft.mission),
        store=store,
        town=town,
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
        mid_goals=mid_goals,
    )
    return GameService(
        start_play=start,
        advance_play=advance,
        bridge=bridge,
        text_generator=text_generator,
        action_selector=action_selector,
        mid_goals=mid_goals,
    )
