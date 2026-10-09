"""プレイのユースケース: LLM が設計して目標を決め、ブリッジが判定し、選択器が遊ぶ。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Optional, TypeVar

from loguru import logger

from ailoveshen.application.dto.game_dto import PlayStepReport
from ailoveshen.application.ports.input.play import IAdvancePlay, IStartPlay
from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.goal_store import IGoalStore, SavedGoal
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.application.use_cases.action_label import action_label
from ailoveshen.application.use_cases.goal_chooser import ChosenGoal, GoalChooser
from ailoveshen.application.use_cases.death_report import death_situation, describe_death
from ailoveshen.application.use_cases.goal_ends import goal_end_entry
from ailoveshen.application.use_cases.facility_satisfy import describe_memory, home_signature
from ailoveshen.application.use_cases.step_picker import PickedStep, StepPicker
from ailoveshen.application.use_cases.repetition import Repetition
from ailoveshen.application.use_cases.same_cause import same_cause_run
from ailoveshen.application.use_cases.stagnation import WAITING_VERBS, Stagnation
from ailoveshen.application.use_cases.stuck_check import StuckCheck
from ailoveshen.application.use_cases.goal_vocabulary import (
    GoalDecision,
    Serves,
    goal_schema,
    parse_decision,
    parse_note_changes,
    predicates_now,
)
from ailoveshen.application.use_cases.house import HouseDesigner, parse_blueprint
from ailoveshen.application.use_cases.job_planner import JobPlanner, unplanned
from ailoveshen.application.use_cases.plan_lint import check_plan
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.lessons import (
    LessonBook,
    clean_keys,
    ended_kind,
    situation_of,
)
from ailoveshen.application.use_cases.learned_candidates import LearnedCandidates, offers_of
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.search import KeySearch, WorldSearch
from ailoveshen.application.use_cases.skills import NotWritten, SkillWriter, order_skills
from ailoveshen.application.use_cases.tool_catalog import (
    ACTION_TOOLS,
    LOOK_SCREEN,
    SEARCH_TOOLS,
    WRITE_SKILL,
    ToolCallError,
    is_query,
    is_skill_tool,
    parse_tool_call,
    tool_specs,
)
from ailoveshen.application.use_cases.town import TownPlanner
from ailoveshen.application.use_cases.vision import ScreenReviewer
from ailoveshen.application.use_cases.watcher import ToolWatcher
from ailoveshen.domain.entities import Conversation, ObjectivePlan, Notebook, PlaySession
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalDecidedEvent,
    GoalDecidingEvent,
    GoalEndedEvent,
    GoalMemberMetEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
    SkillLearnedEvent,
    SkillRevisedEvent,
)
from ailoveshen.domain.exceptions import (
    AILoveShenError,
    BuildNotDesignedError,
    GameBridgeError,
    GoalRejectedError,
    TextGenerationError,
)
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    Activity,
    Candidate,
    ConditionStatus,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    JobState,
    Lesson,
    MessageRole,
    MessageType,
    LearnedCandidate,
    Objective,
    Job,
    Screenshot,
    SkillInfo,
    SkillRun,
    ToolCall,
    ToolOutcome,
    is_survival,
)

CONTROLS = ("candidates", "tools")
MAX_QUERIES_PER_STEP = 5  # 1 ステップの中で調べものをしてよい回数
SUGGESTIONS_SHOWN = 12  # 道具の選択に見せるソルバーの提案の数
RECENT_TOOLS = 8  # 道具の選択に見せる直近の呼び出しの数
GOAL_STEPS_SHOWN = 12  # 失敗の分析に見せる、その小目標の行動の数（docs/design/27）
OFFERED_SHOWN = 12  # 失敗の分析に見せる、最後に出ていた候補の数
MAX_SAME_FAILURES = 2  # 同じ小目標がこの回数続けて失敗したら、やり直しは選べない
UNDESIGNABLE_SECONDS = 600  # 設計できなかった建物を、大目標に足さない間（秒）
MAX_FAILURES_BEFORE_GEMINI = 3  # 種類を問わず続けて失敗したら、Jev に任せず Gemini が考え直す（34 §7）
SAME_CAUSE_EXPERIMENT = 3  # 同じ理由でこれだけ続けて失敗したら、実験の道具を出す（設計書 85）
SKILL_FAILED_NEXT = (
    " | Find the cause first: the call that failed or was refused in the record above, and its "
    "message. If the code is the cause, fix it now with write_skill (name {name}; what = the cause "
    "and the fix). If the situation is the cause (no tool, no seeds), put that right with the "
    "tools. Do not run {name} again unchanged"
)
STUCK_TOOL_FAILURES = 2  # 続けてこれだけ失敗した小目標の道具の選択は、よく考えさせる（purpose tool_stuck）
REFLEX_POLL_SECONDS = 0.5  # 反射が動いている間、終わったかを見る間隔（その間は道具が断られるので選ばせない）
REFLEX_WAIT_LIMIT_SECONDS = 60  # 反射がこれ以上続くなら手放す（ブリッジの反射が離れられない相手と回り続けた: 2026-10-01）
REFLEX_RELEASE_SECONDS = 30  # 手放したあと、近づいた相手に反射させない時間
RETHINK_POLL_SECONDS = 0.5  # 行動の間、考え直しの依頼が来たかを見る間隔（docs/design/38）
# 目標の決定を待つ間（ローカルの LLM で数分かかる）、この間隔で飢えを確かめ、ブリッジの候補に食べる
# 物があれば食べる（2026-09-28: 空腹 0 のまま 6 分近く決定を待って立っていた）。ほかの行動はしない
MEANWHILE_SECONDS = 5.0
HUNGER_URGENT = 6  # ブリッジの HUNGER_URGENT と同じ（これ以下で飢えそう）

T = TypeVar("T")


def _plan_blueprint(obs: GameObservation) -> HouseBlueprint | None:
    """建てている（建てた）家の設計。引っ越しの途中なら、引っ越し先の家。"""
    design = (obs.state.get("build") or {}).get("design") or (obs.state.get("home") or {}).get(
        "design"
    )
    if not design:
        return None
    try:
        return parse_blueprint(design)
    except ValueError as e:
        logger.warning(f"家の設計を読めない: {e}")
        return None


class StartPlayUseCase(IStartPlay):
    """
    ユースケース: LLM が家を設計し、そのブロックのプランをブリッジに送り、セッションを始める。

    ブリッジにもう建築のプランがあるとき（前の実行が送った）は、家を設計しない: その
    プランの家（建ち終わっていれば家、引っ越しの途中なら引っ越し先の家）をそのまま使い、
    完成を伝えたかはプランが建ち終わったかで決める。

    保存したプランが同じ最終目標のものなら、最終目標と大目標はそこから続ける。そうでなければ、
    設定から作ったプランで始める。最終目標の街は、プランにまだなければ定め（以後は保つ）、
    未解決の段階は今の能力で書き直す。
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        designer: HouseDesigner,
        plan: ObjectivePlan,
        store: IMissionStore,
        town: TownPlanner,
        notes: NoteKeeper,
        max_steps_per_goal: int = 40,
        max_consecutive_failures: int = 3,
        max_stalled_steps: int = 8,
        goal_store: Optional[IGoalStore] = None,
        collect: Sequence[str] = (),
    ) -> None:
        """
        依存を受け取ってユースケースを初期化する（依存性の注入）。

        Args:
            bridge: Minecraft ブリッジのアダプター
            event_publisher: ドメインイベントの発行器
            designer: 最初の家を設計する
            plan: 設定から作った、最終目標と最初の大目標
            store: 再起動をまたいでプランを保つ
            town: 最終目標の街を定め、実現できる形に保つ
            notes: 自分のメモを再起動をまたいで戻す
            max_steps_per_goal: LLM に新しい目標を求めるまでのステップ数
            max_consecutive_failures: 新しい目標を求めるまでに、続けて失敗するステップ数
            max_stalled_steps: 新しい目標を求めるまでに、進まないステップ数
            goal_store: 再起動をまたいで今の小目標を保つ（None なら小目標は引き継がない）
            collect: 目標外でも見つけたら集めるブロック（鉱石のほかに。docs/design/79）
        """
        self._goal_store = goal_store
        self._collect = tuple(collect)
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._designer = designer
        self._plan = plan
        self._store = store
        self._town = town
        self._notes = notes
        self._max_steps_per_goal = max_steps_per_goal
        self._max_consecutive_failures = max_consecutive_failures
        self._max_stalled_steps = max_stalled_steps

    async def execute(self) -> PlaySession:
        """プランと街を読み込み、家を設計して（建っていなければ）、セッションを始める。"""
        plan = self._load_plan()
        await self._bridge.set_collect(self._collect)
        await self._town.prepare(plan)
        obs = await self._bridge.observe()
        notebook = Notebook()
        self._notes.load(notebook, obs.day)
        if obs.has_plan:
            blueprint = _plan_blueprint(obs)
            name = f" ({blueprint.name})" if blueprint else ""
            state = "建っている" if obs.house_complete else "建てている途中"
            logger.info(f"家はもう{state}{name}: 新しい家は設計しない")
            session = self._session(blueprint, plan, notebook)
            session.completion_announced = obs.house_complete
            self._resume_goal(session, obs)
            return session
        blueprint = await self._designer.design(provisional=True)
        await self._event_publisher.publish(
            HouseDesignedEvent(name=blueprint.name, concept=blueprint.concept)
        )
        await self._bridge.set_build_plan(blueprint)
        return self._session(blueprint, plan, notebook)

    def _resume_goal(self, session: PlaySession, obs: GameObservation) -> None:
        """
        保存しておいた小目標を、ブリッジが今も同じ小目標を追っているときだけ続きから追う
        （docs/design/63）。大目標が終わった、済んだ、時間帯が変わった、死んだ、は
        いつものとおり最初のステップの `needs_new_goal` が見て、決め直させる。
        """
        saved = self._goal_store.load() if self._goal_store is not None else None
        if saved is None or obs.goal is None:
            return
        lead = replace(saved.goal.spec, dig_depth=None).describe()
        if obs.goal.spec_label != lead:
            logger.info(
                f"保存した小目標 {saved.goal.describe()} は引き継がない"
                f"（ブリッジは {obs.goal.spec_label or 'なし'} を追っている）"
            )
            return
        session.resume_goal(
            saved.goal,
            saved.phase,
            saved.steps_in_goal,
            saved.consecutive_failures,
            saved.stalled_steps,
            saved.least_remaining,
            saved.deaths_at_goal,
        )
        logger.info(
            f"小目標を引き継いだ: {saved.goal.describe()}"
            f"（{saved.steps_in_goal} 歩目、続けて失敗 {saved.consecutive_failures}）"
        )

    def _session(
        self, blueprint: HouseBlueprint | None, plan: ObjectivePlan, notebook: Notebook
    ) -> PlaySession:
        return PlaySession(
            blueprint=blueprint,
            plan=plan,
            notebook=notebook,
            max_steps_per_goal=self._max_steps_per_goal,
            max_consecutive_failures=self._max_consecutive_failures,
            max_stalled_steps=self._max_stalled_steps,
        )

    def _load_plan(self) -> ObjectivePlan:
        plan = self._plan
        saved = self._store.load()
        if saved is not None and saved.mission == plan.mission:
            plan.restore(
                list(saved.pending),
                list(saved.finished),
                saved.next_id,
                next_job_id=saved.next_job_id,
                town=saved.town,
                town_stage=saved.town_stage,
                stage_met=saved.stage_met,
                site=saved.site,
                town_map=saved.town_map,
                playbook_done=saved.playbook_done,
            )
            logger.info(f"大目標を引き継いだ: {', '.join(g.describe() for g in plan.pending)}")
        else:
            if saved is not None:
                logger.info(f"最終目標が「{saved.mission.text}」から変わった: 大目標は最初からにする")
            self._store.save(plan)
        return plan


