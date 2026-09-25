"""大目標の街: LLM が一度だけ定め、ブリッジが確かめ、あとで埋める。"""

from __future__ import annotations

from dataclasses import replace

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.goal_vocabulary import (
    parse_stage,
    parse_town,
    stage_schema,
    town_schema,
)
from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.events import TownDefinedEvent
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    GoalSpec,
    TownDefinition,
    TownStage,
)


class TownPlanner:
    """
    大目標の街を一度だけ作り、実現できる形に保つ（docs/design/15_town.md §3）。

    街がどんなものかと、その段階は LLM が書く。定義を保存する前に、各段階の条件を
    ブリッジが確かめる: 判定できない条件や、配信者にできることでは手に入らない
    アイテム（製錬できる前の鉄の剣）は、理由をつけて差し戻す。最後の試行のあとも
    直らないものは、理由をつけて段階の未解決の部分に回す。こうして、できないことが
    中目標になることはない。

    定義は固定する: コメントでも実行でも変わらない。未解決の部分だけは、実行の
    始めに書き直す。その後に足された能力で表せるようになっているかもしれないから
    だ（段階の題名と意味は変えない）。
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        character: CharacterProfile,
        store: IMissionStore,
        max_attempts: int = 3,
    ) -> None:
        """
        依存を受け取って初期化する（依存性の注入）。

        Args:
            text_generator: LLM のアダプター（構造化出力）
            prompt_builder: ゲームのプロンプト組み立てのアダプター
            bridge: Minecraft ブリッジのアダプター（条件を確かめる）
            event_publisher: ドメインイベントの発行器
            character: 配信者のキャラクターのプロフィール
            store: 再起動をまたいでプランを保つ
            max_attempts: まだ直らないものを未解決に残すまでに生成する回数
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._character = character
        self._store = store
        self._max_attempts = max_attempts

    async def prepare(self, plan: MidGoalPlan) -> None:
        """街がまだなければ定め、あれば未解決の段階を書き直す。そして保存する。"""
        if plan.town is None:
            town = await self._define(plan)
            plan.define_town(town)
            self._store.save(plan)
            logger.info(f"街を定めた: {town.text} / {' → '.join(s.title for s in town.stages)}")
            for i, s in enumerate(town.stages):
                logger.info(f"  段階 {i + 1} {_describe(s)}")
            await self._event_publisher.publish(
                TownDefinedEvent(text=town.text, stages=tuple(s.title for s in town.stages))
            )
            return
        town = plan.town
        stages = list(town.stages)
        for i in range(plan.town_stage, len(stages)):
            if stages[i].ready:
                continue
            stages[i] = await self._resolve(town, stages[i])
            logger.info(f"街の段階 {i + 1} を書き直した: {_describe(stages[i])}")
        if tuple(stages) != town.stages:
            plan.define_town(replace(town, stages=tuple(stages)))
            self._store.save(plan)

    async def _define(self, plan: MidGoalPlan) -> TownDefinition:
        error = ""
        town: TownDefinition | None = None
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_town_prompt(
                self._character, plan.mission, previous_error=error
            )
            data = await self._text_generator.generate_json(prompt, town_schema())
            try:
                town = parse_town(data)
            except ValueError as e:
                error = str(e)
                logger.warning(f"街の定義の試行 {attempt} を差し戻した: {error}")
                continue
            errors = [
                f"段階「{s.title}」: {e}" for s in town.stages for e in await self._problems(s)
            ]
            if not errors:
                return town
            error = "\n".join(errors)
            logger.warning(f"街の定義の試行 {attempt} を差し戻した: {error}")
        if town is None:
            raise TextGenerationError(
                f"no valid town definition after {self._max_attempts} attempts: {error}"
            )
        return replace(town, stages=tuple([await self._settle(s) for s in town.stages]))

    async def _resolve(self, town: TownDefinition, stage: TownStage) -> TownStage:
        """
        書き直した段階。保存できる答えがなければ、元のままの段階。

        まだない能力のために残した部分が、もう済んでいることはありえない: 新しい
        条件が今すべて満たされているなら、その部分は別のもの（built() としての倉庫、
        つまり最初の家）に言い換えられている。そうなると、何も建てずに段階と街が
        終わってしまう。
        """
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_stage_prompt(town, stage, previous_error=error)
            data = await self._text_generator.generate_json(prompt, stage_schema())
            try:
                written = parse_stage(data, stage.title, stage.why)
            except ValueError as e:
                error = str(e)
                logger.warning(f"街の段階の試行 {attempt} を差し戻した: {error}")
                continue
            problems = await self._problems(written)
            if not problems:
                problems = await self._already_done(stage, written)
            if not problems:
                return written
            error = "\n".join(problems)
            logger.warning(f"街の段階の試行 {attempt} を差し戻した: {error}")
        return stage

    async def _already_done(self, stage: TownStage, written: TownStage) -> list[str]:
        new = [c for c in written.conditions if c not in stage.conditions]
        if not new or not all(s.met for s in await self._bridge.check(new)):
            return []
        conditions = ", ".join(c.describe() for c in new)
        return [
            f"{conditions} はもう全部そろっている。まだできないことを、"
            "今そろっている別のことに言い換えている。書けないなら unresolved に残す"
        ]

    async def _problems(self, stage: TownStage) -> list[str]:
        """段階の条件を保存できない理由。条件ごとに 1 行（なし: すべて問題ない）。"""
        return [p for c in stage.conditions if (p := await self._problem(c))]

    async def _problem(self, condition: GoalSpec) -> str:
        try:
            [status] = await self._bridge.check([condition])
        except GoalRejectedError as e:
            return f"{condition.describe()} は判定できない（{e}）"
        if status.impossible:
            why = ", ".join(status.impossible)
            return f"{condition.describe()} は今できることでは手に入らない（{why}）"
        return ""

    async def _settle(self, stage: TownStage) -> TownStage:
        """保存できない条件を未解決の部分に移した段階。"""
        kept: list[GoalSpec] = []
        unresolved = list(stage.unresolved)
        for c in stage.conditions:
            problem = await self._problem(c)
            if problem:
                unresolved.append(problem)
            else:
                kept.append(c)
        return replace(stage, conditions=tuple(kept), unresolved=tuple(unresolved))


def _describe(stage: TownStage) -> str:
    conditions = ", ".join(c.describe() for c in stage.conditions) or "-"
    unresolved = f" / unresolved: {'; '.join(stage.unresolved)}" if stage.unresolved else ""
    return f"{stage.title}: {conditions}{unresolved}"
