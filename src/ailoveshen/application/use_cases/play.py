"""プレイのユースケース: LLM が設計して目標を決め、ブリッジが判定し、選択器が遊ぶ。"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Optional

from loguru import logger

from ailoveshen.application.dto.game_dto import PlayStepReport
from ailoveshen.application.ports.input.play import IAdvancePlay, IStartPlay
from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.goal_chooser import ChosenGoal, GoalChooser
from ailoveshen.application.use_cases.goal_vocabulary import (
    GoalDecision,
    Remedy,
    Serves,
    goal_schema,
    parse_decision,
    parse_note_changes,
    predicates_now,
)
from ailoveshen.application.use_cases.house import HouseDesigner, parse_blueprint
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.skills import NotWritten, SkillWriter, order_skills
from ailoveshen.application.use_cases.tool_catalog import (
    ACTION_TOOLS,
    LOOK_SCREEN,
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
from ailoveshen.domain.entities import Conversation, MidGoalPlan, Notebook, PlaySession
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    SkillLearnedEvent,
    SkillRevisedEvent,
    GoalEndedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
)
from ailoveshen.domain.exceptions import GameBridgeError, GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    Activity,
    Candidate,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    MessageRole,
    MessageType,
    Screenshot,
    SkillInfo,
    SkillRun,
    ToolCall,
    ToolOutcome,
    is_survival,
)

CONTROLS = ("candidates", "tools")
MAX_QUERIES_PER_STEP = 3  # 1 ステップの中で調べものをしてよい回数
SUGGESTIONS_SHOWN = 12  # 道具の選択に見せるソルバーの提案の数
RECENT_TOOLS = 8  # 道具の選択に見せる直近の呼び出しの数
GOAL_STEPS_SHOWN = 12  # 失敗の分析に見せる、その小目標の行動の数（docs/design/27）
OFFERED_SHOWN = 12  # 失敗の分析に見せる、最後に出ていた候補の数
MAX_SAME_FAILURES = 2  # 同じ小目標がこの回数続けて失敗したら、やり直しは選べない


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

    保存したプランが同じ大目標のものなら、大目標と中目標はそこから続ける。そうでなければ、
    設定から作ったプランで始める。大目標の街は、プランにまだなければ定め（以後は保つ）、
    未解決の段階は今の能力で書き直す。
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        designer: HouseDesigner,
        plan: MidGoalPlan,
        store: IMissionStore,
        town: TownPlanner,
        notes: NoteKeeper,
        max_steps_per_goal: int = 40,
        max_consecutive_failures: int = 3,
        max_stalled_steps: int = 8,
    ) -> None:
        """
        依存を受け取ってユースケースを初期化する（依存性の注入）。

        Args:
            bridge: Minecraft ブリッジのアダプター
            event_publisher: ドメインイベントの発行器
            designer: 最初の家を設計する
            plan: 設定から作った、大目標と最初の中目標
            store: 再起動をまたいでプランを保つ
            town: 大目標の街を定め、実現できる形に保つ
            notes: 自分のメモを再起動をまたいで戻す
            max_steps_per_goal: LLM に新しい目標を求めるまでのステップ数
            max_consecutive_failures: 新しい目標を求めるまでに、続けて失敗するステップ数
            max_stalled_steps: 新しい目標を求めるまでに、進まないステップ数
        """
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
            return session
        blueprint = await self._designer.design()
        await self._event_publisher.publish(
            HouseDesignedEvent(name=blueprint.name, concept=blueprint.concept)
        )
        await self._bridge.set_build_plan(blueprint)
        return self._session(blueprint, plan, notebook)

    def _session(
        self, blueprint: HouseBlueprint | None, plan: MidGoalPlan, notebook: Notebook
    ) -> PlaySession:
        return PlaySession(
            blueprint=blueprint,
            plan=plan,
            notebook=notebook,
            max_steps_per_goal=self._max_steps_per_goal,
            max_consecutive_failures=self._max_consecutive_failures,
            max_stalled_steps=self._max_stalled_steps,
        )

    def _load_plan(self) -> MidGoalPlan:
        plan = self._plan
        saved = self._store.load()
        if saved is not None and saved.mission == plan.mission:
            plan.restore(
                list(saved.pending),
                list(saved.finished),
                saved.next_id,
                town=saved.town,
                town_stage=saved.town_stage,
                stage_met=saved.stage_met,
                site=saved.site,
            )
            logger.info(f"中目標を引き継いだ: {', '.join(g.describe() for g in plan.pending)}")
        else:
            if saved is not None:
                logger.info(f"大目標が「{saved.mission.text}」から変わった: 中目標は最初からにする")
            self._store.save(plan)
        return plan


class AdvancePlayUseCase(IAdvancePlay):
    """
    ユースケース: プレイセッションの 1 ステップ。

    1. 観測する。ブリッジが塞がっている間（反射が近くの脅威に対処している）は、
       何もしない。家の完成は一度だけ伝える
    2. 新しい小目標が要るとき（目標がない、達成した、その中目標が終わった、
       行き詰まった、進まない、長すぎる、時間帯が変わった）は、まず中目標を世界から
       判定し（済んだものは完了にする）、次に今の目標を終わらせて（GoalEndedEvent）、
       LLM が次の目標を決める。LLM は大目標、中目標、配信者が今していること、最近の
       会話を見る。同時に LLM は中目標リストを編集できる（追加、移動、理由つきの
       断念。プランの上限は守る）。小目標は、編集後のリストの一番上の中目標のため
       か、生存のためのもの。使えない目標や編集は、理由をつけて LLM に戻す
    3. 行動選択器（Jev）が、ブリッジが目標と体の必要から具体化した候補を 1 つ選ぶ。
       候補が 1 つだけならモデルを呼ばずにそれを取る
    4. ブリッジがそれを実行し、セッションはそのステップを目標とその中目標の分として
       数える（予算を超えた視聴者の頼みは断念し、そのことを伝える）

    小目標を変えるのはこのループだけ: チャットの返答は並行して走り、視聴者の中目標を
    今の中目標の後ろに足すことはあるが、割り込むことはない。
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        action_selector: IActionSelector,
        event_publisher: IEventPublisher,
        conversation: Conversation,
        mid_goals: MidGoalKeeper,
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
            mid_goals: 中目標を判定し、編集し、保存する（チャットの返答と共有する）
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
            chooser: 小目標を、Gemini が書いた手順の中から Jev に選ばせる（docs/design/26）。
                None なら毎回 Gemini が決める（今までどおり）
            replan_minutes: 手順があっても Gemini に見直させる間隔（中目標リストが変わって
                いなければ、その 3 倍まで待つ）
            clock: 時計（テストで差し替える）
            skills: 技を書くもの（docs/design/22）。"tools" のとき、技の道具（run_skill /
                write_skill）と技の一覧を出す。None なら技を使わない

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
        self._mid_goals = mid_goals
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
        self._clock = clock
        self._last_gemini_at: float | None = None
        self._last_plan: tuple[str, ...] = ()  # 前の Gemini の決定のときの中目標の並び
        # 今の小目標の行動の記録と、最後に出ていた候補（失敗の分析に見せる。docs/design/27）
        self._goal_steps: deque[str] = deque(maxlen=GOAL_STEPS_SHOWN)
        self._offered: tuple[Candidate, ...] = ()
        self._skills = skills if control == "tools" else None
        self._skill_failures: dict[str, str] = {}  # 技の名前 -> 最後の失敗（直すときに見せる）

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

        session.track_progress(obs)
        if self._screen is not None:
            # 定期の見直し。「考え直す」なら、下の判定で今の小目標が終わる
            await self._screen.review_if_due(session)
        goal_changed = False
        if session.needs_new_goal(obs):
            await self._change_goal(session, obs)
            goal_changed = True
            obs = await self._bridge.observe()
            session.observe(obs)

        goal = session.goal
        assert goal is not None
        self._offered = obs.candidates[:OFFERED_SHOWN]
        if self._control == "tools":
            decision, result = await self._act_with_tools(session, obs)
        else:
            decision, result = await self._act_on_candidate(goal, obs)
        await self._mid_goals.step_counted(session.plan, session.record(result))
        self._goal_steps.append(_step_line(decision, result))
        await self._event_publisher.publish(
            GameActionExecutedEvent(
                action_id=result.action_id,
                ok=result.ok,
                result=result.result,
                confidence=decision.confidence,
            )
        )
        return PlayStepReport(
            goal=goal,
            goal_changed=goal_changed,
            status=obs.goal,
            decision=decision,
            result=result,
            house_complete=obs.house_complete,
        )

    async def _act_on_candidate(
        self, goal: Goal, obs: GameObservation
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
        return decision, await self._bridge.act(decision.action_id)

    async def _act_with_tools(
        self, session: PlaySession, obs: GameObservation
    ) -> tuple[ActionDecision, ActionResult]:
        """
        LLM が道具を呼ぶ（設計書 21 §2）。調べものの道具はすぐ結果を返し、同じステップの中で
        選び直す（MAX_QUERIES_PER_STEP 回まで。その後は行動の道具だけを出す）。行動の道具は
        見張りつきで実行し、1 ステップはそれで終わる。
        """
        specs = tool_specs(can_look=self._screen is not None, skills=self._skills is not None)
        action_specs = [t for t in specs if t.name in ACTION_TOOLS or is_skill_tool(t.name)]
        skills = await self._skill_list(session) if self._skills is not None else None
        images: list[Screenshot] = []  # 次の選択にだけ添える画面（ステップをまたがない）
        last = self._recent_tools[-1] if self._recent_tools else None
        if self._screen is not None and last is not None and not last.ok:
            shot = await self._screen.after_failure()
            images = [shot] if shot else []
        for attempt in range(MAX_QUERIES_PER_STEP + 1):
            state = await self._bridge.state()
            prompt = self._prompt_builder.build_tool_prompt(
                session.activity(),
                state,
                obs.candidates[:SUGGESTIONS_SHOWN],
                tuple(self._recent_tools),
                skills=skills,
            )
            last = self._recent_tools[-1] if self._recent_tools else None
            purpose = "tool_after_failure" if last is not None and not last.ok else "tool"
            offered = specs if attempt < MAX_QUERIES_PER_STEP else action_specs
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
            if call.name == LOOK_SCREEN:
                shot = await self._screen.look() if self._screen is not None else None
                images = [shot] if shot else []
                result = "the screen is attached to this prompt" if shot else "could not capture"
                self._recent_tools.append(
                    ToolOutcome(call=call, ok=shot is not None, result=result, seconds=0.0)
                )
                continue
            if is_query(call.name):
                ok, result, seconds, refused = await self._bridge.run_tool(call.name, call.args)
                self._recent_tools.append(
                    ToolOutcome(call=call, ok=ok, result=result, seconds=seconds, refused=refused)
                )
                logger.info(f"調べた: {call.describe()} -> {result[:200]}")
                continue
            session.set_intent(call.intent)
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
        decision = ActionDecision(action_id=outcome.call.describe(), confidence=1.0)
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
            else:
                self._skill_failures[name] = run.describe()
            if run.learned:
                await self._event_publisher.publish(
                    SkillLearnedEvent(name=name, description=description)
                )
        return outcome

    async def _change_goal(self, session: PlaySession, obs: GameObservation) -> None:
        reason = session.goal_end_reason(obs)
        # 決める前に判定する: もう済んでいる中目標（前の実行で建てた家）を、次の目標の
        # 対象にしてはいけない
        await self._mid_goals.judge(session.plan)
        await self._town.advance(session, obs)
        self._notes.expire(session.notebook, obs.day)
        # 目標が終わる前の見え方: その状態が、終わる理由だ
        activity = session.activity()
        await self._end_goal(session, obs, reason)
        why = self._needs_gemini(session, reason)
        if why is None:
            assert self._chooser is not None
            picked = await self._chooser.choose(session, obs, reason)
            if isinstance(picked, ChosenGoal):
                try:
                    status = await self._bridge.set_goal(picked.spec, _kept(session.plan))
                except GoalRejectedError as e:
                    why = f"the bridge rejected {picked.spec.describe()}: {e}"
                else:
                    goal = Goal(
                        spec=picked.spec, reason=picked.reason, mid_goal_id=picked.mid_goal_id
                    )
                    logger.info(
                        f"小目標は Jev が選んだ（確信度 {picked.confidence:.2f}）: "
                        f"{picked.spec.describe()}"
                    )
                    await self._start_goal(session, obs, goal, reason, status)
                    return
            else:
                why = picked.why
        logger.info(f"小目標は Gemini が決める: {why}")
        await self._decide_goal(session, obs, activity, reason)
        self._last_gemini_at = self._clock()
        self._last_plan = tuple(g.id for g in session.plan.pending)

    def _needs_gemini(self, session: PlaySession, reason: str) -> str | None:
        """小目標を Gemini に決めさせる理由（None なら Jev が選ぶ）。docs/design/26 §2。"""
        if self._chooser is None:
            return "small goals are decided by Gemini (minecraft.agent.small_goals: gemini)"
        if any(k in reason for k in (" is stuck ", " stalled ", " is reconsidered: ")):
            return f"the last goal did not work out ({reason})"
        current = session.plan.current
        if current is None:
            return "there is no mid goal"
        if not current.plan_steps:
            return f"the mid goal {current.title} has no steps yet"
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
        await self._event_publisher.publish(
            GoalEndedEvent(
                goal=goal.spec.describe(),
                reason=goal.reason,
                ended_because=reason,
                met=met,
            )
        )

    async def _decide_goal(
        self, session: PlaySession, obs: GameObservation, activity: Activity, reason: str
    ) -> None:
        plan = session.plan
        predicates = predicates_now(obs, plan)
        messages = _for_goal(self._conversation.recent_messages(self._history_limit))
        goals = _shown_goals(activity, reason)
        viewers = _viewers(messages)
        # 行き詰まった・進まなかった・画面で考え直すことになった後は、深く考え直す（19 §7）。
        # 画面も添える（23 §2）
        failed = any(k in reason for k in (" is stuck ", " stalled ", " is reconsidered: "))
        purpose = "goal_after_failure" if failed else "goal"
        # 行き詰まった・進まなかったときは、原因を分析してから決める（docs/design/27）
        failed_goal = (
            session.recent_goals[-1].goal.spec
            if _no_progress(reason) and session.recent_goals
            else None
        )
        same_failures = _same_failures(session) if failed_goal else 0
        retry_allowed = same_failures < MAX_SAME_FAILURES
        record = tuple(self._goal_steps) if failed_goal else ()
        offered = self._offered if failed_goal else ()
        images: list[Screenshot] = []
        if failed and self._screen is not None:
            shot = await self._screen.after_failure()
            images = [shot] if shot else []
        error = ""
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
            )
            schema = goal_schema(
                predicates,
                [g.id for g in plan.pending],
                [n.id for n in session.notebook.notes],
                len(goals),
                viewers,
                after_failure=failed_goal is not None,
                retry_allowed=retry_allowed,
            )
            data = await self._text_generator.generate_json(
                prompt,
                schema,
                system_instruction=self._prompt_builder.build_goal_system(),
                purpose=purpose,
                images=images,
            )
            try:
                decision = parse_decision(data)
                if decision.spec.predicate not in predicates:
                    raise ValueError(
                        f"{decision.spec.predicate.value} is not one of the goals offered"
                    )
                if failed_goal is not None:
                    _check_remedy(decision, failed_goal, same_failures)
                await self._mid_goals.check_new(decision.changes)
                _serving(decision, self._mid_goals.rehearse(plan, decision.changes))
                status = await self._bridge.set_goal(
                    decision.spec, _kept(self._mid_goals.rehearse(plan, decision.changes))
                )
                # ここで初めて、今のままのプランに反映する（その間に返答が足しているかもしれない）
                preview = self._mid_goals.rehearse(plan, decision.changes)
                _serving(decision, preview)
                if (
                    self._chooser is not None
                    and preview.current is not None
                    and not preview.current.plan_steps
                    and not decision.steps
                ):
                    # 手順がないと、次の切れ目でまた Gemini を呼ぶことになる（docs/design/26）
                    raise ValueError(
                        f"write the steps for the mid goal at the top ({preview.current.title})"
                    )
            except (ValueError, GoalRejectedError) as e:
                error = str(e)
                logger.warning(f"目標の決定 {attempt} を差し戻した: {data!r}: {error}")
                continue
            if decision.diagnosis:
                logger.info(
                    f"失敗の分析: {decision.diagnosis} → {decision.remedy.value if decision.remedy else '?'}"
                    + (f"（助言: {decision.advice}）" if decision.advice else "")
                )
            await self._mid_goals.commit(plan, decision.changes)
            if decision.steps and plan.current is not None:
                await self._mid_goals.set_steps(plan, plan.current.id, decision.steps)
            self._write_notes(session.notebook, data, obs.day, goals, viewers)
            await self._start_goal(session, obs, _goal(decision, plan), reason, status)
            return
        raise TextGenerationError(
            f"no acceptable goal after {self._max_goal_attempts} attempts: {error}"
        )

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

    async def _start_goal(
        self,
        session: PlaySession,
        obs: GameObservation,
        goal: Goal,
        reason: str,
        status: GoalStatus,
    ) -> None:
        session.set_goal(goal, obs.time_phase)
        self._goal_steps.clear()
        # これから見えるもの（このイベントの語り、返答）は、終わった目標ではなく
        # 新しい目標の状態
        session.observe(replace(obs, goal=status))
        mid = session.plan.get(goal.mid_goal_id) if goal.mid_goal_id else None
        serves = f"（{mid.describe()} のため）" if mid else "（生存のため）"
        logger.info(
            f"目標: {goal.spec.describe()}{serves}（{reason}） - {goal.reason}; "
            f"残り {status.remaining}"
        )
        await self._event_publisher.publish(
            GoalSetEvent(
                goal=goal.spec.describe(), reason=goal.reason, mid_goal=mid.title if mid else ""
            )
        )


