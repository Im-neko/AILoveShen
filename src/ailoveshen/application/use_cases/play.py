"""プレイのユースケース: LLM が設計して目標を決め、ブリッジが判定し、選択器が遊ぶ。"""

from __future__ import annotations

from collections import deque
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
from ailoveshen.application.use_cases.goal_vocabulary import (
    GoalDecision,
    Serves,
    goal_schema,
    parse_decision,
    parse_note_changes,
    predicates_now,
)
from ailoveshen.application.use_cases.house import HouseDesigner, parse_blueprint
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.tool_catalog import (
    ACTION_TOOLS,
    ToolCallError,
    is_query,
    parse_tool_call,
    tool_specs,
)
from ailoveshen.application.use_cases.town import TownPlanner
from ailoveshen.application.use_cases.watcher import ToolWatcher
from ailoveshen.domain.entities import Conversation, MidGoalPlan, Notebook, PlaySession
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalEndedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
)
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    Activity,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    MessageRole,
    ToolCall,
    ToolOutcome,
    is_survival,
)

CONTROLS = ("candidates", "tools")
MAX_QUERIES_PER_STEP = 3  # 1 ステップの中で調べものをしてよい回数
SUGGESTIONS_SHOWN = 12  # 道具の選択に見せるソルバーの提案の数
RECENT_TOOLS = 8  # 道具の選択に見せる直近の呼び出しの数


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
        self._recent_tools: deque[ToolOutcome] = deque(maxlen=RECENT_TOOLS)

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
        goal_changed = False
        if session.needs_new_goal(obs):
            await self._change_goal(session, obs)
            goal_changed = True
            obs = await self._bridge.observe()
            session.observe(obs)

        goal = session.goal
        assert goal is not None
        if self._control == "tools":
            decision, result = await self._act_with_tools(session, obs)
        else:
            decision, result = await self._act_on_candidate(goal, obs)
        await self._mid_goals.step_counted(session.plan, session.record(result))
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
        specs = tool_specs()
        action_specs = [t for t in specs if t.name in ACTION_TOOLS]
        for attempt in range(MAX_QUERIES_PER_STEP + 1):
            state = await self._bridge.state()
            prompt = self._prompt_builder.build_tool_prompt(
                session.activity(),
                state,
                obs.candidates[:SUGGESTIONS_SHOWN],
                tuple(self._recent_tools),
            )
            last = self._recent_tools[-1] if self._recent_tools else None
            purpose = "tool_after_failure" if last is not None and not last.ok else "tool"
            offered = specs if attempt < MAX_QUERIES_PER_STEP else action_specs
            choice = await self._text_generator.choose_tool(prompt, offered, purpose=purpose)
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
            outcome = await self._watcher.run(call)
            self._recent_tools.append(outcome)
            break
        decision = ActionDecision(action_id=outcome.call.describe(), confidence=1.0)
        return decision, outcome.as_action_result()

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
        await self._decide_goal(session, obs, activity, reason)

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
        messages = self._conversation.recent_messages(self._history_limit)
        goals = _shown_goals(activity, reason)
        viewers = _viewers(messages)
        # 行き詰まった・進まなかった後は深く考え直す（設計書 19 §7）
        failed = " is stuck " in reason or " stalled " in reason
        purpose = "goal_after_failure" if failed else "goal"
        error = ""
        for attempt in range(1, self._max_goal_attempts + 1):
            prompt = self._prompt_builder.build_goal_prompt(
                blueprint=session.blueprint,
                activity=activity,
                goal_ended_because=reason,
                recent_messages=messages,
                predicates=predicates,
                previous_error=error,
            )
            schema = goal_schema(
                predicates,
                [g.id for g in plan.pending],
                [n.id for n in session.notebook.notes],
                len(goals),
                viewers,
            )
            data = await self._text_generator.generate_json(prompt, schema, purpose=purpose)
            try:
                decision = parse_decision(data)
                if decision.spec.predicate not in predicates:
                    raise ValueError(
                        f"{decision.spec.predicate.value} is not one of the goals offered"
                    )
                await self._mid_goals.check_new(decision.changes)
                _serving(decision, self._mid_goals.rehearse(plan, decision.changes))
                status = await self._bridge.set_goal(
                    decision.spec, _kept(self._mid_goals.rehearse(plan, decision.changes))
                )
                # ここで初めて、今のままのプランに反映する（その間に返答が足しているかもしれない）
                preview = self._mid_goals.rehearse(plan, decision.changes)
                _serving(decision, preview)
            except (ValueError, GoalRejectedError) as e:
                error = str(e)
                logger.warning(f"目標の決定 {attempt} を差し戻した: {data!r}: {error}")
                continue
            await self._mid_goals.commit(plan, decision.changes)
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
    return Goal(spec=decision.spec, reason=decision.reason, mid_goal_id=mid_goal_id)


def _kept(plan: MidGoalPlan) -> tuple[GoalSpec, ...]:
    """チェストが中目標のために取っておくもの: その stored() 条件。"""
    return tuple(
        c for g in plan.pending for c in g.conditions if c.predicate == GoalPredicate.STORED
    )
