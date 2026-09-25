"""プレイのユースケース: LLM が設計して目標を決め、ブリッジが判定し、選択器が遊ぶ。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

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
    predicates_now,
)
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.town import TownPlanner
from ailoveshen.domain.entities import Conversation, MidGoalPlan, PlaySession
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
    Activity,
    CharacterProfile,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    Side,
    is_survival,
)

_SIDES = [s.value for s in Side]

HOUSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "A short name for the house"},
        "concept": {
            "type": "string",
            "description": "One sentence about the idea behind the design",
        },
        "width": {
            "type": "integer",
            "minimum": HouseBlueprint.MIN_SIDE,
            "maximum": HouseBlueprint.MAX_SIDE,
        },
        "depth": {
            "type": "integer",
            "minimum": HouseBlueprint.MIN_SIDE,
            "maximum": HouseBlueprint.MAX_SIDE,
        },
        "wall_height": {
            "type": "integer",
            "minimum": HouseBlueprint.MIN_WALL_HEIGHT,
            "maximum": HouseBlueprint.MAX_WALL_HEIGHT,
        },
        "door_side": {"type": "string", "enum": _SIDES},
        "door_offset": {"type": "integer", "minimum": 1, "maximum": HouseBlueprint.MAX_SIDE - 2},
        "corner_pillars": {"type": "boolean"},
    },
    "required": [
        "name",
        "concept",
        "width",
        "depth",
        "wall_height",
        "door_side",
        "door_offset",
        "corner_pillars",
    ],
}


def _parse_blueprint(data: dict[str, Any]) -> HouseBlueprint:
    try:
        return HouseBlueprint(
            name=str(data["name"]),
            concept=str(data["concept"]),
            width=int(data["width"]),
            depth=int(data["depth"]),
            wall_height=int(data["wall_height"]),
            door_side=Side(data["door_side"]),
            door_offset=int(data["door_offset"]),
            corner_pillars=bool(data.get("corner_pillars", False)),
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"malformed blueprint: {e}") from e


def _home_blueprint(obs: GameObservation) -> HouseBlueprint | None:
    """前に建てた家の設計（ブリッジが持っていれば）。"""
    design = (obs.state.get("home") or {}).get("design")
    if not design:
        return None
    try:
        return _parse_blueprint(design)
    except ValueError as e:
        logger.warning(f"家の設計を読めない: {e}")
        return None


class StartPlayUseCase(IStartPlay):
    """
    ユースケース: LLM が家を設計し、そのブロックのプランをブリッジに送り、セッションを始める。

    設計は HouseBlueprint が検証する。正しくない設計は、検証のエラーをつけて LLM に
    差し戻す（max_attempts 回まで）。

    ブリッジにもう完成した家があるとき（前の実行が建てた）は、家を設計しない: その家を
    そのまま使い、中目標を先に進める。

    保存したプランが同じ大目標のものなら、大目標と中目標はそこから続ける。そうでなければ、
    設定から作ったプランで始める。大目標の街は、プランにまだなければ定め（以後は保つ）、
    未解決の段階は今の能力で書き直す。
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        character: CharacterProfile,
        plan: MidGoalPlan,
        store: IMissionStore,
        town: TownPlanner,
        max_attempts: int = 3,
        max_steps_per_goal: int = 40,
        max_consecutive_failures: int = 3,
        max_stalled_steps: int = 8,
    ) -> None:
        """
        依存を受け取ってユースケースを初期化する（依存性の注入）。

        Args:
            text_generator: LLM のアダプター（構造化出力）
            prompt_builder: ゲームのプロンプト組み立てのアダプター
            bridge: Minecraft ブリッジのアダプター
            event_publisher: ドメインイベントの発行器
            character: 配信者のキャラクターのプロフィール（設計に反映する）
            plan: 設定から作った、大目標と最初の中目標
            store: 再起動をまたいでプランを保つ
            town: 大目標の街を定め、実現できる形に保つ
            max_attempts: あきらめるまでに設計を試す回数
            max_steps_per_goal: LLM に新しい目標を求めるまでのステップ数
            max_consecutive_failures: 新しい目標を求めるまでに、続けて失敗するステップ数
            max_stalled_steps: 新しい目標を求めるまでに、進まないステップ数
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._character = character
        self._plan = plan
        self._store = store
        self._town = town
        self._max_attempts = max_attempts
        self._max_steps_per_goal = max_steps_per_goal
        self._max_consecutive_failures = max_consecutive_failures
        self._max_stalled_steps = max_stalled_steps

    async def execute(self) -> PlaySession:
        """プランと街を読み込み、家を設計して（建っていなければ）、セッションを始める。"""
        plan = self._load_plan()
        await self._town.prepare(plan)
        obs = await self._bridge.observe()
        if obs.has_home:
            blueprint = _home_blueprint(obs)
            name = f" ({blueprint.name})" if blueprint else ""
            logger.info(f"家はもう建っている{name}: 新しい家は設計しない")
            session = self._session(blueprint, plan)
            session.completion_announced = True
            return session
        blueprint = await self._design()
        logger.info(
            f"家を設計した: {blueprint.name} "
            f"{blueprint.width}x{blueprint.depth}x{blueprint.wall_height} "
            f"door={blueprint.door_side.value}:{blueprint.door_offset} "
            f"pillars={blueprint.corner_pillars} - {blueprint.concept}"
        )
        await self._event_publisher.publish(
            HouseDesignedEvent(name=blueprint.name, concept=blueprint.concept)
        )
        await self._bridge.set_build_plan(blueprint)
        return self._session(blueprint, plan)

    def _session(self, blueprint: HouseBlueprint | None, plan: MidGoalPlan) -> PlaySession:
        return PlaySession(
            blueprint=blueprint,
            plan=plan,
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
            )
            logger.info(f"中目標を引き継いだ: {', '.join(g.describe() for g in plan.pending)}")
        else:
            if saved is not None:
                logger.info(f"大目標が「{saved.mission.text}」から変わった: 中目標は最初からにする")
            self._store.save(plan)
        return plan

    async def _design(self) -> HouseBlueprint:
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_house_design_prompt(
                self._character, previous_error=error
            )
            data = await self._text_generator.generate_json(prompt, HOUSE_SCHEMA)
            try:
                return _parse_blueprint(data)
            except ValueError as e:
                error = str(e)
                logger.warning(f"家の設計の試行 {attempt} を差し戻した: {error}")
        raise TextGenerationError(
            f"no valid house design after {self._max_attempts} attempts: {error}"
        )


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
        history_limit: int = 10,
        max_goal_attempts: int = 3,
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
            history_limit: 目標の決定に渡す最近の会話のメッセージの数
            max_goal_attempts: 拒否が続くとき、あきらめるまでに目標を決める回数
        """
        self._bridge = bridge
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._action_selector = action_selector
        self._event_publisher = event_publisher
        self._conversation = conversation
        self._mid_goals = mid_goals
        self._history_limit = history_limit
        self._max_goal_attempts = max_goal_attempts

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
        candidates = obs.candidates
        if len(candidates) == 1:
            decision = ActionDecision(action_id=candidates[0].action_id, confidence=1.0)
        else:
            state, instructions = self._prompt_builder.build_action_context(goal, obs)
            logger.info(
                f"候補（{len(candidates)}）: " + " | ".join(c.action_id for c in candidates)
            )
            decision = await self._action_selector.select(state, candidates, instructions)

        result = await self._bridge.act(decision.action_id)
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

    async def _change_goal(self, session: PlaySession, obs: GameObservation) -> None:
        reason = session.goal_end_reason(obs)
        # 決める前に判定する: もう済んでいる中目標（前の実行で建てた家）を、次の目標の
        # 対象にしてはいけない
        await self._mid_goals.judge(session.plan)
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
        predicates = predicates_now(obs)
        plan = session.plan
        error = ""
        for attempt in range(1, self._max_goal_attempts + 1):
            prompt = self._prompt_builder.build_goal_prompt(
                blueprint=session.blueprint,
                activity=activity,
                goal_ended_because=reason,
                recent_messages=self._conversation.recent_messages(self._history_limit),
                predicates=predicates,
                previous_error=error,
            )
            schema = goal_schema(predicates, [g.id for g in plan.pending])
            data = await self._text_generator.generate_json(prompt, schema)
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
            await self._start_goal(session, obs, _goal(decision, plan), reason, status)
            return
        raise TextGenerationError(
            f"no acceptable goal after {self._max_goal_attempts} attempts: {error}"
        )

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


def _goal(decision: GoalDecision, plan: MidGoalPlan) -> Goal:
    current = plan.current
    mid_goal_id = current.id if decision.serves == Serves.CURRENT and current else None
    return Goal(spec=decision.spec, reason=decision.reason, mid_goal_id=mid_goal_id)


def _kept(plan: MidGoalPlan) -> tuple[GoalSpec, ...]:
    """チェストが中目標のために取っておくもの: その stored() 条件。"""
    return tuple(
        c for g in plan.pending for c in g.conditions if c.predicate == GoalPredicate.STORED
    )