def _serving(decision: GoalDecision, plan: MidGoalPlan) -> None:
    """小目標が、言っている対象のためのものかを、編集後のプランで確かめる。"""
    if decision.serves == Serves.SURVIVAL:
        if not is_survival(decision.spec):
            raise ValueError(
                f"{decision.spec.describe()} is not a survival goal (through_night, at_home, "
                "cleared, have(food, n)); it must serve the mid goal at the top of the list"
            )
    elif plan.current is None:
        raise ValueError(
            "there is no mid goal left: add one toward the mission, or choose a survival goal"
        )


def _shown_goals(activity: Activity, ended_because: str) -> list[str]:
    """
    目標の決定で番号をつけて示す小目標（lesson の根拠）: これまでの小目標（古い順）と、
    今終わった小目標。番号は stream_context.format_activity(with_ids=True) と同じ。
    """
    goals = [f"{o.goal.spec.describe()}: {o.ended_because}" for o in activity.recent_goals]
    if activity.goal is not None:
        goals.append(f"{activity.goal.spec.describe()}: {ended_because}")
    return goals


def _viewers(messages: tuple[ConversationMessage, ...]) -> list[str]:
    """示した会話にいる視聴者（出てきた順）。"""
    names = [m.speaker_name for m in messages if m.role == MessageRole.VIEWER and m.speaker_name]
    return list(dict.fromkeys(names))


