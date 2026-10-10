"""ゲームのエージェントのファクトリー（Composition Root）。"""

from __future__ import annotations

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.generation_log import IGenerationLog
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.advisor import Advisor
from ailoveshen.application.use_cases.builds import BuildDesigner
from ailoveshen.application.use_cases.checks import CheckWriter
from ailoveshen.application.use_cases.facility_design import FacilityDesigner
from ailoveshen.application.use_cases.naming import NameGiver
from ailoveshen.application.use_cases.danger import DangerWatcher, ReflexPolicy
from ailoveshen.application.use_cases.step_picker import StepPicker
from ailoveshen.application.use_cases.stuck_check import StuckCheck
from ailoveshen.application.use_cases.goal_chooser import GoalChooser
from ailoveshen.application.use_cases.job_planner import JobPlanner
from ailoveshen.application.use_cases.goal_vocabulary import parse_spec
from ailoveshen.application.use_cases.house import HouseDesigner
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.lesson_compaction import LessonCompactor
from ailoveshen.application.use_cases.lessons import LessonBook
from ailoveshen.application.use_cases.learned_candidates import LearnedCandidates
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase, StartPlayUseCase
from ailoveshen.application.use_cases.search import KeySearch
from ailoveshen.application.use_cases.skills import SkillWriter
from ailoveshen.application.use_cases.town import TownPlanner
from ailoveshen.application.use_cases.vision import ScreenReviewer, VisionPolicy
from ailoveshen.application.use_cases.watcher import ToolWatcher, WatchPolicy
from ailoveshen.domain.entities import Conversation, ObjectivePlan
from ailoveshen.domain.value_objects import (
    Mission,
    PlaybookStep,
    StartingObjective,
    Supply,
    SupplyTier,
)
from ailoveshen.factories.llm import create_character_profile
from ailoveshen.factories.text import create_text_generator, has_advisor
from ailoveshen.infrastructure.adapters.jev.jev_action_selector import JevActionSelector
from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import JevFastJudge
from ailoveshen.infrastructure.adapters.map import PilMapRenderer
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
    MineflayerBridgeClient,
)
from ailoveshen.infrastructure.adapters.prompts.abilities import load_abilities
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)
from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore
from ailoveshen.infrastructure.adapters.storage.json_note_store import JsonNoteStore
from ailoveshen.infrastructure.adapters.storage.json_lesson_store import JsonLessonStore
from ailoveshen.infrastructure.adapters.storage.json_goal_store import JsonGoalStore
from ailoveshen.infrastructure.adapters.storage.json_learned_candidate_store import JsonLearnedCandidateStore
from ailoveshen.infrastructure.adapters.storage.jsonl_watch_recorder import JsonlWatchRecorder
from ailoveshen.infrastructure.config import (
    CharacterSettings,
    JevSettings,
    LlmSettings,
    MinecraftSettings,
    MissionSettings,
    OBSSettings,
)
from ailoveshen.infrastructure.run_info import collect_run_info
from ailoveshen.presentation.services.game_service import GameService


def create_objective_plan(settings: MissionSettings) -> ObjectivePlan:
    """
    設定から、最初の大目標と上限を持つ最終目標を作る。

    Raises:
        ValueError: 設定の大目標か上限が不正なとき
    """
    plan = ObjectivePlan(
        mission=Mission(text=settings.text),
        max_goals=settings.max_objectives,
        max_viewer_goals=settings.max_viewer_objectives,
        viewer_budget=settings.viewer_budget_steps,
        playbook=tuple(
            PlaybookStep(
                name=step["name"],
                title=step["title"],
                conditions=tuple(parse_spec(c) for c in step["conditions"]),
                reason=step.get("reason", ""),
                ready=tuple(parse_spec(c) for c in step.get("ready", [])),
                keep=bool(step.get("keep", False)),
            )
            for step in settings.playbook
        ),
        initial=tuple(
            StartingObjective(
                title=goal["title"],
                conditions=tuple(parse_spec(c) for c in goal["conditions"]),
                reason=goal.get("reason", ""),
            )
            for goal in settings.objectives
        ),
    )
    plan.add_initial()
    return plan


