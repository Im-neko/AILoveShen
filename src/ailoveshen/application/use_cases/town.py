"""最終目標の街: 場所を選び、LLM が一度だけ定め、ブリッジが確かめ、あとで埋める。達成したら次の最終目標を決める。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.goal_vocabulary import (
    mission_schema,
    parse_mission,
    parse_site,
    parse_stage,
    parse_town,
    site_schema,
    stage_schema,
    town_schema,
)
from ailoveshen.application.use_cases.house import HouseDesigner
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.town_map import (
    apply_areas,
    draw_base,
    removable_facilities,
    town_map_schema,
)
from ailoveshen.application.use_cases.town_roads import road_strips
from ailoveshen.domain.entities import ObjectivePlan, PlaySession
from ailoveshen.domain.events import (
    HouseDesignedEvent,
    MissionAdvancedEvent,
    TownDefinedEvent,
    TownMapDrawnEvent,
    TownSiteChosenEvent,
)
from ailoveshen.domain.exceptions import GameBridgeError, GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    ConditionStatus,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    PlaybookStep,
    TownDefinition,
    TownSite,
    TownStage,
)

MAP_RETRY_SECONDS = 300.0  # 理想図を描けなかったあと、また描くまで待つ時間（time.monotonic）
MISSION_RETRY_SECONDS = 300.0  # 次の最終目標を書けなかったあと、また頼むまで待つ時間（time.monotonic）
SURVEY_SITES = 9  # 家の場所と 8 方向（minecraft-bridge/src/survey.mjs の plannedSites）
MOVE_CONDITIONS = (
    GoalSpec(GoalPredicate.BUILT),
    GoalSpec(GoalPredicate.PLACED, item="bed", where="home"),
)


class TownPlanner:
    """
    最終目標の街の場所を選び、街を一度だけ作り、実現できる形に保つ
    （docs/design/15_town.md §3、16_town_site.md）。

    場所が先: 最初の家ができたら、候補地を調べる大目標を足す。調べ終えたら、ブリッジが
    測った数字の表から LLM が 1 か所選ぶ（決めた後は変えない）。最初の家の場所でなければ、
    そこに建てる家を設計して引っ越しの大目標を足す。それから街を定める。どの手順も、
    世界とプランから次にやることを決めるので、途中で再起動しても続きから進む。

    街がどんなものかと、その段階は LLM が書く。定義を保存する前に、各段階の条件を
    ブリッジが確かめる: 判定できない条件や、配信者にできることでは手に入らない
    アイテム（製錬できる前の鉄の剣）は、理由をつけて差し戻す。最後の試行のあとも
    直らないものは、理由をつけて段階の未解決の部分に回す。こうして、できないことが
    大目標になることはない。

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
        objectives: ObjectiveKeeper,
        designer: HouseDesigner,
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
            objectives: 街の準備の大目標（調査、引っ越し）を足す
            designer: 引っ越し先の家を設計する
            max_attempts: まだ直らないものを未解決に残すまでに生成する回数
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._character = character
        self._store = store
        self._objectives = objectives
        self._designer = designer
        self._max_attempts = max_attempts
        self._mission_failed_at = -MISSION_RETRY_SECONDS
        self._map_failed_at = -MAP_RETRY_SECONDS

    async def advance(self, session: PlaySession, obs: GameObservation) -> None:
        """
        街の準備を 1 つ進める（小目標の切れ目で、大目標を判定した後に呼ぶ）。

        調査の大目標を足す → 調べ終えたら場所を選ぶ → 引っ越すなら家を設計して
        引っ越しの大目標を足す → 街を定める。LLM が使える答えを出せなかったときは、
        ログに残して次の切れ目でやり直す（代わりの場所や定義で進めない）。
        """
        plan = session.plan
        try:
            if plan.site is None:
                await self._survey_or_choose(plan, obs)
            if plan.site is None:
                return
            if plan.site.moving:
                await self._move(session, plan.site, obs)
            await self.prepare(plan)
            if plan.mission_achieved:
                await self._next_mission(plan, obs)
        except TextGenerationError as e:
            logger.error(f"街の準備を次の切れ目に持ち越す: {e}")

    async def prepare(self, plan: ObjectivePlan) -> None:
        """
        場所が決まっていれば、街がまだなければ定め、あれば今の段階の未解決の部分を書き直す。
        そして保存する。場所が決まるまでは何もしない（街は場所に合わせて定める）。

        書き直すのは今の段階だけ（未解決の段階は終えられないので、先の段階はそれが今になって
        から）、それも語彙（使える条件と、できること）が前に試したときから変わったときだけ。
        同じ語彙では同じ答えにしかならない（2026-09-27: 起動のたびに 5 段階を書き直し、ローカルの
        LLM で 4 分かかっていた）。
        """
        if plan.site is None:
            return
        if plan.town is None:
            town = await self._define(plan, await self._site_facts(plan.site))
            # 今の語彙で書いた段階は、書き直しを試したのと同じ
            version = await self._version(plan)
            town = replace(
                town,
                stages=tuple(
                    s if s.ready else replace(s, tried_with=version) for s in town.stages
                ),
            )
            plan.define_town(town)
            self._store.save(plan)
            logger.info(f"街を定めた: {town.text} / {' → '.join(s.title for s in town.stages)}")
            for i, s in enumerate(town.stages):
                logger.info(f"  段階 {i + 1} {_describe(s)}")
            await self._event_publisher.publish(
                TownDefinedEvent(text=town.text, stages=tuple(s.title for s in town.stages))
            )
            await self._keep_map(plan)
            return
        await self._keep_map(plan)
        town = plan.town
        i = plan.town_stage
        if i >= len(town.stages):
            return
        stage = town.stages[i]
        version = await self._version(plan, stage)
        if stage.ready or stage.tried_with == version:
            return
        written = replace(await self._resolve(town, stage), tried_with=version)
        logger.info(f"街の段階 {i + 1} を書き直した: {_describe(written)}")
        stages = list(town.stages)
        stages[i] = written
        plan.define_town(replace(town, stages=tuple(stages)))
        self._store.save(plan)

    async def _version(self, plan: ObjectivePlan, stage: TownStage | None = None) -> str:
        """
        未解決の部分を書き直す理由の版: 語彙（条件の一覧と、できること）と、完成した施設の名前と、段階の条件が
        全部そろったか。

        語彙だけだと、畑ができて `farmed(wheat, 9)` が手に入るようになっても、段階が書き直されず、条件が
        そろった段階が終わらなかった。そのあいだ、大目標が空になるたびに同じ畑を別の名前で作り直した
        （2026-10-19: wheat_farm_1〜5）。施設が増えたときと、段階の条件がそろったときに、一度ずつ書き直す
        （条件がそろった段階の未解決は、施設のほかの理由でも、手に入るようになっているかもしれない）。
        """
        version = self._prompt_builder.vocabulary_version()
        try:
            done = sorted(str(f.get("name")) for f in await self._bridge.facilities() if f.get("status") == "done")
        except GameBridgeError:
            done = []
        if stage is not None and stage.conditions and all(c in plan.stage_met for c in stage.conditions):
            done.append("(conditions met)")
        if not done:
            return version
        return f"{version}+{hashlib.sha256(','.join(done).encode('utf-8')).hexdigest()[:8]}"

    async def _next_mission(self, plan: ObjectivePlan, obs: GameObservation) -> None:
        """
        最終目標を達成したので、次の最終目標と、その定石（済んだ定石と同じ形の段）を LLM に書かせる
        （docs/design/180）。段の条件はブリッジが確かめる: 判定できない条件、今できることでは手に
        入らない物、全部もう満たしている段しかない答えは、理由をつけて差し戻す。定石の大目標は
        `ObjectiveKeeper` が次の切れ目から順に足す。書けなかったときは、しばらく待ってまた頼む。
        """
        if time.monotonic() - self._mission_failed_at < MISSION_RETRY_SECONDS:
            return
        assert plan.town is not None
        done = plan.mission
        facts = {
            "day": obs.day,
            "inventory": obs.state.get("inventory") or {},
            "builds": [name for name, _ in obs.titles],
        }
        taken = {s.name for s in plan.playbook}
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_mission_prompt(
                self._character,
                done,
                plan.missions_done,
                plan.playbook,
                plan.town,
                facts,
                previous_error=error,
            )
            data = await self._text_generator.generate_json(
                prompt, mission_schema(), purpose="mission"
            )
            try:
                mission, reason, steps = parse_mission(data, taken)
            except ValueError as e:
                error = str(e)
                logger.warning(f"次の最終目標の試行 {attempt} を差し戻した: {error}")
                continue
            problems = [
                f"段「{s.title}」: {p}"
                for s in steps
                for p in await self._condition_problems(_judgeable(s.conditions))
            ]
            if not problems and await self._all_met(steps):
                problems = ["全部の段の条件がもう満たされている。まだできていないことを書く"]
            if not problems:
                problems = [
                    f"段「{s.title}」の条件はもう満たされている。まだできていないこと（新しい建物、新しい成果）を書く"
                    for s in steps
                    if not s.keep and await self._step_met(s)
                ]
            if problems:
                error = "\n".join(problems)
                logger.warning(f"次の最終目標の試行 {attempt} を差し戻した: {error}")
                continue
            plan.advance_mission(mission, steps)
            self._store.save(plan)
            logger.info(f"最終目標を達成した: {done.text}")
            logger.info(f"次の最終目標を決めた: {mission.text} - {reason}")
            for step in steps:
                logger.info(f"  定石 {step.name} {step.title}: {', '.join(c.describe() for c in step.conditions)}")
            await self._event_publisher.publish(
                MissionAdvancedEvent(
                    done=done.text, text=mission.text, reason=reason, steps=tuple(s.title for s in steps)
                )
            )
            return
        self._mission_failed_at = time.monotonic()
        raise TextGenerationError(
            f"no valid next mission after {self._max_attempts} attempts: {error}"
        )

    async def _step_met(self, step: PlaybookStep) -> bool:
        """段の条件がもう全部満たされているか（これから作る施設や、まだない判定がある段は、まだ）。"""
        if len(_judgeable(step.conditions)) < len(step.conditions):
            return False
        if await self._unwritten(step.conditions):
            return False
        return all(s.met for s in await self._bridge.check(step.conditions))

    async def _all_met(self, steps: Sequence[PlaybookStep]) -> bool:
        for step in steps:
            if len(_judgeable(step.conditions)) < len(step.conditions):
                return False  # 施設はこれから作る
            if not all(s.met for s in await self._bridge.check(step.conditions)):
                return False
        return True

    async def _keep_map(self, plan: ObjectivePlan) -> None:
        """
        街の理想図（docs/design/47 §6）を、まだなければ描き、街の段階が進んでいれば描き直す。
        引っ越しの途中は描かない（街の場所の地形がまだ見えていない）。
        """
        if plan.town is None or plan.site is None or plan.preparing_town:
            return
        current = plan.town_map
        if current is None:
            reason = "街を定めた" if plan.town_stage == 0 else "理想図がまだない"
        elif current.stage < plan.town_stage and not plan.town_complete:
            reason = f"段階 {plan.town_stage + 1}「{plan.town.stages[plan.town_stage].title}」に進んだ"
        else:
            return
        if time.monotonic() - self._map_failed_at < MAP_RETRY_SECONDS:
            return
        try:
            await self._draw_map(plan, reason)
        except TextGenerationError as e:
            # 理想図は進めるための地図で、描けなくても街づくりは止めない（2026-10-19: 出力の上限で毎回切れ、
            # プレイを始められないまま 8 分待った）。前の図のまま続けて、しばらくしてからまた描く
            self._map_failed_at = time.monotonic()
            logger.warning(f"街の理想図を描けなかった。前の図のまま続ける: {e}")

    async def _draw_map(self, plan: ObjectivePlan, reason: str) -> None:
        """理想図を描く（描き直す）。区画の地形を読めなければ次の切れ目に持ち越す。"""
        assert plan.town is not None and plan.site is not None
        site = plan.site
        try:
            rows = await self._bridge.zones(site.x, site.z)
        except GameBridgeError as e:
            logger.warning(f"街の理想図を次の切れ目に持ち越す（区画の地形を読めない）: {e}")
            return
        current = plan.town_map
        base = draw_base(rows, current)
        schema = town_map_schema([z.id for z in base if not z.fixed], removable_facilities(base))
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_town_map_prompt(
                plan.town, plan.town_stage, rows, base, current, reason, previous_error=error
            )
            data = await self._text_generator.generate_json(prompt, schema, purpose="town_plan")
            try:
                town_map = apply_areas(
                    base,
                    data,
                    (site.x, site.z),
                    plan.town_stage,
                    current.history if current else (),
                    reason,
                )
            except ValueError as e:
                error = str(e)
                logger.warning(f"街の理想図の試行 {attempt} を差し戻した: {error}")
                continue
            plan.set_town_map(town_map)
            self._store.save(plan)
            try:
                await self._bridge.set_town_layout(
                    road_strips(list(town_map.zones)), [name for name, _ in town_map.clear]
                )
            except GameBridgeError as e:
                logger.warning(f"街の道・壊す建築をブリッジに送れなかった（次に描くときに送る）: {e}")
            used = {u: len(zs) for u, zs in town_map.by_use().items()}
            logger.info(f"街の理想図を描いた（{reason}）: {town_map.concept} / {used}")
            await self._event_publisher.publish(
                TownMapDrawnEvent(
                    concept=town_map.concept, reason=reason, revised=current is not None
                )
            )
            return
        raise TextGenerationError(
            f"no valid town map after {self._max_attempts} attempts: {error}"
        )

    async def _survey_or_choose(self, plan: ObjectivePlan, obs: GameObservation) -> None:
        if plan.preparing_town:
            return  # 調べている途中
        survey = obs.state.get("survey") or {}
        rows = survey.get("sites") or []
        if rows and len(rows) >= survey.get("planned", 0):
            await self._choose(plan, rows)
        elif obs.has_home:
            await self._objectives.add_town_goal(
                plan,
                title="街の場所を探す",
                conditions=(GoalSpec(GoalPredicate.SURVEYED, count=SURVEY_SITES),),
                reason="街を作る場所を、まわりを見て回って決める",
            )

    async def _choose(self, plan: ObjectivePlan, rows: list[dict[str, Any]]) -> None:
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_site_prompt(
                self._character, plan.mission, rows, previous_error=error
            )
            data = await self._text_generator.generate_json(
                prompt, site_schema([str(r["id"]) for r in rows]), purpose="site"
            )
            try:
                site = parse_site(data, rows)
            except ValueError as e:
                error = str(e)
                logger.warning(f"街の場所の選択 {attempt} を差し戻した: {error}")
                continue
            plan.choose_site(site)
            self._store.save(plan)
            where = "引っ越す" if site.moving else "最初の家の場所のまま"
            logger.info(
                f"街の場所を選んだ: {site.name}（{site.site_id} {site.x},{site.z}、{where}）"
                f" - {site.reason}"
            )
            await self._event_publisher.publish(
                TownSiteChosenEvent(
                    name=site.name, site_id=site.site_id, reason=site.reason, moving=site.moving
                )
            )
            return
        raise TextGenerationError(
            f"no valid town site after {self._max_attempts} attempts: {error}"
        )

    async def _move(self, session: PlaySession, site: TownSite, obs: GameObservation) -> None:
        """引っ越し先の家を建てる計画と、引っ越しの大目標（まだなければ）。"""
        build = obs.state.get("build") or {}
        if build.get("site") != {"x": site.x, "z": site.z}:
            facts = await self._site_facts(site)
            blueprint = await self._designer.design(
                site_note=self._prompt_builder.describe_site(site, facts)
            )
            await self._bridge.set_build_plan(blueprint, site)
            session.blueprint = blueprint
            session.completion_announced = False
            await self._event_publisher.publish(
                HouseDesignedEvent(name=blueprint.name, concept=blueprint.concept)
            )
        elif build.get("complete"):
            return  # 引っ越し先の家はもう建った
        if not session.plan.preparing_town:
            await self._objectives.add_town_goal(
                session.plan,
                title=f"{site.name}に引っ越す",
                conditions=MOVE_CONDITIONS,
                reason=site.reason,
                position=0,
            )

    async def _site_facts(self, site: TownSite) -> dict[str, Any]:
        """選んだ候補地の、ブリッジが測った数字。"""
        obs = await self._bridge.observe()
        rows = (obs.state.get("survey") or {}).get("sites") or []
        return next((r for r in rows if r.get("id") == site.site_id), {})

    async def _define(self, plan: ObjectivePlan, facts: dict[str, Any]) -> TownDefinition:
        assert plan.site is not None
        error = ""
        town: TownDefinition | None = None
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_town_prompt(
                self._character, plan.mission, plan.site, facts, previous_error=error
            )
            data = await self._text_generator.generate_json(prompt, town_schema(), purpose="town")
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
            data = await self._text_generator.generate_json(prompt, stage_schema(), purpose="town")
            try:
                written = parse_stage(data, stage.title, stage.why)
            except ValueError as e:
                error = str(e)
                logger.warning(f"街の段階の試行 {attempt} を差し戻した: {error}")
                continue
            problems = await self._problems(written)
            if not problems:
                problems = _added_unresolved(stage, written)
            if not problems:
                problems = await self._already_done(stage, written)
            if not problems:
                return written
            error = "\n".join(problems)
            logger.warning(f"街の段階の試行 {attempt} を差し戻した: {error}")
        return stage

    async def _already_done(self, stage: TownStage, written: TownStage) -> list[str]:
        # 未解決として書いてあった条件そのもの（`farmed(wheat, 9)`）が、畑ができて今そろったのは、言い換えではなく解決
        named = " ".join(stage.unresolved)
        new = [c for c in written.conditions if c not in stage.conditions and c.describe() not in named]
        new = [c for c in new if c not in await self._unwritten(new)]
        if not new or not all(s.met for s in await self._bridge.check(new)):
            return []
        conditions = ", ".join(c.describe() for c in new)
        return [
            f"{conditions} はもう全部そろっている。まだできないことを、"
            "今そろっている別のことに言い換えている。書けないなら unresolved に残す"
        ]

    async def _problems(self, stage: TownStage) -> list[str]:
        """段階の条件を保存できない理由。条件ごとに 1 行（なし: すべて問題ない）。"""
        return await self._condition_problems(stage.conditions)

    async def _condition_problems(self, conditions: Sequence[GoalSpec]) -> list[str]:
        unwritten = await self._unwritten(conditions)
        return [p for c in conditions if c not in unwritten and (p := await self._problem(c))]

    async def _unwritten(self, conditions: Sequence[GoalSpec]) -> set[GoalSpec]:
        """
        まだ判定が書かれていない `custom(name)`。段階が大目標になるときに `ObjectiveKeeper` が書かせるので、
        今は確かめない（述語で書けない部分を、判定コードを LLM が書くことで条件にする。設計書 199 §5）。
        """
        named = [c for c in conditions if c.predicate == GoalPredicate.CUSTOM]
        if not named:
            return set()
        try:
            known = {str(c["name"]) for c in await self._bridge.conditions()}
        except GameBridgeError:
            return set()
        return {c for c in named if c.name not in known}

    async def _problem(self, condition: GoalSpec) -> str:
        try:
            [status] = await self._bridge.check([condition])
        except GoalRejectedError as e:
            return f"{condition.describe()} は判定できない（{e}）"
        if status.impossible:
            return _impossible(condition, status)
        return ""

    async def _settle(self, stage: TownStage) -> TownStage:
        """
        保存できない条件を整えた段階。

        手に入らない条件は未解決の部分に移す（能力が足されたら書き直す）。判定できない条件は
        書き損じで、能力が足されても書けるようにならない: 未解決で待たせると段階が二度と終わらないので捨てる。
        """
        kept: list[GoalSpec] = []
        unresolved = list(stage.unresolved)
        unwritten = await self._unwritten(stage.conditions)
        for c in stage.conditions:
            if c in unwritten:
                kept.append(c)
                continue
            try:
                [status] = await self._bridge.check([c])
            except GoalRejectedError as e:
                logger.warning(f"街の段階「{stage.title}」の条件 {c.describe()} は判定できないので捨てた（{e}）")
                continue
            if status.impossible:
                unresolved.append(_impossible(c, status))
            else:
                kept.append(c)
        if not kept and not unresolved:
            raise TextGenerationError(
                f"town stage {stage.title} has no condition the bridge can judge"
            )
        return replace(stage, conditions=tuple(kept), unresolved=tuple(unresolved))