def _goal(decision: GoalDecision, plan: MidGoalPlan) -> Goal:
    current = plan.current
    mid_goal_id = current.id if decision.serves == Serves.CURRENT and current else None
    return Goal(
        spec=decision.spec,
        reason=decision.reason,
        mid_goal_id=mid_goal_id,
        diagnosis=decision.diagnosis,
        advice=decision.advice,
    )


def _no_progress(reason: str) -> bool:
    """小目標が行き詰まったか進まなかったか（原因を分析する失敗。docs/design/27）。"""
    return " is stuck " in reason or " stalled " in reason


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


def _check_remedy(decision: GoalDecision, failed: GoalSpec, same_failures: int) -> None:
    """失敗の後の決定が、分析と対処の決まりに合うか（docs/design/27 §2）。"""
    if not decision.diagnosis or decision.remedy is None:
        raise ValueError("after a failure, write the diagnosis first and then the remedy")
    same = decision.spec.same_kind(failed)
    if decision.remedy == Remedy.RETRY and not same:
        raise ValueError(
            f"retry means the same small goal as {failed.describe()}; for another one use change"
        )
    if decision.remedy == Remedy.CHANGE and same:
        raise ValueError(
            f"{decision.spec.describe()} is the same small goal as {failed.describe()} "
            "(only the number differs); choose a different one, or retry with advice"
        )
    if same and same_failures >= MAX_SAME_FAILURES:
        raise ValueError(
            f"{failed.describe()} made no progress {same_failures} times in a row; "
            "choose a different small goal (remedy change)"
        )
    if same and not decision.advice:
        raise ValueError("retry needs advice: what the action chooser should do differently")


def _step_line(decision: ActionDecision, result: ActionResult) -> str:
    """失敗の分析に見せる 1 ステップ: 選んだ行動、確信度、結果。"""
    outcome = "ok" if result.ok else "failed"
    return f"{result.action_id} ({decision.confidence:.2f}) → {outcome}: {result.result[:80]}"


def _kept(plan: MidGoalPlan) -> tuple[GoalSpec, ...]:
    """チェストが中目標のために取っておくもの: その stored() 条件。"""
    return tuple(
        c for g in plan.pending for c in g.conditions if c.predicate == GoalPredicate.STORED
    )


GOAL_COMMENTARY = 3


def _for_goal(messages: Sequence[ConversationMessage]) -> tuple[ConversationMessage, ...]:
    """目標の決定に渡す会話: チャットと返答は全部、実況は直近の数件だけ（26 §4）。"""
    commentary = [m for m in messages if m.message_type == MessageType.COMMENTARY]
    keep = set(map(id, commentary[-GOAL_COMMENTARY:]))
    return tuple(m for m in messages if m.message_type != MessageType.COMMENTARY or id(m) in keep)