class AdvancePlayUseCase(IAdvancePlay):
    """
    ユースケース: プレイセッションの 1 ステップ。

    1. 観測する。ブリッジが塞がっている間（反射が近くの脅威に対処している）は、
       何もしない。家の完成は一度だけ伝える
    2. 新しい小目標が要るとき（目標がない、達成した、その大目標が終わった、
       行き詰まった、進まない、長すぎる、時間帯が変わった）は、まず大目標を世界から
       判定し（済んだものは完了にする）、次に今の目標を終わらせて（GoalEndedEvent）、
       LLM が次の目標を決める。LLM は最終目標、大目標、配信者が今していること、最近の
       会話を見る。同時に LLM は大目標リストを編集できる（追加、移動、理由つきの
       断念。プランの上限は守る）。小目標は、編集後のリストの一番上の大目標のため
       か、生存のためのもの。使えない目標や編集は、理由をつけて LLM に戻す
    3. 行動選択器（Jev）が、ブリッジが目標と体の必要から具体化した候補を 1 つ選ぶ。
       候補が 1 つだけならモデルを呼ばずにそれを取る
    4. ブリッジがそれを実行し、セッションはそのステップを目標とその大目標の分として
       数える（予算を超えた視聴者の頼みは断念し、そのことを伝える）

    小目標を変えるのはこのループだけ: チャットの返答は並行して走り、視聴者の大目標を
    今の大目標の後ろに足すことはあるが、割り込むことはない。
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        action_selector: IActionSelector,
        event_publisher: IEventPublisher,
        conversation: Conversation,
        objectives: ObjectiveKeeper,
        town: TownPlanner,
        notes: NoteKeeper,
        history_limit: int = 10,
        max_goal_attempts: int = 3,
        control: str = "candidates",
        tool_watcher: Optional[ToolWatcher] = None,
        screen: Optional[ScreenReviewer] = None,
        chooser: Optional[GoalChooser] = None,
        replan_minutes: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
        skills: Optional[SkillWriter] = None,
        objective_stall_steps: int = 60,
        step_picker: Optional[StepPicker] = None,
        failure_routing: str = "gemini",
        stuck_check: Optional[StuckCheck] = None,
        stagnation_seconds: float = 10.0,
        lessons: Optional[LessonBook] = None,
        learned_candidates: Optional[LearnedCandidates] = None,
        search: Optional[KeySearch] = None,
        abilities: Sequence[dict[str, Any]] = (),
        planner: Optional[JobPlanner] = None,
        goal_store: Optional[IGoalStore] = None,
        goal_end_recorder: Optional[IWatchRecorder] = None,
    ) -> None:
        """
        依存を受け取ってユースケースを初期化する（依存性の注入）。

        Args:
            bridge: Minecraft ブリッジのアダプター
            text_generator: 目標の決定に使う LLM のアダプター（構造化出力）
            prompt_builder: ゲームのプロンプト組み立てのアダプター
            action_selector: 高速な判断モデルのアダプター（Jev）
            event_publisher: ドメインイベントの発行器
            conversation: 配信で話されたこと（実況、返答と共有する）
            objectives: 大目標を判定し、編集し、保存する（チャットの返答と共有する）
            town: 街の準備（候補地の調査、場所の選択、引っ越し、定義）を進める
            notes: 自分のメモを寿命で消し、目標の決定の編集を反映する
            history_limit: 目標の決定に渡す最近の会話のメッセージの数
            max_goal_attempts: 拒否が続くとき、あきらめるまでに目標を決める回数
            control: 行動の決め方。"candidates" はブリッジの候補から選択器（Jev）が選ぶ（比べる
                相手として残す）。"tools" は LLM が道具を呼び、見張り（Jev）が実行中に質問に
                答える（設計書 21）
            tool_watcher: "tools" のとき、道具を実行して見張るもの
            screen: 配信の画面を見せるもの（docs/design/23）。定期の見直し、失敗の後の考え直しに
                画像を添える、道具モードの look_screen。None なら画面は見せない
            chooser: 小目標を、Gemini が書いた中目標の中から Jev に選ばせる（docs/design/26）。
                None なら毎回 Gemini が決める（今までどおり）
            replan_minutes: 中目標があっても Gemini に見直させる間隔（大目標リストが変わって
                いなければ、その 3 倍まで待つ）
            clock: 時計（テストで差し替える）
            skills: 技を書くもの（docs/design/22）。"tools" のとき、技の道具（run_skill /
                write_skill）と技の一覧を出す。None なら技を使わない
            planner: 一番上の大目標の計画（段階と小目標の並び、前提つき）を書くもの（docs/design/50 §18）。
                None なら計画は書かず、Gemini の目標の決定が中目標を書く
            goal_store: 小目標を決めたときと、1 手ごとに、今の小目標と歩みの数えを保存する
                （再起動のあとに続きから追う。None なら保存しない。docs/design/63）
            goal_end_recorder: 小目標が終わるたびに、その終わり方（達成、行き詰まり、死亡、切った）と歩み
                （ステップ数、秒数、残り）を 1 件残す（実行の成績の集計用。None なら残さない。docs/design/82）
            objective_stall_steps: 一番上の大目標の進み具合（条件の数）が、そのための行動を
                これだけ続けても変わらなければ、Gemini に考え直させる（小目標を変えても同じ所を
                回るのを止める。0 なら見ない）

        Raises:
            ValueError: control が不明か、"tools" なのに tool_watcher がないとき
        """
        if control not in CONTROLS:
            raise ValueError(f"control must be one of {CONTROLS}, got {control!r}")
        if control == "tools" and tool_watcher is None:
            raise ValueError('control "tools" needs a tool_watcher')
        self._bridge = bridge
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._action_selector = action_selector
        self._event_publisher = event_publisher
        self._conversation = conversation
        self._objectives = objectives
        self._town = town
        self._notes = notes
        self._history_limit = history_limit
        self._max_goal_attempts = max_goal_attempts
        self._control = control
        self._watcher = tool_watcher
        self._screen = screen
        self._recent_tools: deque[ToolOutcome] = deque(maxlen=RECENT_TOOLS)
        self._chooser = chooser
        self._replan_seconds = replan_minutes * 60
        # 一番上の大目標の進み具合の見張り（docs/design/50 §4）: その id、済んだ中目標の数、条件の進み具合の
        # 数、今の中目標の id とその進み具合の数、変わらないまま使った行動の数
        self._objective_stall_steps = objective_stall_steps
        self._objective_watch: _ObjectiveWatch = _ObjectiveWatch()
        self._objective_idle_steps = 0
        self._clock = clock
        self._last_gemini_at: float | None = None
        self._last_plan: tuple[str, ...] = ()  # 前の Gemini の決定のときの大目標の並び
        # 「作った施設」を見せた時の（大目標の並び、家の場所）。リストか家が変わったときだけ見せる（docs/design/142）
        self._facilities_seen: tuple[tuple[str, ...], tuple[float, float] | None] | None = None
        # 今の小目標の行動の記録と、最後に出ていた候補（失敗の分析に見せる。docs/design/27）
        self._goal_steps: deque[str] = deque(maxlen=GOAL_STEPS_SHOWN)
        self._offered: tuple[Candidate, ...] = ()
        self._skills = skills if control == "tools" else None
        # 道具モードの 1 手は、まず Jev が候補と技から選ぶ（docs/design/34 §3）
        self._step_picker = step_picker if control == "tools" else None
        # 行き詰まった後、まず Jev が別の中目標か Gemini に考え直させるかを選ぶ（docs/design/34 §7）
        self._failure_routing = failure_routing
        # 行動の記録から、行き詰まっていないかを Jev に定期的に聞く（docs/design/34 §9）
        self._stuck = stuck_check
        self._stagnation = Stagnation(stagnation_seconds)
        # 同じ手の並びを続けて成功させたら、技にする機会を LLM に見せる（docs/design/22 §13）
        self._repetition = Repetition()
        # 教訓帳: 小目標の始まりと Gemini の決定の前に思い出し、実績を数える（docs/design/35）
        self._lessons = lessons
        # 覚えた候補: LLM が選んでうまくいった手を覚え、似た状況で Jev の選択肢に出す（docs/design/52）
        self._learned = learned_candidates if control == "tools" else None
        self._in_play: tuple[Lesson, ...] = ()  # 今の小目標の始まりに思い出した教訓
        self._last_ended = ""  # 前の小目標の終わり方（stuck / stalled / died / met / other）
        self._undesignable: dict[str, float] = {}  # 設計できなかった建物 → また試してよい時刻
        self._skill_failures: dict[str, str] = {}  # 技の名前 -> 最後の失敗（直すときに見せる）
        # 検索の道具（docs/design/47 §4）。Jev がなければ語の重なりの上位を返す。abilities はやり方の帳
        self._search = WorldSearch(bridge, search or KeySearch(None), abilities)
        # 大目標の計画（中目標と小目標の並び）を書くもの（docs/design/50 §18）。None なら Gemini の目標の決定が
        # 中目標を書き、小目標は解決器の次の一歩から選ぶ（今までどおり）
        self._planner = planner
        self._goal_store = goal_store
        self._goal_end_recorder = goal_end_recorder
        self._goal_end_recorded: Optional[Goal] = None  # 終わりを記録した小目標（決定に失敗して残った目標を二度数えない）
        self._planned_at: dict[str, float] = {}  # 大目標の id → 最後に計画を書いた（書こうとした）時刻

    async def execute(self, session: PlaySession) -> PlayStepReport:
        """セッションを 1 ステップ進める。"""
        obs = await self._bridge.observe()
        session.observe(obs)
        if obs.house_complete and not session.completion_announced:
            session.completion_announced = True
            name = session.blueprint.name if session.blueprint else ""
            await self._event_publisher.publish(HouseCompletedEvent(name=name))
        if obs.busy:
            return PlayStepReport(
                goal=session.goal,
                goal_changed=False,
                status=obs.goal,
                decision=None,
                result=None,
                house_complete=obs.house_complete,
                waiting=True,
            )

        recovering = await self._recover_first(session, obs)
        if recovering is not None:
            return recovering
        session.track_progress(obs)
        await self._members_met(session, obs)
        if self._screen is not None:
            # 定期の見直し。「考え直す」なら、下の判定で今の小目標が終わる
            await self._screen.review_if_due(session)
        goal_changed = False
        if session.needs_new_goal(obs):
            await self._change_goal(session, obs)
            goal_changed = True
            obs = await self._bridge.observe()
            session.observe(obs)
            if session.died_since_goal(obs):
                # 決めている間に死んだ（決めた目標は死ぬ前の持ち物と場所で書かれている）。動かずに、次の
                # ステップで持ち物を取りに戻り、決め直す。動くと 301 m 歩いた: 2026-10-03
                logger.info("目標を決めている間に死んだ: この目標では動かず、決め直す")
                return PlayStepReport(
                    goal=session.goal,
                    goal_changed=True,
                    status=obs.goal,
                    decision=None,
                    result=None,
                    house_complete=obs.house_complete,
                    waiting=True,
                )

        goal = session.goal
        assert goal is not None
        self._offered = obs.candidates[:OFFERED_SHOWN]
        if self._control == "tools":
            acting = self._act_with_tools(session, obs)
        else:
            acting = self._act_on_candidate(session, goal, obs)
        try:
            decision, result = await self._cut_on_rethink(session, acting)
        finally:
            session.end_action()
        await self._objectives.step_counted(session.plan, session.record(result))
        if self._side_work(obs, decision):
            # ついでの作業（そばの葉から苗木など）は、今の小目標の「進まない」に数えない
            session.skip_stall_once()
        if goal.objective_id is not None and goal.objective_id == self._objective_watch.goal_id:
            self._objective_idle_steps += 1
        line = _step_line(decision, result)
        self._goal_steps.append(line)
        last = self._recent_tools[-1] if self._recent_tools else None
        self._stagnation.record(
            line, result.seconds, waiting=_verb_of(last, obs.candidates) in WAITING_VERBS
        )
        self._repetition.record(_verb_of(last, obs.candidates), result.ok)
        if self._stuck is not None and not session.rethink_reason:
            self._stuck.note(line, obs)
            why = await self._stuck.check_if_due(goal, obs)
            if why:
                logger.info(f"行き詰まり: {why}。次の切れ目で Gemini が考え直す")
                session.request_rethink(why)
        await self._event_publisher.publish(
            GameActionExecutedEvent(
                action_id=result.action_id,
                ok=result.ok,
                result=result.result,
                confidence=decision.confidence,
            )
        )
        self._save_goal(session)
        return PlayStepReport(
            goal=goal,
            goal_changed=goal_changed,
            status=obs.goal,
            decision=decision,
            result=result,
            house_complete=obs.house_complete,
        )

    def _save_goal(self, session: PlaySession) -> None:
        """今の小目標と歩みの数えを保存する（再起動のあとに続きから追う）。"""
        if self._goal_store is None or session.goal is None:
            return
        try:
            self._goal_store.save(
                SavedGoal(
                    goal=session.goal,
                    phase=session.goal_phase,
                    steps_in_goal=session.steps_in_goal,
                    consecutive_failures=session.consecutive_failures,
                    stalled_steps=session.stalled_steps,
                    least_remaining=session.least_remaining,
                    deaths_at_goal=session.deaths_at_goal,
                )
            )
        except OSError as e:
            logger.warning(f"小目標を保存できなかった: {e}")

    async def _recover_first(
        self, session: PlaySession, obs: GameObservation
    ) -> Optional[PlayStepReport]:
        """
        死んだ直後は、次の小目標を LLM に決めさせる前に、落とした持ち物を取りに戻る（設計書 50 §17）。
        持ち物は 5 分で消えるのに、小目標の決め直しは数十秒かかる（2026-09-30: 死んでから取りに
        戻るまで 48 秒、うち決め直しが 2 回）。新しい小目標がない間の 1 手なので、ステップの数には
        入れない。取りに戻る手がなくなれば（取り戻した、2 回行った、期限が過ぎた）通常どおり決める。
        """
        if self._step_picker is None or session.goal is None or not session.died_since_goal(obs):
            return None
        state, _ = self._prompt_builder.build_action_context(session.goal, obs)
        picked = self._step_picker.recovery(state, obs.candidates)
        if picked is None:
            return None
        call = picked.call
        session.start_action(call.intent, action_label(call, obs.candidates))
        logger.info(f"死んだので、決め直す前に持ち物を取りに戻る: {call.describe()}")
        assert self._watcher is not None
        try:
            outcome = await self._watcher.run(call)
        finally:
            session.end_action()
        self._recent_tools.append(outcome)
        self._step_picker.note(by_jev=True, outcome=outcome)
        return PlayStepReport(
            goal=session.goal,
            goal_changed=False,
            status=obs.goal,
            decision=ActionDecision(action_id=call.describe(), confidence=picked.confidence),
            result=outcome.as_action_result(),
            house_complete=obs.house_complete,
        )

    async def _cut_on_rethink(self, session: PlaySession, acting: Awaitable[T]) -> T:
        """
        行動の間に考え直しを頼まれたら（コメント、約束した頼み）、実行中の行動を止める。行動は時間で
        止めないので、切れ目を待つと長い移動の終わりまで待つことになる（docs/design/38）。まだ
        行動が始まっていなければ（道具を選んでいる）、始まるまで止め続ける。
        """
        mark = session.rethink_mark
        task = asyncio.ensure_future(acting)
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=RETHINK_POLL_SECONDS)
                if task.done() or session.rethink_mark <= mark or session.defers_rethink():
                    continue
                try:
                    if await self._bridge.abort(f"rethink: {session.rethink_reason}"):
                        logger.info(f"考え直しの依頼で行動を止めた: {session.rethink_reason}")
                        mark = session.rethink_mark
                except GameBridgeError as e:
                    logger.warning(f"考え直しで行動を止められない: {e}")
            return task.result()
        finally:
            if not task.done():
                task.cancel()

    async def _act_on_candidate(
        self, session: PlaySession, goal: Goal, obs: GameObservation
    ) -> tuple[ActionDecision, ActionResult]:
        """選択器（Jev）がブリッジの候補から 1 つ選び、ブリッジが実行する。"""
        candidates = obs.candidates
        if len(candidates) == 1:
            decision = ActionDecision(action_id=candidates[0].action_id, confidence=1.0)
        else:
            state, instructions = self._prompt_builder.build_action_context(goal, obs)
            logger.info(
                f"候補（{len(candidates)}）: " + " | ".join(c.action_id for c in candidates)
            )
            decision = await self._action_selector.select(state, candidates, instructions)
        session.start_action(
            action=action_label(
                ToolCall("do_suggestion", {"id": decision.action_id}), candidates
            )
        )
        return decision, await self._bridge.act(decision.action_id)

    @staticmethod
    def _side_work(obs: GameObservation, decision: ActionDecision) -> bool:
        """選んだ候補が、ほかの大目標の物をついでに取るものか（今の目標の残りは減らない）。"""
        return any(
            c.action_id == decision.action_id and c.description.get("also") for c in obs.candidates
        )

    async def _wait_out_reflex(self) -> dict[str, Any]:
        """
        反射（近くの敵への対処）が動いている間は、道具も候補も断られる。モデルに選ばせても
        断られて 1 手を無駄にするので、反射が終わるまで待つ。
        """
        state = await self._bridge.state()
        if not state.get("reflex"):
            return state
        logger.info("反射が動いているので、終わるまで選ばせずに待つ")
        waited = 0.0
        while state.get("reflex"):
            if waited >= REFLEX_WAIT_LIMIT_SECONDS:
                # 待っても終わらない（同じ相手から逃げ続けるなど）: 待ち続けると、止まっている検知（Stagnation、
                # StuckCheck）にも届かない。反射を手放して、選ばせる
                logger.warning(f"反射が {REFLEX_WAIT_LIMIT_SECONDS} 秒続いている: 手放して、選ばせる")
                await self._bridge.release_reflex(REFLEX_RELEASE_SECONDS)
                return await self._bridge.state()
            await asyncio.sleep(REFLEX_POLL_SECONDS)
            waited += REFLEX_POLL_SECONDS
            state = await self._bridge.state()
        return state

    async def _act_with_tools(
        self, session: PlaySession, obs: GameObservation
    ) -> tuple[ActionDecision, ActionResult]:
        """
        LLM が道具を呼ぶ（設計書 21 §2）。調べものの道具はすぐ結果を返し、同じステップの中で
        選び直す（MAX_QUERIES_PER_STEP 回まで。その後は行動の道具だけを出す）。行動の道具は
        見張りつきで実行し、1 ステップはそれで終わる。
        """
        seen = await self._wait_out_reflex()
        # 位置・持ち物・小目標の進み具合が 10 秒変わらないなら、Jev に任せず LLM に、その間の手と結果を
        # 見せて別の方法を探させる（失敗が続いていなくても。成功して何も変わらない手もある）
        stagnant = self._stagnation.observe(seen, obs)
        session.stagnant_seconds = stagnant
        if stagnant:
            logger.info(f"{stagnant:.0f} 秒、状況が変わらない: Jev に任せず、失敗と結果を踏まえて選び直させる")
        skills = await self._skill_list(session) if self._skills is not None else None
        # 同じ手の並びが続いていれば、Jev に任せず LLM に見せる（技にするか、ある技を呼ぶか。LLM が決める）
        repeat = self._repetition.find() if skills is not None else None
        if repeat is not None:
            logger.info(f"{repeat.describe()} を {repeat.times} 回続けた: 技にする機会を LLM に見せる")
        if (stagnant or repeat) and self._step_picker is not None and session.goal is not None:
            self._step_picker.skip(
                session.goal.describe(),
                f"nothing changed for {stagnant:.0f} s" if stagnant else "the same steps repeat",
            )
        learned: list[LearnedCandidate] = []
        situation: dict[str, str] = {}
        if self._learned is not None and session.goal is not None:
            self._learned.settle(obs, session.goal.describe())
            situation = {**situation_of(session.goal.spec, obs), "offers": offers_of(obs.candidates)}
            learned = self._learned.recall(situation)
        picked = (
            None
            if stagnant or repeat
            else await self._jev_step(session, obs, skills or [], learned, situation)
        )
        if picked is not None:
            return picked
        # 行き詰まっているときだけ、実験の道具（inspect / diagnostics、素手・面・スニークの use_item）を加える（設計書 85）
        specs_by_experiment = {
            on: tool_specs(can_look=self._screen is not None, skills=self._skills is not None, experiment=on)
            for on in (False, True)
        }
        images: list[Screenshot] = []  # 次の選択にだけ添える画面（ステップをまたがない）
        last = self._recent_tools[-1] if self._recent_tools else None
        if self._screen is not None and last is not None and not last.ok:
            shot = await self._screen.after_failure()
            images = [shot] if shot else []
        asked: set[str] = set()  # このステップで答えた調べもの（同じ問いを繰り返させない）
        queries_closed = False
        for attempt in range(MAX_QUERIES_PER_STEP + 1):
            state = await self._bridge.state()
            cause = same_cause_run(self._recent_tools, lambda name: is_query(name) or name in SEARCH_TOOLS or name == LOOK_SCREEN)
            if cause is not None and attempt == 0:
                logger.info(f"同じ理由で {cause.times} 回続けて失敗: {cause.tool}（{cause.reason[:100]}）")
            prompt = self._prompt_builder.build_tool_prompt(
                session.activity(),
                state,
                obs.candidates[:SUGGESTIONS_SHOWN],
                tuple(self._recent_tools),
                skills=skills,
                failing=session.consecutive_failures,
                stagnant_seconds=stagnant,
                stagnant_actions=self._stagnation.actions(),
                repeated=repeat.steps if repeat else (),
                repeated_times=repeat.times if repeat else 0,
                repeated_skill=_fitting_skill(skills or [], session.goal) if repeat else "",
                same_cause=cause,
            )
            last = self._recent_tools[-1] if self._recent_tools else None
            purpose = "tool_after_failure" if last is not None and not last.ok else "tool"
            if session.consecutive_failures >= STUCK_TOOL_FAILURES or stagnant:
                purpose = "tool_stuck"  # 行き詰まり: よく考えて別の方法を選ばせる
            experiment = (
                session.consecutive_failures >= STUCK_TOOL_FAILURES
                or bool(stagnant)
                or (cause is not None and cause.times >= SAME_CAUSE_EXPERIMENT)
            )
            specs = specs_by_experiment[experiment]
            offered = (
                specs
                if attempt < MAX_QUERIES_PER_STEP and not queries_closed
                else [t for t in specs if t.name in ACTION_TOOLS or is_skill_tool(t.name)]
            )
            if cause is not None and cause.withheld:  # 数字を変えて繰り返すのをやめさせる: 別の道具だけを出す
                offered = [t for t in offered if t.name != cause.tool]
            if state.get("pickup_ready") is False:  # 拾える落とし物がなければ pickup は断られるだけ
                offered = [t for t in offered if t.name != "pickup"]
            choice = await self._text_generator.choose_tool(
                prompt, offered, purpose=purpose, images=images
            )
            images = []
            try:
                call = parse_tool_call(choice)
            except ToolCallError as e:
                outcome = ToolOutcome(
                    call=ToolCall(name=choice.name or "?", args=dict(choice.args)),
                    ok=False,
                    result=f"invalid call: {e}",
                    seconds=0.0,
                    refused=True,
                )
                self._recent_tools.append(outcome)
                break
            if is_query(call.name) and attempt == MAX_QUERIES_PER_STEP:
                outcome = ToolOutcome(
                    call=call,
                    ok=False,
                    result="invalid call: no more lookups in this step; call an action tool",
                    seconds=0.0,
                    refused=True,
                )
                self._recent_tools.append(outcome)
                break
            if is_query(call.name) or call.name in SEARCH_TOOLS:
                # 同じ調べものを繰り返すと、答えは変わらないまま 1 回 5 秒かかる（2026-10-07 01:25Z: 食べ物がなくて
                # find_foods を 5 回続け、動かなかった）。2 回目で断り、あとは行動の道具だけを出す
                asked_key = json.dumps([call.name, call.args], sort_keys=True, default=str)
                if asked_key in asked:
                    self._recent_tools.append(
                        ToolOutcome(
                            call=call,
                            ok=False,
                            result="invalid call: this same lookup was just answered above and the answer has not changed; call an action tool",
                            seconds=0.0,
                            refused=True,
                        )
                    )
                    queries_closed = True
                    continue
                asked.add(asked_key)
            if call.name == LOOK_SCREEN:
                shot = await self._screen.look() if self._screen is not None else None
                images = [shot] if shot else []
                result = "the screen is attached to this prompt" if shot else "could not capture"
                self._recent_tools.append(
                    ToolOutcome(call=call, ok=shot is not None, result=result, seconds=0.0)
                )
                continue
            if call.name in SEARCH_TOOLS:
                started = self._clock()
                ok, result = await self._search.run(call.name, call.args)
                self._recent_tools.append(
                    ToolOutcome(call=call, ok=ok, result=result, seconds=self._clock() - started)
                )
                logger.info(f"検索した: {call.describe()} -> {result[:200]}")
                continue
            if is_query(call.name):
                ok, result, seconds, refused = await self._bridge.run_tool(call.name, call.args)
                self._recent_tools.append(
                    ToolOutcome(call=call, ok=ok, result=result, seconds=seconds, refused=refused)
                )
                logger.info(f"調べた: {call.describe()} -> {result[:200]}")
                continue
            session.start_action(call.intent, action_label(call, obs.candidates))
            logger.info(
                f"道具: {call.describe()}「{call.intent}」"
                + (f" 見張り: {[w.question for w in call.watch]}" if call.watch else "")
            )
            assert self._watcher is not None
            if is_skill_tool(call.name):
                outcome = await self._skill_step(session, call, skills or [])
            else:
                outcome = await self._watcher.run(call)
            self._recent_tools.append(outcome)
            break
        if self._step_picker is not None:
            self._step_picker.note(by_jev=False, outcome=outcome)
        if self._learned is not None and session.goal is not None:
            self._learned.begin(
                situation, outcome.call, obs, session.goal.describe(), outcome.ok and not outcome.refused
            )
        decision = ActionDecision(action_id=outcome.call.describe(), confidence=1.0)
        return decision, outcome.as_action_result()

    async def _jev_step(
        self,
        session: PlaySession,
        obs: GameObservation,
        skills: list[SkillInfo],
        learned: Sequence[LearnedCandidate] = (),
        situation: Optional[dict[str, str]] = None,
    ) -> Optional[tuple[ActionDecision, ActionResult]]:
        """
        まず Jev が候補と技から 1 手を選んで実行する（docs/design/34 §3）。Jev が選ばなければ
        None（Gemini が選ぶ）。実行の経路と記録は Gemini が選んだときと同じ。
        """
        if self._step_picker is None or session.goal is None:
            return None
        last = self._recent_tools[-1] if self._recent_tools else None
        state, instructions = self._prompt_builder.build_action_context(session.goal, obs)
        # 断られた手（ブリッジが実行する前に断った。同じ手は台帳で次から出ない）は、失敗として LLM に
        # 回さない: 決まりきった断りで 1 手（LLM の数秒）を使わず、残った候補から Jev が選ぶ
        last_ok = None if last is None else last.ok or last.refused
        picked = await self._step_picker.pick(
            state, instructions, obs.candidates, skills, last_ok, learned=learned
        )
        if not isinstance(picked, PickedStep):
            logger.info(f"1 手は Gemini が決める: {picked.why}")
            return None
        call = picked.call
        session.start_action(call.intent, action_label(call, obs.candidates))
        logger.info(f"1 手は Jev が選んだ（確信度 {picked.confidence:.2f}）: {call.describe()}")
        assert self._watcher is not None
        if picked.skill is not None:
            outcome = await self._run_skill(
                call, picked.skill.name, {}, None, picked.skill.description
            )
        else:
            outcome = await self._watcher.run(call)
        self._recent_tools.append(outcome)
        self._step_picker.note(by_jev=True, outcome=outcome)
        if self._learned is not None and picked.learned is not None:
            self._learned.begin(
                situation or {},
                call,
                obs,
                session.goal.describe(),
                outcome.ok and not outcome.refused,
                picked.learned,
            )
        decision = ActionDecision(action_id=picked.option_id, confidence=picked.confidence)
        return decision, outcome.as_action_result()

    async def _skill_list(self, session: PlaySession) -> list[SkillInfo]:
        """道具の選択に見せる技（今の小目標に合うものが先）。読めなければ空（プレイは止めない）。"""
        try:
            skills = await self._bridge.skills()
        except GameBridgeError as e:
            logger.warning(f"技の一覧を読めなかった: {e}")
            return []
        item = session.goal.spec.item if session.goal else None
        return order_skills(skills, item)

    async def _skill_step(
        self, session: PlaySession, call: ToolCall, skills: list[SkillInfo]
    ) -> ToolOutcome:
        """技を実行する（write_skill なら、書いてからすぐ 1 回試す）。docs/design/22 §4。"""
        assert self._skills is not None
        name = call.args["name"]
        if call.name != WRITE_SKILL:
            known = next((s for s in skills if s.name == name), None)
            return await self._run_skill(
                call,
                name,
                call.args.get("args", {}),
                call.args.get("version"),
                known.description if known else name,
            )
        written = await self._skills.write(
            name,
            call.args["what"],
            session.activity(),
            tuple(self._recent_tools),
            skills,
            self._skill_failures.get(name, ""),
        )
        if isinstance(written, NotWritten):
            logger.info(f"技を書けなかった: {name}: {written.why}")
            return ToolOutcome(call=call, ok=False, result=written.why, seconds=0.0, refused=True)
        await self._event_publisher.publish(
            SkillRevisedEvent(
                name=name,
                description=written.description,
                version=written.version,
                reason=written.revised_because,
            )
        )
        outcome = await self._run_skill(
            call, name, written.trial_args, written.version, written.description
        )
        return replace(outcome, result=f"wrote {name} v{written.version}; trial: {outcome.result}")

    async def _run_skill(
        self, call: ToolCall, name: str, args: dict, version: Optional[int], description: str
    ) -> ToolOutcome:
        """技を見張りつきで 1 回実行し、覚えたら知らせ、失敗は次に直すときのために取っておく。"""
        assert self._watcher is not None
        runs: list[SkillRun] = []

        async def execute() -> tuple[bool, str, float, bool]:
            run = await self._bridge.run_skill(name, args, version)
            runs.append(run)
            return run.ok, run.describe(), run.seconds, False

        outcome = await self._watcher.run(call, execute=execute)
        if runs:
            run = runs[0]
            logger.info(f"技: {run.describe()}（{run.seconds}秒、道具 {len(run.calls)}）")
            if run.ok:
                self._skill_failures.pop(name, None)
            elif run.ended != "refused":  # 動かさなかった断りは、直すときに見せる失敗ではない
                self._skill_failures[name] = run.describe()
                if run.ended != "stopped":  # 見張りが止めたのは、技のコードの欠陥ではない
                    note = SKILL_FAILED_NEXT.format(name=name)
                    outcome = replace(outcome, result=f"{outcome.result}{note}")
            if run.learned:
                await self._event_publisher.publish(
                    SkillLearnedEvent(name=name, description=description)
                )
        return outcome

    async def _change_goal(self, session: PlaySession, obs: GameObservation) -> None:
        # ここで読んだ考え直しだけを使ったことにする（決めている間に来たコメントの指摘は残す）
        seen = session.rethink_mark
        reason = session.goal_end_reason(obs)
        # 決める前に判定する: もう済んでいる大目標（前の実行で建てた家）を、次の目標の
        # 対象にしてはいけない
        await self._objectives.judge(session.plan)
        await self._town.advance(session, obs)
        self._notes.expire(session.notebook, obs.day)
        stuck = self._objective_stuck(session)
        if stuck:
            reason = f"{reason}; {stuck}" if reason else stuck
        # 目標が終わる前の見え方: その状態が、終わる理由だ
        activity = session.activity()
        ended = ended_kind(reason)
        # 終わった小目標の状況（教訓の条件と、思い出す手がかり。docs/design/35）
        situation = situation_of(session.goal.spec if session.goal else None, obs, ended)
        if ended == "died":
            # 復活すると家の中・体力満タンになる: 教訓の「どんなときか」は、死ぬ前の様子から（設計書 105）
            situation = {**situation, **death_situation(obs.state.get("last_death"))}
        if self._lessons is not None and self._in_play:
            self._lessons.settle(self._in_play, ended)
        self._in_play = ()
        self._last_ended = ended
        await self._end_goal(session, obs, reason)
        why = self._needs_gemini(session, reason)
        if await self._plan_objective(session, activity, reason, why):
            why = self._needs_gemini(session, reason)
        if why is None:
            assert self._chooser is not None
            failed = (
                session.recent_goals[-1].goal.spec
                if _no_progress(reason) and session.recent_goals
                else None
            )
            picked = await self._chooser.choose(session, obs, reason, avoid=failed)
            if isinstance(picked, ChosenGoal):
                try:
                    status = await self._bridge.set_goal(
                        picked.spec,
                        _kept(session.plan),
                        _also(session.plan, (picked.spec, *picked.together)),
                        together=picked.together,
                        level_areas=_level_areas(session.plan, (picked.spec, *picked.together)),
                    )
                except GoalRejectedError as e:
                    why = f"the bridge rejected {picked.describe()}: {e}"
                else:
                    goal = Goal(
                        spec=picked.spec,
                        reason=picked.reason,
                        objective_id=picked.objective_id,
                        together=picked.together,
                        approach=picked.approach,
                    )
                    logger.info(
                        f"小目標は Jev が選んだ（確信度 {picked.confidence:.2f}）: "
                        f"{picked.describe()}"
                    )
                    await self._start_goal(session, obs, goal, reason, status, seen)
                    return
            else:
                why = picked.why
        logger.info(f"小目標は Gemini が決める: {why}")
        if self._lessons is not None:
            recalled = await self._lessons.recall(
                situation,
                _upcoming(session.plan),
                {
                    "last_goal": activity.goal.describe() if activity.goal else None,
                    "ended_because": reason,
                    **_light_of(obs),
                },
                moment="decision",
            )
            activity = replace(activity, lessons=recalled)
        await self._while_deciding(
            self._decide_goal(session, obs, activity, reason, situation, seen),
            after_failure=any(k in reason for k in REPLAN_REASONS),
        )
        self._last_gemini_at = self._clock()
        self._last_plan = tuple(g.id for g in session.plan.pending)

    async def _plan_objective(
        self, session: PlaySession, activity: Activity, reason: str, why_gemini: str | None
    ) -> bool:
        """
        一番上の大目標の計画を、まだなら（中目標が尽きて大目標が済んでいないときも）書く。書き直すのは、
        失敗の後に Gemini が考え直すとき・死んだとき・大目標が進まないとき（一定の間隔を置いて）。
        計画を書いたか（docs/design/50 §18）。
        """
        current = session.plan.current
        if self._planner is None or current is None:
            return False
        failed = why_gemini is not None and any(k in reason for k in REPLAN_REASONS)
        if not (unplanned(current) or failed):
            return False
        now = self._clock()
        if now - self._planned_at.get(current.id, -PLAN_INTERVAL_SECONDS) < PLAN_INTERVAL_SECONDS:
            return False
        self._planned_at[current.id] = now
        replan = ""
        if current.planned and failed:
            record = "; ".join(list(self._goal_steps)[-PLAN_RECORD_SHOWN:])
            replan = reason + (f". The last actions: {record}" if record else "")
        written: list[bool] = []
        # 終わったばかりの小目標がこの大目標のものでなければ、計画には見せない。置き換わる小目標（死んだ
        # ときの食料と鉄と革の束）を真似て、剣の大目標に食料と鉄の中目標を書き、3 回差し戻されて 40 秒
        # 無駄になった（2026-10-03）
        if activity.goal is not None and activity.goal.objective_id != current.id:
            activity = replace(activity, goal=None, intent="")

        async def write() -> None:
            written.append(
                await self._planner.plan(  # type: ignore[union-attr]
                    session,
                    activity,
                    replan_reason=replan,
                    keep_done_steps=" died and " not in reason,
                )
            )

        await self._while_deciding(write(), after_failure=failed)
        return bool(written and written[0])

    async def _while_deciding(self, deciding: Awaitable[None], after_failure: bool = False) -> None:
        """
        目標の決定を待つ間の最低限の安全策: 飢えそうで、ブリッジの候補に食べる物があれば食べる。
        移動や作業は始めない（目標の決定と競わない）。考え始めたことと考え終えたことは知らせる
        （実況の定型の一言、ボードの「考え中」）。
        """
        await self._event_publisher.publish(GoalDecidingEvent(after_failure=after_failure))
        task = asyncio.ensure_future(deciding)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=MEANWHILE_SECONDS)
                if done:
                    task.result()
                    return
                await self._eat_if_starving()
        finally:
            if not task.done():
                task.cancel()
            await self._event_publisher.publish(GoalDecidedEvent())

    async def _eat_if_starving(self) -> None:
        try:
            obs = await self._bridge.observe()
            if obs.food > HUNGER_URGENT:
                return
            eat = next(
                (c for c in obs.candidates if c.description.get("verb") == "eat"), None
            )
            if eat is None:
                return
            result = await self._bridge.act(eat.action_id)
            logger.info(f"目標の決定を待つ間に食べた: {eat.action_id} → {result.result}")
        except GameBridgeError as e:
            logger.warning(f"目標の決定を待つ間に食べられなかった: {e}")

    async def _members_met(self, session: PlaySession, obs: GameObservation) -> None:
        """束の条件が 1 つ済んだら伝える（束全体はまだ。docs/design/44）。"""
        met = session.newly_met_members(obs)
        if not met or session.goal is None or obs.goal is None:
            return
        left = tuple(m.label for m in obs.goal.members if not m.met)
        for member in met:
            logger.info(f"束の条件が済んだ: {member}（残り {', '.join(left)}）")
            await self._event_publisher.publish(
                GoalMemberMetEvent(goal=session.goal.describe(), member=member, left=left)
            )

    def _undesignable_now(self) -> list[str]:
        """さっき設計できなかった建物（UNDESIGNABLE_SECONDS の間は足さない）。"""
        now = self._clock()
        self._undesignable = {n: t for n, t in self._undesignable.items() if t > now}
        return list(self._undesignable)

    def _needs_gemini(self, session: PlaySession, reason: str) -> str | None:
        """小目標を Gemini に決めさせる理由（None なら Jev が選ぶ）。docs/design/26 §2。"""
        if self._chooser is None:
            return "small goals are decided by Gemini (minecraft.agent.small_goals: gemini)"
        if " died and " in reason:
            return f"the streamer died: the situation changed ({reason})"
        if OBJECTIVE_STALLED in reason:
            return f"the objective is not progressing ({reason})"
        if " is reconsidered: " in reason:
            return f"the last goal did not work out ({reason})"
        if any(k in reason for k in (" is stuck ", " stalled ")):
            if self._failure_routing != "jev":
                return f"the last goal did not work out ({reason})"
            same = _same_failures(session) if session.recent_goals else 0
            if same >= MAX_SAME_FAILURES:
                return f"the same kind of goal failed {same} times in a row ({reason})"
            # 種類が交互でも、続けて失敗していれば考え直す（A→B→A→B と行き来しない）
            failed = _failures_in_a_row(session)
            if failed >= MAX_FAILURES_BEFORE_GEMINI:
                return f"{failed} small goals in a row did not work out ({reason})"
        current = session.plan.current
        if current is None:
            return "there is no objective"
        if not current.open_jobs:
            return f"the objective {current.title} has no jobs yet"
        if self._last_gemini_at is not None:
            elapsed = self._clock() - self._last_gemini_at
            changed = tuple(g.id for g in session.plan.pending) != self._last_plan
            if elapsed >= self._replan_seconds and (changed or elapsed >= 3 * self._replan_seconds):
                return f"periodic re-planning ({elapsed / 60:.0f} minutes)"
        return None

    async def _end_goal(self, session: PlaySession, obs: GameObservation, reason: str) -> None:
        met = obs.goal is not None and obs.goal.met
        outcome = session.end_goal(reason, met)
        if outcome is None:
            return
        goal = outcome.goal
        self._record_goal_end(session, obs, reason)
        await self._event_publisher.publish(
            GoalEndedEvent(
                goal=goal.describe(),
                reason=goal.reason,
                ended_because=reason,
                met=met,
            )
        )

    def _record_goal_end(self, session: PlaySession, obs: GameObservation, reason: str) -> None:
        """終わる小目標の成績を 1 件残す（新しい小目標が歩みの数えを消す前に）。計測のためにプレイは止めない。"""
        if self._goal_end_recorder is None or session.goal is self._goal_end_recorded:
            return
        self._goal_end_recorded = session.goal
        try:
            self._goal_end_recorder.record(
                goal_end_entry(session, obs, reason, datetime.now(timezone.utc))
            )
        except Exception as e:
            logger.warning(f"小目標の終わりの記録を作れなかった: {type(e).__name__}: {e}")

    async def _facility_memory(
        self, plan: ObjectivePlan
    ) -> tuple[str, tuple[tuple[str, ...], tuple[float, float] | None] | None]:
        """
        目標の決定に見せる「作った施設」と、そのときの印（docs/design/142）。大目標に施設の条件があり、
        リストか家が前に見せたときから変わったときだけ文を返す（毎回は見せない）。見せなければ空の文。
        """
        if not any(c.predicate == GoalPredicate.FACILITY for g in plan.pending for c in g.conditions):
            return "", None
        try:
            facilities = await self._bridge.facilities()
        except AILoveShenError as e:
            logger.debug(f"施設の一覧を読めなかった: {e}")
            return "", None
        seen = (tuple(g.id for g in plan.pending), home_signature(facilities))
        if seen == self._facilities_seen:
            return "", seen
        return describe_memory(facilities), seen

    async def _decide_goal(
        self,
        session: PlaySession,
        obs: GameObservation,
        activity: Activity,
        reason: str,
        situation: Optional[dict[str, str]] = None,
        rethinks_seen: Optional[int] = None,
    ) -> None:
        plan = session.plan
        predicates = predicates_now(obs, plan, tools=self._control == "tools")
        messages = _for_goal(self._conversation.recent_messages(self._history_limit))
        goals = _shown_goals(activity, reason)
        viewers = _viewers(messages)
        # 行き詰まった・進まなかった・画面で考え直すことになった後は、深く考え直す（19 §7）。
        # 画面も添える（23 §2）
        failed = any(
            k in reason
            for k in (
                " is stuck ",
                " stalled ",
                " is reconsidered: ",
                " died and ",
                OBJECTIVE_STALLED,
            )
        )
        purpose = "goal_after_failure" if failed else "goal"
        # 行き詰まった・進まなかったときは、原因を分析してから決める（docs/design/27）
        # 死んだときも分析させる（原因と、同じ死に方をしないための教訓。docs/design/105: 今まで死んだときは
        # 分析も教訓も書かせず、96 回死んで教訓が 0 件だった）
        died_goal = (
            session.recent_goals[-1].goal.spec
            if " died and " in reason and session.recent_goals
            else None
        )
        failed_goal = (
            session.recent_goals[-1].goal.spec
            if (_no_progress(reason) or died_goal is not None) and session.recent_goals
            else None
        )
        same_failures = _same_failures(session) if failed_goal and died_goal is None else 0
        # 死んだときは、死んだ小目標をそのまま再開させない（持ち物が消えている。2026-10-01: 死んだ直後に
        # 同じ have(raw_iron, 3) を決め、また同じ地下へ戻った）。やり直し（retry）は選べない
        # 大目標が進まないときは、同じやり方のやり直しを出さない（別のやり方に変える）
        retry_allowed = (
            same_failures < MAX_SAME_FAILURES
            and OBJECTIVE_STALLED not in reason
            and died_goal is None
        )
        death_report = describe_death(obs.state.get("last_death")) if died_goal is not None else ""
        record = tuple(self._goal_steps) if failed_goal else ()
        offered = self._offered if failed_goal else ()
        images: list[Screenshot] = []
        if failed and self._screen is not None:
            shot = await self._screen.after_failure()
            images = [shot] if shot else []
        job_status = await self._job_status(plan)
        facility_memory, facilities_now = await self._facility_memory(plan)
        plan_owns_jobs = plan.current is not None and plan.current.planned
        error = ""
        learned = False
        for attempt in range(1, self._max_goal_attempts + 1):
            prompt = self._prompt_builder.build_goal_prompt(
                blueprint=session.blueprint,
                activity=activity,
                goal_ended_because=reason,
                recent_messages=messages,
                predicates=predicates,
                previous_error=error,
                failure_record=record,
                offered=offered,
                retry_allowed=retry_allowed,
                job_status=job_status,
                death_report=death_report,
                facility_memory=facility_memory,
            )
            schema = goal_schema(
                predicates,
                [g.id for g in plan.pending],
                [n.id for n in session.notebook.notes],
                len(goals),
                viewers,
                after_failure=failed_goal is not None,
                retry_allowed=retry_allowed,
                plan_owns_jobs=plan_owns_jobs,
                died=died_goal is not None,
            )
            data = await self._text_generator.generate_json(
                prompt,
                schema,
                system_instruction=self._prompt_builder.build_goal_system(),
                purpose=purpose,
                images=images,
            )
            dropped = ""  # 設計できずに落とした建物（差し戻すとき Gemini に伝える）
            chosen: Optional[GoalDecision] = None  # 差し戻す理由に、夜の言い添えをするため
            try:
                decision = parse_decision(data)
                chosen = decision
                # 教訓は過去の失敗の分析で、次の目標とは別: 目標が差し戻されても、書いたものは残す（死んだあと、持ち物を
                # 取り戻して「食料は足りている」と 3 回差し戻され、書かせた教訓が消えた: 2026-10-07）
                if not learned and decision.lesson:
                    self._learn(decision, situation or {}, "death" if died_goal is not None else "failure")
                    learned = True
                # 最後の試行は、プランが断る並べ替え・断念（定石の順を破る move など）を捨てて、決定の
                # 残りを通す（同じ編集を書き続けて、夜の野外で 4 分以上決まらなかった: 2026-10-03）
                if attempt >= self._max_goal_attempts and decision.changes:
                    decision = replace(
                        decision,
                        changes=self._objectives.without_refused(plan, decision.changes),
                    )
                # 計画済みなら中目標は計画が持つ: スキーマに欄がなくても、モデルが書いてきたら捨てる
                # （書き直されて計画の小目標と済みが消える。決定の残りは通す）
                if plan_owns_jobs and decision.jobs:
                    logger.warning("計画済みの大目標の中目標を決定が書いてきた: 捨てる")
                    decision = replace(decision, jobs=())
                # さっき設計できなかった建物は、しばらく足さない（毎回同じ建物を足して決定が
                # 通らなかった: 2026-09-27）
                for name in self._undesignable_now():
                    if _without_build(decision, name) != decision:
                        decision = _without_build(decision, name)
                        dropped = (
                            f" (the build or facility {name} could not be designed a moment ago: "
                            "do not add it again for a while; choose another objective)"
                        )
                if decision.spec.predicate not in predicates:
                    raise ValueError(
                        f"{decision.spec.predicate.value} is not one of the goals offered"
                    )
                # 束に入れる条件も、出している小目標から（家がないのに planted を束ねて、植林場がなく
                # 2 回続けて進まなかった: 2026-10-09 最初の家の材料集め）
                for extra in decision.together:
                    if extra.predicate not in predicates:
                        raise ValueError(
                            f"{extra.predicate.value} in together is not one of the goals offered "
                            "now: bundle only goals from the list"
                        )
                if failed_goal is not None:
                    _check_remedy(decision, failed_goal, same_failures)
                _check_time(decision, obs)
                if attempt < self._max_goal_attempts:
                    _check_survival_time(decision, obs)
                await self._check_job_order(decision.jobs, plan.current)
                if died_goal is not None and attempt < self._max_goal_attempts:
                    _check_after_death(decision, died_goal)
                # 最後の試行は通す（差し戻し続けて決定が失敗するより、細かい中目標のほうがまし）
                if attempt < self._max_goal_attempts:
                    await self._check_job_grain(decision.jobs, plan.current)
                    if decision.jobs:
                        check_plan(decision.jobs, plan.current, await self._built_names())
                try:
                    await self._objectives.check_new(plan, decision.changes)
                except BuildNotDesignedError as e:
                    # 建物を設計できなくても、決定の残り（空腹なら食べる、など）は通す: その建物の
                    # 大目標と中目標だけ落とす（2026-09-27: 餓死寸前に決定ごと差し戻していた）。施設も同じ
                    logger.warning(f"建物・施設 {e.name} を設計できないので、その大目標は足さない: {e}")
                    self._undesignable[e.name] = self._clock() + UNDESIGNABLE_SECONDS
                    dropped = (
                        f" (the build or facility {e.name} could not be designed just now and was "
                        "left out: choose another objective for now)"
                    )
                    decision = _without_build(decision, e.name)
                    await self._objectives.check_new(plan, decision.changes)
                _serving(decision, self._objectives.rehearse(plan, decision.changes))
                rehearsed = self._objectives.rehearse(plan, decision.changes)
                status = await self._bridge.set_goal(
                    decision.spec,
                    _kept(rehearsed),
                    _also(rehearsed, (decision.spec, *decision.together)),
                    together=decision.together,
                    level_areas=_level_areas(rehearsed, (decision.spec, *decision.together)),
                )
                if status.met:
                    # 決めた時点でもう済んでいる小目標は、何もせずに終わって次の決定を呼ぶだけ
                    # （2026-09-27: かまどを持っているのに have(furnace, 1) を決め、その中目標の石を掘りに行った）
                    raise ValueError(
                        f"the small goal {_bundle_label(decision)} is already met now"
                        f"{_carried_note(obs, decision)}; choose one that is not met yet "
                        "(see the jobs' state now)" + _stay_note(decision, obs) + _harvest_note(decision)
                    )
                if status.dead_end:
                    # 進める手がない小目標（畑がないのに farmed など）は、行き詰まるまで回さずに差し戻す
                    raise ValueError(
                        f"the small goal {_bundle_label(decision)} cannot progress now: "
                        f"{'; '.join(status.dead_end)}. Choose a small goal that can be done now "
                        "(what it needs first)" + _doable_note(job_status)
                    )
                # ここで初めて、今のままのプランに反映する（その間に返答が足しているかもしれない）
                preview = self._objectives.rehearse(plan, decision.changes)
                _serving(decision, preview)
                if decision.jobs and preview.current is not None:
                    # 書き直しが中目標の上限を破らないか（反映するときに落とさない）
                    preview.set_jobs(preview.current.id, decision.jobs)
                if (
                    self._chooser is not None
                    and self._planner is None
                    and preview.current is not None
                    and not preview.current.open_jobs
                    and not decision.jobs
                    and decision.serves == Serves.CURRENT
                ):
                    # 中目標がないと、次の切れ目でまた Gemini を呼ぶことになる（docs/design/26）。
                    # 身を守る小目標（夜に家へ戻るなど）は大目標のためではなく、どのみち終われば次の
                    # 切れ目で Gemini が決めるので、ここで中目標を書かせて差し戻さない
                    raise ValueError(
                        f"write the jobs for the objective at the top ({preview.current.title})"
                    )
            except (ValueError, GoalRejectedError) as e:
                error = str(e) + dropped + _night_note(chosen, obs)
                logger.warning(f"目標の決定 {attempt} を差し戻した: {data!r}: {error}")
                continue
            if decision.diagnosis:
                logger.info(
                    f"失敗の分析: {decision.diagnosis} → {decision.remedy.value if decision.remedy else '?'}"
                    + (f"（助言: {decision.advice}）" if decision.advice else "")
                )
            if decision.review:
                logger.info(
                    f"見直し: {decision.review}（大目標の編集 {len(decision.changes)}、"
                    f"中目標 {'書き直し' if decision.jobs else 'そのまま'}）"
                    + (f"; 外出ごと: {decision.trips}" if decision.trips else "")
                )
            await self._objectives.commit(plan, decision.changes)
            self._facilities_seen = facilities_now
            if decision.jobs and plan.current is not None:
                try:
                    await self._objectives.set_jobs(plan, plan.current.id, decision.jobs)
                except ValueError as e:
                    # 決定を確かめたあとに、返答が視聴者の中目標を足して上限に届いた: 書き直しは見送る
                    logger.warning(f"中目標の書き直しを見送った: {e}")
            self._write_notes(session.notebook, data, obs.day, goals, viewers)
            await self._start_goal(
                session, obs, _goal(decision, plan), reason, status, rethinks_seen
            )
            return
        if await self._spend_the_night(session, obs, reason, rethinks_seen):
            return
        if await self._take_a_doable_job(session, obs, reason, rethinks_seen, job_status):
            return
        raise TextGenerationError(
            f"no acceptable goal after {self._max_goal_attempts} attempts: {error}"
        )

    async def _spend_the_night(
        self,
        session: PlaySession,
        obs: GameObservation,
        reason: str,
        rethinks_seen: Optional[int],
    ) -> bool:
        """
        夕方・夜に、小目標が決まらないまま回り続けるのを、夜を越す小目標（through_night）で止める。
        夜のあいだ、決定が毎回差し戻され（進められない小目標を選び続ける）、行動が 1 つもないまま
        夜が明けた（2026-10-07 00:42〜00:52Z: 夜（朝まで 8 分）、ベッドが 0.7 m にあるのに、
        have(raw_iron, 3) を 3 回ずつ選び続けた）。through_night は家に帰り、眠れる時間になれば
        ベッドで寝る（家がなければ地下に潜る）。
        """
        if obs.time_phase not in ("dusk", "night"):
            return False
        spec = GoalSpec(GoalPredicate.THROUGH_NIGHT)
        try:
            status = await self._bridge.set_goal(spec, _kept(session.plan), _also(session.plan, (spec,)))
        except GoalRejectedError as e:
            logger.warning(f"夜を越す小目標をブリッジが断った: {e}")
            return False
        logger.warning(
            f"小目標が {self._max_goal_attempts} 回決まらなかった（{obs.time_phase}）: "
            "夜を越す小目標にする"
        )
        goal = Goal(
            spec=spec,
            reason=(
                f"it is {obs.time_phase} and no other small goal could be decided: "
                "spend the night at home (sleep in the bed)"
            ),
        )
        await self._start_goal(session, obs, goal, reason, status, rethinks_seen)
        return True

    async def _take_a_doable_job(
        self,
        session: PlaySession,
        obs: GameObservation,
        reason: str,
        rethinks_seen: Optional[int],
        job_status: tuple[ConditionStatus, ...],
    ) -> bool:
        """
        決定が決まらないまま回り続けるのを、いま進められる中目標の次の一歩で止める。昼に、進められない
        小目標（手に入る手立てがない丸石）を選び続け、17 分以上行動が 0 件だった（2026-10-07 02:42Z〜: 松明の
        輪で石・石炭が「手立てなし」になっていた）。夜は `_spend_the_night` が先に受ける。
        進められる中目標がなければ、作り出さずに False（決定の失敗のまま）。
        """
        plan = session.plan
        current = plan.current
        if current is None:
            return False
        for spec in _doable_goals(job_status):
            try:
                status = await self._bridge.set_goal(spec, _kept(plan), _also(plan, (spec,)))
            except GoalRejectedError as e:
                logger.debug(f"進められる中目標をブリッジが断った: {spec.describe()}: {e}")
                continue
            if status.met or status.dead_end:
                continue
            logger.warning(
                f"小目標が {self._max_goal_attempts} 回決まらなかった: "
                f"進められる中目標 {spec.describe()} にする"
            )
            goal = Goal(
                spec=spec,
                reason=(
                    f"no small goal could be decided in {self._max_goal_attempts} attempts: "
                    "this job of the objective can progress now"
                ),
                objective_id=current.id,
            )
            await self._start_goal(session, obs, goal, reason, status, rethinks_seen)
            return True
        return False

    def _objective_stuck(self, session: PlaySession) -> str:
        """
        一番上の大目標が、そのための行動を objective_stall_steps 続けても進んでいなければ、その理由
        （小目標は「進まない」で区切られても、同じ小目標に戻って回り続けることがあった）。
        進んだとするのは、中目標が 1 つ済んだとき、条件の進み具合の数（例: log (3/20) の 3）が変わった
        とき、今の中目標の進み具合の数が変わったとき（中目標を書き直して今の中目標が変わっただけでは
        数え直さない。docs/design/50 §4）。
        """
        top = session.plan.current
        if top is None or not self._objective_stall_steps:
            self._objective_watch, self._objective_idle_steps = _ObjectiveWatch(), 0
            return ""
        watch = _ObjectiveWatch.of(top)
        if self._objective_watch.progressed_by(watch):
            self._objective_watch, self._objective_idle_steps = watch, 0
            return ""
        self._objective_watch = watch  # 中目標を書き直して今の中目標が変わっただけなら、数え直さない
        if self._objective_idle_steps < self._objective_stall_steps:
            return ""
        steps, self._objective_idle_steps = self._objective_idle_steps, 0  # 考え直した後も同じだけ待つ
        return (
            f"the objective {top.title} {OBJECTIVE_STALLED} in {steps} actions for it "
            f"({'; '.join(top.summary()) or 'no progress shown'}). Do not try the same way again: "
            "change the approach: another source or method; grow what cannot be found (no trees "
            "nearby: a tree farm with facility(<name>) and planted, and do other work while they "
            "grow; no food: a farm with facility(<name>) and farmed); explore far in a new "
            "direction with explored; or "
            "move this objective down or drop it with the reason"
        )

    async def _built_names(self) -> Optional[set[str]]:
        """登録済みの建物の名前（分からなければ None: 建物の名前は確かめない）。"""
        try:
            return {str(b.get("name")) for b in await self._bridge.builds()}
        except AILoveShenError as e:
            logger.debug(f"建物の一覧を読めなかった: {e}")
            return None

    async def _check_job_order(
        self, jobs: tuple[Job, ...], current: Optional[Objective] = None
    ) -> None:
        """
        中目標は順番どおりに進む前提なので、後の中目標がもう済んでいるのに前の中目標が残っていれば、その前の
        中目標はそこへ行くためのものでしかない（2026-09-27: かまどを持っているのに、中目標に「石 8 → かまど」を
        書き、石を掘りに行った）。判定できなければ確かめない。
        一度済みにした中目標（ラッチ。docs/design/50 §4）は数えない: 書き直しの中に残っていても、済みの
        まま残り、材料を使って今は満たしていなくても、順番の食い違いにしない。
        """
        if current is not None:
            jobs = tuple(
                s for s in jobs if not any(d.same_as(s) for d in current.done_jobs)
            )
        if len(jobs) < 2:
            return
        try:
            statuses = list(await self._bridge.check([x for s in jobs for x in s.specs]))
        except AILoveShenError as e:
            logger.debug(f"中目標を判定できなかった: {e}")
            return
        done = []
        for job in jobs:
            mine, statuses = statuses[: len(job.specs)], statuses[len(job.specs) :]
            done.append(all(c.met for c in mine))
        for j, job in enumerate(jobs):
            before = [jobs[i].describe() for i in range(j) if not done[i]]
            if done[j] and before:
                raise ValueError(
                    f"job {j + 1} {job.describe()} is already met now, but the jobs before it are "
                    f"not ({', '.join(before)}): the jobs are in order, so those only led to it; "
                    "drop them, or put the ones still needed after it"
                )

    async def _check_job_grain(
        self, jobs: tuple[Job, ...], current: Optional[Objective] = None
    ) -> None:
        """
        中目標が素材の採集そのもの（have(oak_log, 4) など）なら差し戻す: 小目標と同じ 1 回の遠征で、画面に
        同じ文が 2 つ並ぶ（2026-09-29: 中目標が have(raw_iron, 3)・have(wheat, 6)・have(oak_log, 4) で、
        小目標もそのまま。プロンプトの決まりを守らなかった）。次の一歩を出せるのは、素材を材料にする
        「作るもの」を中目標にしたとき。除くもの: 束（together。同じ外出でまとめて取る）、have 以外
        （stored など）、大目標の完了条件そのもの、もう書いてある中目標。判定できなければ確かめない。
        """
        known = list(current.jobs) if current is not None else []
        own = list(current.conditions) if current is not None else []
        single = [
            j
            for j in jobs
            if not j.together
            and j.spec.predicate == GoalPredicate.HAVE
            and not any(k.same_as(j) for k in known)
            and not any(c.same_kind(j.spec) for c in own)
        ]
        if not single:
            return
        try:
            statuses = await self._bridge.check([j.spec for j in single])
        except AILoveShenError as e:
            logger.debug(f"中目標の粒度を判定できなかった: {e}")
            return
        raw = [j for j, st in zip(single, statuses) if st.gather and not st.met]
        if not raw:
            return
        goals = "; ".join(c.describe() for c in own) or "the objective's conditions"
        raise ValueError(
            f"job {', '.join(j.describe() for j in raw)} is only gathering a raw material: it is the "
            "same trip as the small goal, so the job and the small goal would read the same. A job is what "
            "the material is for: a thing made from it, a tool, a stock of food, a part of the build "
            f"(this objective needs: {goals}), e.g. have(iron_pickaxe, 1) instead of have(raw_iron, 3). "
            "The trip to gather the material is chosen automatically as the next step. If several raw "
            "materials come from one trip, bundle them with together"
        )

    async def _job_status(self, plan: ObjectivePlan) -> tuple[ConditionStatus, ...]:
        """一番上の大目標の中目標を今判定する（見直しの材料。docs/design/31）。判定できなければ空。"""
        current = plan.current
        if current is None or not current.jobs:
            return ()
        try:
            # 束の中目標は条件ごとに（docs/design/44）。済みにした中目標は判定せず、済みのまま見せる
            open_specs = [x for s in current.open_jobs for x in s.specs]
            judged = iter(await self._bridge.check(open_specs) if open_specs else ())
            return tuple(
                ConditionStatus(spec, True) if s.state == JobState.DONE else next(judged)
                for s in current.jobs
                for spec in s.specs
            )
        except AILoveShenError as e:
            logger.debug(f"中目標を判定できなかった: {e}")
            return ()

    def _write_notes(
        self,
        notebook: Notebook,
        data: dict,
        day: int | None,
        goals: list[str],
        viewers: list[str],
    ) -> None:
        """
        メモの編集を反映する。使えない編集は飛ばす: メモのために目標の決定を
        やり直させない（メモを失うほうが、目標を決められないより軽い）。
        """
        try:
            self._notes.commit(notebook, parse_note_changes(data), day, goals, viewers)
        except ValueError as e:
            logger.warning(f"メモの編集を飛ばした: {data.get('note_changes')!r}: {e}")

    def _learn(
        self, decision: GoalDecision, situation: dict[str, str], source: str = "failure"
    ) -> None:
        """失敗の後の決定が書いた教訓を、教訓帳に書く（条件がなければ行き詰まった状況）。"""
        if self._lessons is None or not decision.lesson:
            return
        keys = clean_keys(decision.lesson_when)
        condition = decision.lesson_when.get("text")
        self._lessons.learn(
            decision.lesson,
            condition if isinstance(condition, str) else "",
            keys or situation,
            explicit=bool(keys),
            source=source,
        )

    async def _recall_for(self, goal: Goal, obs: GameObservation) -> tuple[Lesson, ...]:
        """小目標の始まりに、その状況で教訓を思い出す（実績の対象。docs/design/35 §4）。"""
        if self._lessons is None:
            return ()
        recalled = await self._lessons.recall(
            situation_of(goal.spec, obs, self._last_ended),
            context={"goal": goal.spec.describe(), "goal_reason": goal.reason, **_light_of(obs)},
            moment="start",
        )
        recalled = self._lessons.mark_used(recalled)
        self._in_play = recalled
        return recalled

    async def _start_goal(
        self,
        session: PlaySession,
        obs: GameObservation,
        goal: Goal,
        reason: str,
        status: GoalStatus,
        rethinks_seen: Optional[int] = None,
    ) -> None:
        recalled = await self._recall_for(goal, obs)
        if recalled:
            goal = replace(goal, lessons=tuple(n.text for n in recalled))
        session.lessons = recalled
        session.set_goal(goal, obs.time_phase, rethinks_seen)
        self._save_goal(session)
        self._goal_steps.clear()
        self._stagnation.reset()
        self._repetition.reset()
        session.stagnant_seconds = 0.0
        if self._stuck is not None:
            self._stuck.reset()
        # これから見えるもの（このイベントの語り、返答）は、終わった目標ではなく
        # 新しい目標の状態
        session.observe(replace(obs, goal=status))
        obj = session.plan.get(goal.objective_id) if goal.objective_id else None
        serves = f"（{obj.describe()} のため）" if obj else "（生存のため）"
        logger.info(
            f"目標: {goal.describe()}{serves}（{reason}） - {goal.reason}; "
            f"残り {status.remaining}" + (f"; 進め方: {goal.approach}" if goal.approach else "")
        )
        await self._event_publisher.publish(
            GoalSetEvent(
                goal=goal.describe(), reason=goal.reason, objective=obj.title if obj else ""
            )
        )


def _serving(decision: GoalDecision, plan: ObjectivePlan) -> None:
    """小目標が、言っている対象のためのものかを、編集後のプランで確かめる。"""
    if decision.serves == Serves.SURVIVAL:
        if not is_survival(decision.spec):
            raise ValueError(
                f"{decision.spec.describe()} is not a survival goal (through_night, at_home, "
                "cleared, have(food, n)); it must serve the objective at the top of the list"
            )
    elif plan.current is None:
        raise ValueError(
            "there is no objective left: add one toward the mission, or choose a survival goal"
        )


def _shown_goals(activity: Activity, ended_because: str) -> list[str]:
    """
    目標の決定で番号をつけて示す小目標（lesson の根拠）: これまでの小目標（古い順）と、
    今終わった小目標。番号は stream_context.format_activity(with_ids=True) と同じ。
    """
    goals = [f"{o.goal.describe()}: {o.ended_because}" for o in activity.recent_goals]
    if activity.goal is not None:
        goals.append(f"{activity.goal.describe()}: {ended_because}")
    return goals


def _viewers(messages: tuple[ConversationMessage, ...]) -> list[str]:
    """示した会話にいる視聴者（出てきた順）。"""
    names = [m.speaker_name for m in messages if m.role == MessageRole.VIEWER and m.speaker_name]
    return list(dict.fromkeys(names))


def _goal(decision: GoalDecision, plan: ObjectivePlan) -> Goal:
    current = plan.current
    objective_id = current.id if decision.serves == Serves.CURRENT and current else None
    return Goal(
        spec=decision.spec,
        reason=decision.reason,
        objective_id=objective_id,
        diagnosis=decision.diagnosis,
        advice=decision.advice,
        review=decision.review,
        together=decision.together,
        approach=decision.approach,
    )


def _light_of(obs: GameObservation) -> dict[str, Any]:
    """教訓が今に当てはまるかを Jev が確かめる手がかり: 暗いか、松明を何本持っているか（「暗い所では松明を」の教えが当てはまると答えられなかった）。"""
    inventory = obs.state.get("inventory") or {}
    return {
        "dark_here": any("dark here" in n for n in obs.needs),
        "torches_carried": int(inventory.get("torch") or 0),
    }


def _upcoming(plan: ObjectivePlan) -> list[dict[str, str]]:
    """一番上の大目標の中目標（これからやること）の状況のキー（教訓を思い出す手がかり）。"""
    current = plan.current
    if current is None:
        return []
    return [situation_of(spec, None) for job in current.open_jobs for spec in job.specs]


def _no_progress(reason: str) -> bool:
    """小目標が行き詰まったか進まなかったか（原因を分析する失敗。docs/design/27）。"""
    return " is stuck " in reason or " stalled " in reason or OBJECTIVE_STALLED in reason


OBJECTIVE_STALLED = "has made no progress"
# 計画を書き直す理由になる、小目標の終わり方（目標の決定が「失敗の後」と見るものと同じ）
REPLAN_REASONS = (" is stuck ", " stalled ", " is reconsidered: ", " died and ", OBJECTIVE_STALLED)
PLAN_INTERVAL_SECONDS = 300.0  # 同じ大目標の計画を書く（書こうとする）間隔
PLAN_RECORD_SHOWN = 6  # 書き直すときに見せる、直前の行動の数
_PROGRESS = re.compile(r"\((\d+)/\d+\)")


def _progress_numbers(goal: Objective) -> tuple[int, ...]:
    """大目標の進み具合の数（条件ごとの「(3/20)」の 3）。行き方の説明は変わっても数えない。"""
    return tuple(int(m) for line in goal.summary() for m in _PROGRESS.findall(line))


def _job_numbers(job: Optional[Job]) -> tuple[int, ...]:
    """中目標の進み具合の数（条件ごとの「(3/20)」の 3）。"""
    if job is None:
        return ()
    return tuple(
        int(m)
        for line in job.progress
        if not line.startswith(" ")
        for m in _PROGRESS.findall(line)
    )


@dataclass(frozen=True)
class _ObjectiveWatch:
    """一番上の大目標が進んでいるかの目安（docs/design/50 §4）。"""

    goal_id: Optional[str] = None
    done: int = 0
    numbers: tuple[int, ...] = ()
    job_id: str = ""
    job_numbers: tuple[int, ...] = ()
    steps: int = 0  # 計画の小目標を済みにした数（docs/design/50 §18）

    @staticmethod
    def of(goal: Objective) -> "_ObjectiveWatch":
        job = next(iter(goal.open_jobs), None)
        return _ObjectiveWatch(
            goal.id,
            len(goal.done_jobs),
            _progress_numbers(goal),
            job.id if job else "",
            _job_numbers(job),
            sum(len(j.steps_done) for j in goal.jobs),
        )

    def progressed_by(self, now: "_ObjectiveWatch") -> bool:
        """前より進んだ（別の大目標になった、中目標が済んだ、小目標が済んだ、数が変わった）か。"""
        if (now.goal_id, now.done, now.numbers, now.steps) != (
            self.goal_id,
            self.done,
            self.numbers,
            self.steps,
        ):
            return True
        # 中目標の id が同じで進み具合の数が変わったときだけ。中目標が変わっただけは進んだとしない
        return now.job_id == self.job_id and now.job_numbers != self.job_numbers


def _same_failures(session: PlaySession) -> int:
    """最後に終わった小目標と同じ種類の小目標が、続けて何回失敗で終わったか。"""
    recent = session.recent_goals
    last = recent[-1].goal.spec
    n = 0
    for outcome in reversed(recent):
        if not (_no_progress(outcome.ended_because) and outcome.goal.spec.same_kind(last)):
            break
        n += 1
    return n


def _without_build(decision: GoalDecision, name: str) -> GoalDecision:
    """
    その建物か施設（built(name) / facility(name)）を条件に持つ大目標の追加と、それを作る中目標を
    除いた決定。
    """

    def builds_it(spec: GoalSpec) -> bool:
        return spec.predicate in (GoalPredicate.BUILT, GoalPredicate.FACILITY) and spec.name == name

    changes = tuple(
        c
        for c in decision.changes
        if c.proposal is None or not any(builds_it(s) for s in c.proposal.conditions)
    )
    jobs = tuple(s for s in decision.jobs if not any(builds_it(x) for x in s.specs))
    together = tuple(s for s in decision.together if not builds_it(s))
    approach = decision.approach if together else ""
    return replace(decision, changes=changes, jobs=jobs, together=together, approach=approach)


def _failures_in_a_row(session: PlaySession) -> int:
    """最後から続けて、行き詰まった・進まなかった小目標の数（種類は問わない）。"""
    n = 0
    for outcome in reversed(session.recent_goals):
        if not _no_progress(outcome.ended_because):
            break
        n += 1
    return n


def _bundle_label(decision: GoalDecision) -> str:
    return " + ".join(s.describe() for s in (decision.spec, *decision.together))


def _stay_note(decision, obs: GameObservation) -> str:
    """
    夕方・夜に、もう家の中にいて `at_home` を選んだ差し戻しに添える: 安全のためにここにいたいのなら、
    選ぶのは through_night（夜が明けるまで家で過ごし、眠れる時間になればベッドで寝る）。`at_home` は
    済んでいるので通らず、3 回目に外の作業が通って外へ出て死んだ（2026-09-30 18:08）。
    """
    if (
        decision.spec.predicate == GoalPredicate.AT_HOME
        and obs.inside_home
        and obs.time_phase in ("dusk", "night")
    ):
        return (
            ". You are already inside the home: to stay safe here choose through_night "
            "(it waits in the home until morning and sleeps in the bed when it is time), "
            "not an outdoor task"
        )
    return ""


def _harvest_note(decision: GoalDecision) -> str:
    """
    farmed(crop, n) は畑に植わっている数で、収穫の有無は見ない。実った作物を刈りたくて farmed を選び、
    「済んでいる」と差し戻され続けた（2026-10-09: 小麦 64/64 が実っているのに farmed(wheat, 64) を
    2 回選び、3 回目に昼の through_night が通った）。収穫は have(crop, n)。
    """
    if decision.spec.predicate != GoalPredicate.FARMED:
        return ""
    crop = decision.spec.item or "wheat"
    return (
        f". farmed counts crops planted on the farms, not harvested: to harvest the ripe {crop}, "
        f"choose have({crop}, n) (n is the total you want to carry)"
    )


def _doable_goals(job_status: tuple[ConditionStatus, ...]) -> tuple[GoalSpec, ...]:
    """
    中目標の条件のうち、いま進められるものの小目標（済みでなく、手に入る手立てがある）。集めに出る
    材料があれば、その次の一歩（have）、なければ条件そのもの。
    """
    out: list[GoalSpec] = []
    for status in job_status:
        if status.met or status.impossible:
            continue
        spec = status.next_step or status.spec
        if spec not in out:
            out.append(spec)
    return tuple(out)


def _doable_note(job_status: tuple[ConditionStatus, ...]) -> str:
    """進められない小目標を差し戻すとき、いま進められる中目標を添える（選び直しの答え）。"""
    doable = _doable_goals(job_status)[:3]
    if not doable:
        return ""
    return ". Jobs that can progress now: " + ", ".join(s.describe() for s in doable)


def _night_note(decision: Optional[GoalDecision], obs: GameObservation) -> str:
    """
    夕方・夜に、夜を越す以外の小目標を選んで差し戻すとき、選び直しの答えを添える。差し戻しの理由
    （進められない、など）だけでは、時間帯に気づかず同じ種類を選び続けた（2026-10-07 00:42〜00:52Z）。
    """
    if decision is None or obs.time_phase not in ("dusk", "night"):
        return ""
    if decision.spec.predicate in (
        GoalPredicate.THROUGH_NIGHT,
        GoalPredicate.AT_HOME,
        GoalPredicate.CLEARED,
    ):
        return ""
    bed = "the bed in your home" if obs.has_home else "a bed or a hole in the ground"
    return (
        f". It is {obs.time_phase} now: if nothing outdoors can be done safely, choose "
        f"through_night (it goes home and sleeps in {bed} when it is time)"
    )


def _carried_note(obs: GameObservation, decision: GoalDecision) -> str:
    """
    have(item, n) は持っている合計が n 以上になること。「あと n 個」と取り違えた決定（11 個持っていて
    have(oak_log, 5)）を差し戻すとき、持っている数と書き直し方を添える。
    """
    held = obs.state.get("inventory") or {}
    notes = []
    for spec in (decision.spec, *decision.together):
        if spec.predicate != GoalPredicate.HAVE or not spec.item or spec.item not in held:
            continue
        notes.append(f"you already carry {held[spec.item]} {spec.item}")
    if not notes:
        return ""
    return (
        f" ({', '.join(notes)}; have(item, n) means n in total, not n more: to get more, "
        "write the total you want)"
    )


# 見直しの文が、今の時間帯と食い違う言い方（時間帯 → 言ってはいけないこと）。
# 2026-09-27: 夕方に「夜が明けたので外に出られる」と書いて、外の中目標を決めた
_NOT_MORNING = r"夜が明け(?!たら|る|れば|次第|ても)|朝になった(?!ら)|朝(?:だ|なの)(?:から|ので)"
_WRONG_TIME = {
    "day": re.compile(r"夜になった(?!ら)|夜(?:だ|なの)(?:から|ので)"),
    "dusk": re.compile(_NOT_MORNING),
    "night": re.compile(_NOT_MORNING + r"|昼間?(?:だ|なの)(?:から|ので)"),
}


def _check_time(decision: GoalDecision, obs: GameObservation) -> None:
    """見直しが今の時間帯を取り違えていれば差し戻す（読み直させる）。"""
    phase = obs.time_phase
    wrong = _WRONG_TIME.get(phase)
    for name, text in (("review", decision.review), ("reason", decision.reason)):
        found = wrong.search(text) if wrong and text else None
        if found:
            raise ValueError(
                f"the {name} says {found.group(0)!r}, but it is {phase} now: read the time in the "
                "state again and decide for that time"
            )


def _check_survival_time(decision: GoalDecision, obs: GameObservation) -> None:
    """
    through_night は夕方と夜の目標。昼や明け方に選んだ決定を差し戻す（2026-10-04 10:06: 昼なのに
    「家に入れない状態で夜が来るため」と through_night を選び、畑の中に穴を掘って潜った）。
    """
    if decision.spec.predicate == GoalPredicate.THROUGH_NIGHT and obs.time_phase in ("day", "dawn"):
        raise ValueError(
            f"it is {obs.time_phase} now, not night: through_night is for dusk and night. Read the "
            "time in the state. If you cannot get into the home, that is the problem to solve "
            "(its door, the way to it) with a small goal for the day, not a night goal"
        )


def _check_remedy(decision: GoalDecision, failed: GoalSpec, same_failures: int) -> None:
    """失敗の後の決定が、分析と対処の決まりに合うか（docs/design/27 §2）。"""
    if not decision.diagnosis or decision.remedy is None:
        raise ValueError("after a failure, write the diagnosis first and then the remedy")
    same = decision.spec.same_kind(failed)
    if same and same_failures >= MAX_SAME_FAILURES:
        raise ValueError(
            f"{failed.describe()} made no progress {same_failures} times in a row; "
            "choose a different small goal (remedy change)"
        )
    if same and not decision.advice:
        raise ValueError("retry needs advice: what the action chooser should do differently")


def _check_after_death(decision: GoalDecision, died: GoalSpec) -> None:
    """死んだあとの決定が、死んだ小目標の再開ではないか（小目標は決め直す）。"""
    if decision.spec.same_kind(died):
        raise ValueError(
            f"you died while working on {died.describe()}: what you carried is gone and the "
            "situation changed. Set a new small goal: do not resume it as it was. First prepare for "
            "what killed you or what you lost (a weapon, armor, torches, food, a tool), or choose a "
            "different goal; the job comes back after that"
        )


def _verb_of(outcome: Optional[ToolOutcome], candidates: Sequence[Candidate]) -> Optional[str]:
    """
    繰り返しを見つけるための、1 手の動詞。ソルバーの提案（do_suggestion）は候補の verb
    （till / sow など）にする。技と調べものは並びに数えない（None）。
    """
    if outcome is None:
        return None
    call = outcome.call
    if is_query(call.name) or is_skill_tool(call.name):
        return None
    if call.name != "do_suggestion":
        return call.name
    chosen = next((c for c in candidates if c.action_id == call.args.get("id")), None)
    return chosen.description.get("verb") if chosen else None


def _fitting_skill(skills: Sequence[SkillInfo], goal: Optional[Goal]) -> str:
    """今の小目標の品目に合う（expects の品目が同じ）、引数の要らない技の名前。なければ空。"""
    item = goal.spec.item if goal else None
    if item is None:
        return ""
    return next(
        (s.name for s in skills if s.expects.get("item") == item and not s.needs_arguments), ""
    )


def _step_line(decision: ActionDecision, result: ActionResult) -> str:
    """失敗の分析に見せる 1 ステップ: 選んだ行動、確信度、結果。"""
    outcome = "ok" if result.ok else "failed"
    return f"{result.action_id} ({decision.confidence:.2f}) → {outcome}: {result.result[:80]}"


_GATHERED = (GoalPredicate.HAVE, GoalPredicate.STORED, GoalPredicate.PLANTED, GoalPredicate.FARMED)


def _also(plan: ObjectivePlan, bundle: tuple[GoalSpec, ...]) -> tuple[GoalSpec, ...]:
    """
    ほかの大目標（と今の大目標の中目標）で集める物: そばで取れたら一緒に取る。今の小目標（束なら
    その条件すべて）と同じ物は除く（木を見つけたら原木と、ついでに葉から苗木）。
    """
    specs = [c for g in plan.pending for c in g.conditions]
    if plan.current is not None:
        specs += [x for s in plan.current.open_jobs for x in s.specs]
    items = {b.item for b in bundle}
    out: list[GoalSpec] = []
    for s in specs:
        if s.predicate in _GATHERED and s.item not in items and s not in out:
            out.append(s)
    return tuple(out)


def _level_areas(plan: ObjectivePlan, bundle: tuple[GoalSpec, ...]) -> tuple[GoalSpec, ...]:
    """
    整地する予定の土地: 今の小目標の束、大目標の条件、大目標の中目標（とその計画の小目標）の
    leveled。ブリッジは土や石を取るとき、この土地の床より下を掘らない（整地した地面の下が穴だらけになる）。
    """
    specs = list(bundle)
    for g in plan.pending:
        specs += g.conditions
        for job in g.open_jobs:
            specs += job.specs
            specs += [step.spec for step in job.steps]
    out: list[GoalSpec] = []
    for s in specs:
        if s.predicate == GoalPredicate.LEVELED and s not in out:
            out.append(s)
    return tuple(out)


def _kept(plan: ObjectivePlan) -> tuple[GoalSpec, ...]:
    """チェストが大目標のために取っておくもの: その stored() 条件。"""
    return tuple(
        c for g in plan.pending for c in g.conditions if c.predicate == GoalPredicate.STORED
    )


GOAL_COMMENTARY = 3


def _for_goal(messages: Sequence[ConversationMessage]) -> tuple[ConversationMessage, ...]:
    """目標の決定に渡す会話: チャットと返答は全部、実況は直近の数件だけ（26 §4）。"""
    commentary = [m for m in messages if m.message_type == MessageType.COMMENTARY]
    keep = set(map(id, commentary[-GOAL_COMMENTARY:]))
    return tuple(m for m in messages if m.message_type != MessageType.COMMENTARY or id(m) in keep)