def _added_unresolved(stage: TownStage, written: TownStage) -> list[str]:
    """
    書き直しで、元の未解決より多くの未解決を足していないか。足すと段階が終わらなくなる（2026-10-19: 畑ができて
    farmed(wheat, 9) を書けたのに、「食料が減らないことを判定する条件がない」と新しい未解決を足して、段階が閉じなかった）。
    条件に書けるようになった部分（元の未解決の文に、新しい条件の描写がある）は、残る数から引く。
    """
    resolved = sum(
        1
        for u in stage.unresolved
        if any(c.describe() in u for c in written.conditions if c not in stage.conditions)
    )
    allowed = len(stage.unresolved) - resolved
    if len(written.unresolved) <= allowed:
        return []
    return [
        f"unresolved が {len(written.unresolved)} 個ある。書き直す前の未解決のうち今も書けないのは多くても {allowed} 個で、"
        "新しいことを足すと段階が終わらなくなる。書けるようになった部分は conditions に書き、unresolved に新しいことを足さない"
    ]


def _judgeable(conditions: Sequence[GoalSpec]) -> tuple[GoalSpec, ...]:
    """今ブリッジに確かめられる条件。新しい名前の施設は、足すときに設計してから判定する（定石の段）。"""
    return tuple(c for c in conditions if c.predicate != GoalPredicate.FACILITY)


def _impossible(condition: GoalSpec, status: ConditionStatus) -> str:
    return f"{condition.describe()} は今できることでは手に入らない（{', '.join(status.impossible)}）"


def _describe(stage: TownStage) -> str:
    conditions = ", ".join(c.describe() for c in stage.conditions) or "-"
    unresolved = f" / unresolved: {'; '.join(stage.unresolved)}" if stage.unresolved else ""
    return f"{stage.title}: {conditions}{unresolved}"