def create_supply_tiers(settings: list[dict]) -> tuple[SupplyTier, ...]:
    """
    設定の松明と食べ物のストックの段階（docs/design/84）。

    Raises:
        ValueError: 名前が重なる・数の関係が不正なとき
    """
    tiers = tuple(
        SupplyTier(
            name=t["name"],
            torch=Supply(**t["torch"]),
            food=Supply(**t["food"]),
            after=tuple(t.get("after", ())),
            or_mine=bool(t.get("or_mine", False)),
        )
        for t in settings
    )
    names = [t.name for t in tiers]
    if len(set(names)) != len(names):
        raise ValueError(f"supply tier names must be unique: {names}")
    return tiers


def create_game_service(
    jev: JevSettings,
    minecraft: MinecraftSettings,
    character: CharacterSettings,
    event_publisher: IEventPublisher,
    conversation: Conversation,
    generation_log: IGenerationLog | None = None,
    llm: LlmSettings | None = None,
    text_generator: ITextGenerator | None = None,
    obs: OBSSettings | None = None,
) -> GameService:
    """
    依存をすべてつないだゲームのエージェントのサービスを作る。

    Gemini（main の枠）が家を設計して目標を決め、Mineflayer のブリッジ
    （サイドカー）が目標を判定して候補を具体化し、Jev が候補を 1つずつ選ぶ。
    最終目標とその大目標は settings.minecraft.mission から取り、再起動をまたいで
    引き継ぐ（store_path の JSON）。

    Args:
        jev: Jev の設定（settings.jev）
        minecraft: ブリッジとエージェントの設定（settings.minecraft）
        character: キャラクターの設定（settings.character）
        event_publisher: ドメインイベントの発行先
        conversation: 配信で話したこと（create_llm_service と共有する）。目標の決定が
            これを読み、話したことと食い違う目標を立てないようにする
        generation_log: LLM の呼び出しの記録（デバッグ用。create_llm_service と共有する）
        llm: LLM の設定（settings.llm。ローカルの LLM とリモートの振り分け。docs/design/36、127）
        text_generator: 作ってある生成器を使う（ゲームと返事で 1 つを共有し、ローカルに同時に送る数を
            まとめて数える）
        obs: OBS の設定（settings.obs）。minecraft.vision.enabled で obs.game_source があれば、
            配信の画面を Gemini に見せる（docs/design/23）。None なら見せない

    Returns:
        設定済みの GameService

    Raises:
        ValueError: リモートのモデル名（LITELLM_MODEL）がないとき

    Example:
        ```python
        settings = load_settings()
        game = create_game_service(
            settings.jev, settings.minecraft, settings.character, AsyncEventBus(), Conversation(),
            llm=settings.llm,
        )
        outcome = await game.play()
        await game.close()
        ```
    """
    text_generator = text_generator or create_text_generator(llm, generation_log)
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
    reflex_by_jev = minecraft.reflex.judge == "jev" and bool(jev.api_key)
    if (
        minecraft.control == "tools"
        or (minecraft.small_goals == "jev" and jev.api_key)
        or reflex_by_jev
    ):
        fast_judge = JevFastJudge(
            api_key=jev.api_key, model=jev.model, timeout_seconds=jev.timeout_seconds
        )
    # 小目標は Gemini が書いた中目標から Jev が選ぶ（設計書 26）
    chooser = None
    if minecraft.small_goals == "jev" and fast_judge is not None:
        chooser = GoalChooser(
            judge=fast_judge,
            bridge=bridge,
            min_confidence=minecraft.jev_goal_min_confidence,
            recorder=JsonlWatchRecorder(minecraft.goal_record_dir)
            if minecraft.goal_record_dir
            else None,
            supply_tiers=create_supply_tiers(minecraft.supplies),
        )
    if minecraft.control == "tools":
        assert fast_judge is not None
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
    # 襲われたときの反射を Jev が選ぶ（設計書 28）
    danger_watcher = None
    if reflex_by_jev:
        assert fast_judge is not None
        r = minecraft.reflex
        danger_watcher = DangerWatcher(
            bridge=bridge,
            judge=fast_judge,
            recorder=JsonlWatchRecorder(r.record_dir) if r.record_dir else None,
            policy=ReflexPolicy(
                poll_seconds=r.poll_seconds,
                timeout_seconds=r.timeout_seconds,
                min_confidence=r.min_confidence,
            ),
        )
    # 道具モードの 1 手は、まず Jev が候補と技から選ぶ（設計書 34 §3）
    step_picker = None
    if minecraft.control == "tools" and minecraft.step_picker == "jev" and jev.api_key:
        step_picker = StepPicker(
            selector=action_selector,
            min_confidence=minecraft.step_min_confidence,
            timeout_seconds=minecraft.step_timeout_seconds,
            max_failures=minecraft.step_max_failures,
            gemini_every=minecraft.step_gemini_every,
            recorder=JsonlWatchRecorder(minecraft.step_record_dir)
            if minecraft.step_record_dir
            else None,
        )
    # 行動の記録から行き詰まりを Jev が定期的に確かめる（設計書 34 §9）
    stuck_check = None
    if minecraft.stuck_check_steps > 0 and jev.api_key:
        if fast_judge is None:
            fast_judge = JevFastJudge(
                api_key=jev.api_key, model=jev.model, timeout_seconds=jev.timeout_seconds
            )
        stuck_check = StuckCheck(
            judge=fast_judge,
            every_steps=minecraft.stuck_check_steps,
            min_confidence=minecraft.stuck_min_confidence,
            quiet_seconds=minecraft.stuck_check_quiet_minutes * 60,
            recorder=JsonlWatchRecorder(minecraft.stuck_record_dir)
            if minecraft.stuck_record_dir
            else None,
        )
    # 教訓帳: 視聴者の助言と自分の失敗から学んだことを、似た状況で思い出す（設計書 35）
    lessons = None
    if minecraft.lessons != "off":
        by_jev = minecraft.lessons == "jev" and bool(jev.api_key)
        if by_jev and fast_judge is None:
            fast_judge = JevFastJudge(
                api_key=jev.api_key, model=jev.model, timeout_seconds=jev.timeout_seconds
            )
        lessons = LessonBook(
            JsonLessonStore(minecraft.lessons_path),
            judge=fast_judge if by_jev else None,
            mode="jev" if by_jev else "rules",
            shown=minecraft.lessons_shown,
            candidates=minecraft.lesson_candidates,
            min_confidence=minecraft.lesson_min_confidence,
            timeout_seconds=minecraft.lesson_timeout_seconds,
            max_lessons=minecraft.max_lessons,
            recorder=JsonlWatchRecorder(minecraft.lessons_record_dir)
            if minecraft.lessons_record_dir
            else None,
            compactor=LessonCompactor(text_generator, advisor=has_advisor(llm or LlmSettings())),
        )
        lessons.load()
    # 覚えた候補: LLM が選んでうまくいった手を覚え、似た状況で Jev の選択肢に出す（設計書 52）
    learned_candidates = None
    if minecraft.control == "tools" and minecraft.learned_candidates:
        learned_candidates = LearnedCandidates(
            JsonLearnedCandidateStore(minecraft.learned_candidates_path),
            shown=minecraft.learned_candidates_shown,
            max_learned=minecraft.max_learned_candidates,
            recorder=JsonlWatchRecorder(minecraft.learned_candidates_record_dir)
            if minecraft.learned_candidates_record_dir
            else None,
        )
        learned_candidates.load()
    # 検索の道具: 一覧から検索語に合うキーを Jev が選ぶ（設計書 47 §3）
    search = None
    if minecraft.control == "tools":
        search = KeySearch(
            judge=fast_judge if jev.api_key else None,
            recorder=JsonlWatchRecorder(minecraft.search_record_dir)
            if minecraft.search_record_dir
            else None,
            timeout_seconds=minecraft.search_timeout_seconds,
            min_confidence=minecraft.search_min_confidence,
        )
    store = JsonMissionStore(minecraft.mission.store_path)
    notes = NoteKeeper(JsonNoteStore(minecraft.notes_path))
    profile = create_character_profile(character)
    builder = BuildDesigner(
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        bridge=bridge,
        character=profile,
        renderer=PilMapRenderer(),
    )
    # custom(name) の判定（docs/design/42_custom_conditions.md）: 道具で進めるときだけ（候補は出ない）
    checker = (
        CheckWriter(text_generator=text_generator, prompt_builder=prompt_builder, bridge=bridge)
        if minecraft.control == "tools"
        else None
    )
    # 大目標のプラン: 始めるときに保存から戻す。施設の設計が理想図を読み書きするので同じものを渡す
    plan = create_objective_plan(minecraft.mission)
    objectives = ObjectiveKeeper(
        bridge=bridge,
        event_publisher=event_publisher,
        store=store,
        builder=builder,
        checker=checker,
        facilities=FacilityDesigner(
            text_generator=text_generator,
            prompt_builder=prompt_builder,
            bridge=bridge,
            plan=plan,
            store=store,
        ),
    )
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
        objectives=objectives,
        designer=designer,
    )

    goal_store = JsonGoalStore(minecraft.goal_path) if minecraft.goal_path else None
    start = StartPlayUseCase(
        bridge=bridge,
        event_publisher=event_publisher,
        designer=designer,
        plan=plan,
        store=store,
        town=town,
        notes=notes,
        max_steps_per_goal=minecraft.max_steps_per_goal,
        max_consecutive_failures=minecraft.max_consecutive_failures,
        max_stalled_steps=minecraft.max_stalled_steps,
        goal_store=goal_store,
        collect=minecraft.collect,
    )
    advance = AdvancePlayUseCase(
        bridge=bridge,
        text_generator=text_generator,
        prompt_builder=prompt_builder,
        action_selector=action_selector,
        event_publisher=event_publisher,
        conversation=conversation,
        objectives=objectives,
        town=town,
        notes=notes,
        control=minecraft.control,
        tool_watcher=tool_watcher,
        screen=screen,
        chooser=chooser,
        # 大目標の計画は、小目標を中目標から選ぶ（chooser）ときだけ（docs/design/50 §18）
        planner=(
            JobPlanner(
                text_generator=text_generator,
                prompt_builder=prompt_builder,
                bridge=bridge,
                objectives=objectives,
            )
            if chooser is not None
            else None
        ),
        replan_minutes=minecraft.replan_minutes,
        objective_stall_steps=minecraft.objective_stall_steps,
        step_picker=step_picker,
        failure_routing=minecraft.failure_routing,
        stuck_check=stuck_check,
        stagnation_seconds=minecraft.stagnation_seconds,
        lessons=lessons,
        learned_candidates=learned_candidates,
        goal_store=goal_store,
        goal_end_recorder=(
            JsonlWatchRecorder(minecraft.goal_end_record_dir)
            if minecraft.goal_end_record_dir
            else None
        ),
        search=search,
        abilities=load_abilities(),
        namer=NameGiver(bridge, text_generator, prompt_builder),
        advisor=(
            Advisor(text_generator, prompt_builder)
            if minecraft.control == "tools" and has_advisor(llm or LlmSettings())
            else None
        ),
        skills=(
            SkillWriter(
                text_generator,
                prompt_builder,
                bridge,
                rewrites_per_hour=minecraft.skill_rewrites_per_hour,
            )
            if minecraft.control == "tools" and minecraft.skills_enabled
            else None
        ),
    )
    return GameService(
        start_play=start,
        advance_play=advance,
        bridge=bridge,
        text_generator=text_generator,
        action_selector=action_selector,
        objectives=objectives,
        fast_judge=fast_judge,
        danger_watcher=danger_watcher,
        screen_capture=capture,
        notes=notes,
        lessons=lessons,
        run_recorder=(
            JsonlWatchRecorder(minecraft.run_record_dir) if minecraft.run_record_dir else None
        ),
        run_info=collect_run_info(jev, minecraft, llm),
    )
