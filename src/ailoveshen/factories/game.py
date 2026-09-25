"""ゲームのエージェントのファクトリー（Composition Root）。"""

from __future__ import annotations

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.generation_log import IGenerationLog
from ailoveshen.application.use_cases.goal_vocabulary import parse_spec
from ailoveshen.application.use_cases.house import HouseDesigner
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase, StartPlayUseCase
from ailoveshen.application.use_cases.town import TownPlanner
from ailoveshen.application.use_cases.vision import ScreenReviewer, VisionPolicy
from ailoveshen.application.use_cases.watcher import ToolWatcher, WatchPolicy
from ailoveshen.domain.entities import Conversation, MidGoalPlan
from ailoveshen.domain.value_objects import Mission
from ailoveshen.factories.llm import create_character_profile
from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import GeminiTextGenerator
from ailoveshen.infrastructure.adapters.jev.jev_action_selector import JevActionSelector
from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import JevFastJudge
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
    MineflayerBridgeClient,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)
from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore
from ailoveshen.infrastructure.adapters.storage.json_note_store import JsonNoteStore
from ailoveshen.infrastructure.adapters.storage.jsonl_watch_recorder import JsonlWatchRecorder
from ailoveshen.infrastructure.config import (
    CharacterSettings,
    GeminiSettings,
    JevSettings,
    MinecraftSettings,
    MissionSettings,
    OBSSettings,
)
from ailoveshen.presentation.services.game_service import GameService


def create_mid_goal_plan(settings: MissionSettings) -> MidGoalPlan:
    """
    設定から、最初の中目標と上限を持つ大目標を作る。

    Raises:
        ValueError: 設定の中目標か上限が不正なとき
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
    generation_log: IGenerationLog | None = None,
    obs: OBSSettings | None = None,
) -> GameService:
    """
    依存をすべてつないだゲームのエージェントのサービスを作る。

    Gemini（main の枠）が家を設計して目標を決め、Mineflayer のブリッジ
    （サイドカー）が目標を判定して候補を具体化し、Jev が候補を 1つずつ選ぶ。
    大目標とその中目標は settings.minecraft.mission から取り、再起動をまたいで
    引き継ぐ（store_path の JSON）。

    Args:
        gemini: Gemini の設定（settings.gemini）
        jev: Jev の設定（settings.jev）
        minecraft: ブリッジとエージェントの設定（settings.minecraft）
        character: キャラクターの設定（settings.character）
        event_publisher: ドメインイベントの発行先
        conversation: 配信で話したこと（create_llm_service と共有する）。目標の決定が
            これを読み、話したことと食い違う目標を立てないようにする
        generation_log: Gemini の呼び出しの記録（デバッグ用。create_llm_service と共有する）
        obs: OBS の設定（settings.obs）。minecraft.vision.enabled で obs.game_source があれば、
            配信の画面を Gemini に見せる（docs/design/23）。None なら見せない

    Returns:
        設定済みの GameService

    Raises:
        ValueError: API キーがないか、thinking_level に対応していないとき

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
        thinking_levels=gemini.thinking_levels,
        include_thoughts=gemini.include_thoughts,
        generation_log=generation_log,
        media_resolution=gemini.media_resolution,
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
    # 配信の画面を撮る（docs/design/23）。OBS が動いていなくても、画像なしで続く
    screen = None
    capture = None
    if minecraft.vision.enabled and obs is not None and obs.game_source:
        from ailoveshen.infrastructure.adapters.obs.obs_screen_capture import ObsScreenCapture

        capture = ObsScreenCapture(
            host=obs.host,
            port=obs.port,
            password=obs.password,
            source=obs.game_source,
            width=obs.screenshot_width,
            quality=obs.screenshot_quality,
            timeout_seconds=obs.timeout_seconds,
            retry_seconds=obs.retry_seconds,
        )
        screen = ScreenReviewer(
            capture=capture,
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            policy=VisionPolicy(
                review_interval_seconds=minecraft.vision.review_interval_seconds,
                failure_interval_seconds=minecraft.vision.failure_interval_seconds,
            ),
        )
    # control: tools（設計書 21）: Gemini が道具を呼び、Jev が実行中に質問に答える
    fast_judge = None
    tool_watcher = None
    if minecraft.control == "tools":
        fast_judge = JevFastJudge(
            api_key=jev.api_key, model=jev.model, timeout_seconds=jev.timeout_seconds
        )
        w = minecraft.watch
        tool_watcher = ToolWatcher(
            bridge=bridge,
            judge=fast_judge,
            recorder=JsonlWatchRecorder(w.record_dir) if w.record_dir else None,
            policy=WatchPolicy(
                interval_seconds=w.interval_seconds,
                grace_seconds=w.grace_seconds,
                threshold=w.threshold,
                consecutive=w.consecutive,
                act_on_progress=w.act_on_progress,
                act_on_questions=w.act_on_questions,
            ),
        )
    store = JsonMissionStore(minecraft.mission.store_path)
    notes = NoteKeeper(JsonNoteStore(minecraft.notes_path))
    mid_goals = MidGoalKeeper(bridge=bridge, event_publisher=event_publisher, store=store)
    profile = create_character_profile(character)
    designer = HouseDesigner(
        text_generator=text_generator, prompt_builder=prompt_builder, character=profile
    )
    town = TownPlanner(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        bridge=bridge,
        event_publisher=event_publisher,
        character=profile,
        store=store,
        mid_goals=mid_goals,
        designer=designer,
    )

    start = StartPlayUseCase(
        bridge=bridge,
        event_publisher=event_publisher,
        designer=designer,
        plan=create_mid_goal_plan(minecraft.mission),
        store=store,
        town=town,
        notes=notes,
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
        town=town,
        notes=notes,
        control=minecraft.control,
        tool_watcher=tool_watcher,
        screen=screen,
    )
    return GameService(
        start_play=start,
        advance_play=advance,
        bridge=bridge,
        text_generator=text_generator,
        action_selector=action_selector,
        mid_goals=mid_goals,
        fast_judge=fast_judge,
        screen_capture=capture,
    )
