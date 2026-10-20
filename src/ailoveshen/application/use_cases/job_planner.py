"""
大目標の計画（docs/design/50 §18）: 大目標が一番上に来たら、LLM がまず計画を立てる。

大目標を段階（中目標）に分け、それぞれを小目標の並びにする。小目標には前提を付けられる
（バケツの前に鉄）。解決器の「次の一歩」だけに任せず、何を先にそろえるかを LLM が書く。計画は
一度書いたら失敗・行き詰まり・死ぬまで書き直さず、次の小目標は `GoalChooser` がこの計画から選ぶ。

小目標の条件は世界から判定できるもの（`have` `placed` `farmed` `facility` など。custom は除く）。
条件で書けない段階は、その段階の最後の小目標の条件を中目標の条件にする。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.goal_vocabulary import (
    MAX_JOBS,
    _spec_properties,
    parse_spec,
)
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.plan_lint import check_plan, own_condition_last
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.exceptions import (
    AILoveShenError,
    GameBridgeError,
    GoalRejectedError,
    TextGenerationError,
)
from ailoveshen.domain.value_objects import (
    MAX_STEP_DEPTH,
    MAX_STEP_NEEDS,
    MAX_STEPS,
    STEP_PREDICATES,
    Activity,
    GoalPredicate,
    Job,
    MaterialTree,
    Objective,
    Step,
)

_PREDICATES = [p for p in GoalPredicate if p in STEP_PREDICATES]
_MATERIAL_PREDICATES = frozenset({GoalPredicate.HAVE, GoalPredicate.PLACED, GoalPredicate.STORED})


def _step_schema(levels: int, nested: bool = False) -> dict[str, Any]:
    """
    小目標 1 つの形。`levels` は、この下に前提を何段書けるか。`nested` は前提（needs）の中の小目標で、理由は
    省いてよい（2026-10-17: 計画の書き直し 66 回のうち 53 回が `needs[].reason is required`。1 回 19 秒かかる。
    理由は読み出しで空を許している）。
    """
    properties: dict[str, Any] = {
        **_spec_properties(_PREDICATES),
        "reason": {"type": "string", "description": "Why this step, one short Japanese sentence"},
    }
    if levels > 0:
        properties["needs"] = {
            "type": "array",
            "maxItems": MAX_STEP_NEEDS,
            "description": "Prerequisites: what has to be there before this step, in order "
            "(a bucket needs iron). Skipped when this step is already met; leave empty when "
            "nothing is needed",
            "items": _step_schema(levels - 1, nested=True),
        }
    return {"type": "object", "properties": properties, "required": ["predicate"] if nested else ["predicate", "reason"]}


def plan_schema() -> dict[str, Any]:
    """計画の出力の形: 一言のあらすじ、中目標（段階）とその小目標の並び。"""
    return {
        "type": "object",
        "properties": {
            "outline": {
                "type": "string",
                "description": "First: how you will reach the objective, in stages, one or two "
                "Japanese sentences",
            },
            "jobs": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_JOBS,
                "description": "The stages (jobs) in order. The last one reaches the objective's "
                "conditions",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "The stage's name in Japanese, a noun phrase (e.g. "
                            "水源の確保). It is not the condition",
                        },
                        **_spec_properties(_PREDICATES),
                        "reason": {
                            "type": "string",
                            "description": "Why this stage, one Japanese sentence",
                        },
                        "steps": {
                            "type": "array",
                            "maxItems": MAX_STEPS,
                            "description": "The small goals of this stage in order, each one "
                            "concrete trip or piece of work, with its prerequisites (needs). The "
                            "stage's own condition comes last (or is the last step's)",
                            "items": _step_schema(MAX_STEP_DEPTH),
                        },
                    },
                    "required": ["title", "predicate", "reason", "steps"],
                },
            },
        },
        "required": ["outline", "jobs"],
    }


def _parse_step(raw: dict[str, Any]) -> Step:
    return Step(
        parse_spec(raw),
        str(raw.get("reason", "")).strip(),
        needs=tuple(_parse_step(n) for n in raw.get("needs") or []),
    )


def parse_plan(data: dict[str, Any]) -> tuple[Job, ...]:
    """
    計画（中目標と小目標）をパースする。

    Raises:
        ValueError: 中目標がない、名前がない、条件の形が正しくない、多すぎる・深すぎるとき
    """
    raws = data.get("jobs") or []
    if not raws:
        raise ValueError("write at least one job")
    jobs = []
    problems = []
    for i, raw in enumerate(raws[:MAX_JOBS], 1):
        # 見つかった間違いは全部まとめて言う（1 件ずつ差し戻すと、直したそばから次を踏んで書き直しが尽きる）
        errors = []
        title = str(raw.get("title", "")).strip()
        if not title:
            errors.append("a job needs a title (the stage's name)")
        steps = []
        for j, step in enumerate(raw.get("steps") or [], 1):
            try:
                steps.append(_parse_step(step))
            except ValueError as e:
                errors.append(f"step {j}: {e}")
        spec = None
        try:
            spec = parse_spec(raw)
            if spec.predicate not in STEP_PREDICATES:
                allowed = ", ".join(sorted(p.value for p in STEP_PREDICATES))
                errors.append(f"{spec.predicate.value} cannot be a job here (use {allowed})")
        except ValueError as e:
            errors.append(str(e))
        if errors:
            problems.append(f"job {i}: " + "; ".join(errors))
        else:
            jobs.append(Job(spec, str(raw.get("reason", "")).strip(), title=title, steps=tuple(steps)))
    if problems:
        raise ValueError("; ".join(problems))
    return tuple(jobs)


class JobPlanner:
    """一番上の大目標の計画（中目標と小目標）を書く。"""

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        objectives: ObjectiveKeeper,
        max_attempts: int = 3,
        guide_notes: str = "",
    ) -> None:
        """
        Args:
            text_generator: LLM（構造化出力。purpose=plan）
            prompt_builder: プロンプトの組み立て
            bridge: 書いた条件をブリッジが判定できるか確かめる
            objectives: 書いた計画を大目標に反映して保存する
            max_attempts: あきらめるまでに書かせる回数
            guide_notes: 手引きの「計画のための要点」（計画のプロンプトの固定の部分に入れる）
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._objectives = objectives
        self._max_attempts = max_attempts
        self._guide_notes = guide_notes

    async def plan(
        self,
        session: PlaySession,
        activity: Activity,
        replan_reason: str = "",
        keep_done_steps: bool = True,
    ) -> bool:
        """
        一番上の大目標の計画を書いて反映する。書けたか（書けなければ、今の中目標のまま）。

        Args:
            session: 今のセッション（一番上の大目標を読み、計画を反映する）
            activity: 今していることと状況（プロンプトに見せる）
            replan_reason: 書き直すときの理由（失敗や行き詰まり。初めてなら空）
            keep_done_steps: 済みにした小目標を、計画に残っているものだけ引き継ぐか
        """
        plan = session.plan
        current = plan.current
        if current is None:
            return False
        # 計画の条件で書けない完了条件（surveyed など）の大目標は、最後の中目標を書けない。無理に書かせると
        # 探索の代わりに整地や松明が並ぶので、書かずに Gemini の目標の決定に任せる（今までの経路）
        unwritable = [c.describe() for c in current.conditions if c.predicate not in STEP_PREDICATES]
        if unwritable:
            logger.info(f"計画は書かない: {current.title} の完了条件 {', '.join(unwritable)} は計画の条件で書けない")
            return False
        schema = plan_schema()
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_plan_prompt(
                current,
                activity,
                replan_reason=replan_reason,
                previous_error=error,
                guide_notes=self._guide_notes,
            )
            try:
                data = await self._text_generator.generate_json(prompt, schema, purpose="plan")
                jobs = own_condition_last(parse_plan(data), current)
                trees, met = await self._check_bridge(jobs, current)
                check_plan(jobs, current, await self._builds(), trees, met, await self._facility_names())
                await self._objectives.set_jobs(
                    plan, current.id, jobs, planned=True, keep_done_steps=keep_done_steps
                )
            except (ValueError, GoalRejectedError) as e:
                error = str(e)
                logger.warning(f"計画 {current.title} の試行 {attempt} を差し戻した: {error}")
                continue
            except (TextGenerationError, GameBridgeError) as e:
                # LLM が答えない（時間切れ）・ブリッジにつながらない: 同じ呼び出しを繰り返しても
                # 長く待つだけ。書けないまま、今までどおり Gemini の目標の決定に任せる
                logger.warning(f"計画を書けなかった: {current.title}: {e}")
                return False
            outline = str(data.get("outline", "")).strip()
            logger.info(
                f"計画を立てた: {current.title}（{outline}）"
                + " / ".join(
                    f"{j.title}: " + " → ".join(_outline(s) for s in j.steps) for j in jobs
                )
            )
            return True
        logger.warning(f"計画を書けなかった: {current.title}: {error}")
        return False

    async def _builds(self) -> Optional[set[str]]:
        """登録済みの建物の名前（分からなければ None: 建物の名前は確かめない）。"""
        try:
            return {str(b.get("name")) for b in await self._bridge.builds()}
        except AILoveShenError as e:
            logger.debug(f"建物の一覧を読めなかった: {e}")
            return None

    async def _facility_names(self) -> Optional[set[str]]:
        """登録済みの施設の名前（分からなければ None: 施設の名前は確かめない）。"""
        try:
            return {str(f.get("name")) for f in await self._bridge.facilities()}
        except AILoveShenError as e:
            logger.debug(f"施設の一覧を読めなかった: {e}")
            return None

    async def _check_bridge(
        self, jobs: tuple[Job, ...], objective: Objective
    ) -> tuple[dict[str, MaterialTree], dict[str, bool]]:
        """
        書いた条件をブリッジが判定できるか（知らない名前や形の誤りは、GoalRejectedError の理由を
        添えて差し戻す）。返すのは、条件の書き方から材料の木（材料の思い込みを検算するため。設計書 67）と、
        条件の書き方から今もう満たしているか（書いた瞬間に済む中目標を弾くため）。
        """
        specs = {}
        for job in jobs:
            for spec in job.specs:
                specs[spec.describe()] = spec
            for step in job.steps:
                for x in step.walk():
                    specs[x.key] = x.spec
        # 大目標の条件は、材料になる物のものだけ足す（ほかの条件は、判定できない理由でこの差し戻しを起こさない）
        for spec in objective.conditions:
            if spec.predicate in _MATERIAL_PREDICATES:
                specs.setdefault(spec.describe(), spec)
        statuses = await self._bridge.check(list(specs.values()), materials=True)
        trees = {
            key: status.materials
            for key, status in zip(specs, statuses, strict=True)
            if status.materials is not None
        }
        met = {key: status.met for key, status in zip(specs, statuses, strict=True)}
        return trees, met


def _outline(step: Step) -> str:
    """ログ用: 小目標と、その前提（前提を先に）。"""
    return "→".join(x.describe() for x in step.walk())


def unplanned(current: Optional[Objective]) -> bool:
    """計画を書く必要があるか: まだ書いていないか、中目標が尽きたのに大目標が済んでいない。"""
    return current is not None and (not current.planned or not current.open_jobs)
