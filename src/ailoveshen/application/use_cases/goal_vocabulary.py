"""LLM から見た目標の語彙: 今意味のある述語、スキーマ、パース。

目標の決定とチャットの返答が共有する。こうして両方が同じ言い方で目標を語る:
- 小目標（述語 1 つ）と、それがリストの一番上の中目標のためか生存のためか。一緒に決めた
  中目標リストへの変更も含む
- 中目標の完了条件（世界だけから判定する述語）。目標の決定での変更と、視聴者の頼みを受ける
  返答の両方で使う
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ailoveshen.domain.value_objects import (
    CONDITION_PREDICATES,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    TownDefinition,
    TownStage,
)

MAX_GOAL_COUNT = 64
MIN_EXPLORE_DISTANCE = 8
MAX_EXPLORE_DISTANCE = 128
MAX_CONDITIONS = 3
MAX_PLAN_CHANGES = 3
ITEM_DESCRIPTION = (
    "have / stored / placed: an item or group, e.g. planks, log, bed, food, crafting_table, stick, "
    "wooden_sword, wooden_pickaxe"
)
# 条件を示す順番（frozenset には順番がない）
_CONDITION_ORDER = [p for p in GoalPredicate if p in CONDITION_PREDICATES]


class Serves(str, Enum):
    """小目標が何のためか。"""

    CURRENT = "current"  # リストの一番上の中目標（変更のあと）
    SURVIVAL = "survival"  # 生き延びること: 夜、家、扉、食料


class PlanOp(str, Enum):
    """中目標リストの編集。"""

    ADD = "add"
    MOVE = "move"
    DROP = "drop"


class RequestHandling(str, Enum):
    """返答が視聴者のコメントをどう扱うか。"""

    NONE = "none"  # 雑談か質問
    ACCEPT = "accept"  # 中目標に足した
    DECLINE = "decline"  # 受けない理由を返答で言う


@dataclass(frozen=True)
class MidGoalProposal:
    """足す中目標: `position` は未完了のものの中での 0 始まりの位置（None: 最後）。"""

    title: str
    conditions: tuple[GoalSpec, ...]
    reason: str = ""
    position: Optional[int] = None


@dataclass(frozen=True)
class PlanChange:
    """中目標リストの 1 つの編集（add は `proposal`、move と drop は `mid_goal_id`）。"""

    op: PlanOp
    reason: str
    mid_goal_id: str = ""
    position: Optional[int] = None  # 0 始まり
    proposal: Optional[MidGoalProposal] = None


@dataclass(frozen=True)
class GoalDecision:
    """次の小目標、それが何のためか、一緒に行う中目標リストの編集。"""

    spec: GoalSpec
    reason: str
    serves: Serves
    changes: tuple[PlanChange, ...] = ()


def _spec_properties(predicates: list[GoalPredicate]) -> dict[str, Any]:
    return {
        "predicate": {"type": "string", "enum": [p.value for p in predicates]},
        "item": {"type": "string", "description": ITEM_DESCRIPTION},
        "count": {"type": "integer", "minimum": 1, "maximum": MAX_GOAL_COUNT},
        "distance": {
            "type": "integer",
            "minimum": MIN_EXPLORE_DISTANCE,
            "maximum": MAX_EXPLORE_DISTANCE,
            "description": "explored: blocks to go; lit: the radius around the home (8-32)",
        },
    }


def _conditions_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "description": "All must hold for the mid goal to be done (judged from the world)",
        "minItems": 1,
        "maxItems": MAX_CONDITIONS,
        "items": {
            "type": "object",
            "properties": _spec_properties(_CONDITION_ORDER),
            "required": ["predicate"],
        },
    }


def goal_schema(predicates: list[GoalPredicate], mid_goal_ids: list[str]) -> dict[str, Any]:
    """目標の決定の JSON スキーマ: 小目標と、中目標リストの編集。"""
    change: dict[str, Any] = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": [o.value for o in PlanOp]},
            "title": {"type": "string", "description": "add: the mid goal's title"},
            "conditions": _conditions_schema(),
            "position": {
                "type": "integer",
                "minimum": 1,
                "description": "add / move: its place in the list, 1 = worked on first",
            },
            "reason": {"type": "string", "description": "Why, in one short sentence"},
        },
        "required": ["op", "reason"],
    }
    if mid_goal_ids:
        change["properties"]["id"] = {
            "type": "string",
            "enum": mid_goal_ids,
            "description": "move / drop: the mid goal",
        }
    return {
        "type": "object",
        "properties": {
            "plan_changes": {
                "type": "array",
                "description": "Edits of the mid-goal list, applied in order (usually none)",
                "maxItems": MAX_PLAN_CHANGES,
                "items": change,
            },
            **_spec_properties(predicates),
            "serves": {
                "type": "string",
                "enum": [s.value for s in Serves],
                "description": "current: for the mid goal at the top of the list after the "
                "edits; survival: to stay alive (the night, home, the door, food)",
            },
            "reason": {"type": "string", "description": "Why this goal now, in one short sentence"},
        },
        "required": ["predicate", "serves", "reason"],
    }


def reply_schema() -> dict[str, Any]:
    """
    チャットの返答と、それがコメントをどう扱うかの JSON スキーマ。

    返答と扱いは 1 回の生成から出るので、頼みを受けた返答は必ずそれを中目標に足す。
    """
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string", "description": "What to say to the viewer"},
            "request": {
                "type": "string",
                "enum": [r.value for r in RequestHandling],
                "description": "none: chat or a question; accept: add the request to the mid "
                "goals; decline: not taken (the reply says why)",
            },
            "title": {"type": "string", "description": "accept: the mid goal's title"},
            "conditions": _conditions_schema(),
            "position": {
                "type": "integer",
                "minimum": 2,
                "description": "accept: its place in the list (2 = right after the one worked "
                "on now)",
            },
            "reason": {"type": "string", "description": "accept: why it helps, in one sentence"},
        },
        "required": ["reply", "request"],
    }


def parse_spec(data: dict[str, Any]) -> GoalSpec:
    """
    述語 1 つを引数と一緒にパースする。

    Raises:
        ValueError: 引数がないか、形が正しくないとき。
    """
    try:
        predicate = GoalPredicate(data["predicate"])
        return GoalSpec(
            predicate=predicate,
            item=str(data["item"])
            if predicate in (GoalPredicate.HAVE, GoalPredicate.STORED, GoalPredicate.PLACED)
            else None,
            count=int(data["count"])
            if predicate in (GoalPredicate.HAVE, GoalPredicate.STORED)
            else None,
            where="home" if predicate == GoalPredicate.PLACED else None,
            distance=int(data["distance"])
            if predicate in (GoalPredicate.EXPLORED, GoalPredicate.LIT)
            else None,
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"missing or malformed argument: {e}") from e


def parse_proposal(data: dict[str, Any]) -> MidGoalProposal:
    """
    足す中目標をパースする（題名、条件、1 始まりの位置、理由）。

    Raises:
        ValueError: 題名か条件がないか、形が正しくないとき。
    """
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("a mid goal needs a title")
    raw = data.get("conditions") or []
    if not raw:
        raise ValueError(f"mid goal {title} needs conditions")
    conditions = tuple(parse_spec(c) for c in raw)
    for c in conditions:
        if c.predicate not in CONDITION_PREDICATES:
            raise ValueError(f"{c.predicate.value} cannot be a condition of a mid goal")
    position = data.get("position")
    return MidGoalProposal(
        title=title,
        conditions=conditions,
        reason=str(data.get("reason", "")),
        position=int(position) - 1 if position is not None else None,
    )


def parse_decision(data: dict[str, Any]) -> GoalDecision:
    """
    目標の決定をパースする。

    Raises:
        ValueError: 目標か編集がないか、形が正しくないとき。
    """
    try:
        serves = Serves(data["serves"])
    except (KeyError, ValueError) as e:
        raise ValueError(f"serves must be one of {[s.value for s in Serves]}: {e}") from e
    changes = []
    for raw in data.get("plan_changes") or []:
        try:
            op = PlanOp(raw["op"])
        except (KeyError, ValueError) as e:
            raise ValueError(f"plan change op must be one of {[o.value for o in PlanOp]}") from e
        reason = str(raw.get("reason", ""))
        position = raw.get("position")
        if op == PlanOp.ADD:
            changes.append(PlanChange(op=op, reason=reason, proposal=parse_proposal(raw)))
            continue
        mid_goal_id = str(raw.get("id", ""))
        if not mid_goal_id:
            raise ValueError(f"{op.value} needs the id of a mid goal")
        if op == PlanOp.MOVE and position is None:
            raise ValueError(f"move {mid_goal_id} needs a position")
        changes.append(
            PlanChange(
                op=op,
                reason=reason,
                mid_goal_id=mid_goal_id,
                position=int(position) - 1 if position is not None else None,
            )
        )
    return GoalDecision(
        spec=parse_spec(data),
        reason=str(data.get("reason", "")),
        serves=serves,
        changes=tuple(changes),
    )


def predicates_now(obs: GameObservation) -> list[GoalPredicate]:
    """今意味のある述語（例: 家ができる前は、家についての述語は出さない）。"""
    out = []
    if obs.has_plan and not obs.house_complete:
        out.append(GoalPredicate.BUILT)
    out.append(GoalPredicate.HAVE)
    if obs.has_home:
        out += [GoalPredicate.AT_HOME, GoalPredicate.THROUGH_NIGHT]
        if obs.time_phase == "day":
            out.append(GoalPredicate.CLEARED)
        if not obs.bed_in_home:
            out.append(GoalPredicate.PLACED)
        out.append(GoalPredicate.STORED)
        out.append(GoalPredicate.LIT)
    out.append(GoalPredicate.EXPLORED)
    return out


MAX_TOWN_STAGES = 5


def _stage_properties() -> dict[str, Any]:
    return {
        "conditions": {
            **_conditions_schema(),
            "minItems": 0,
            "description": "What is judged done from the world, with the conditions available now",
        },
        "unresolved": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Parts that cannot be written with the conditions available now "
            "(each with what would be needed to do or judge it)",
        },
    }


def town_schema() -> dict[str, Any]:
    """街の定義の JSON スキーマ: 文と、順に並べた段階。"""
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What the town is, in 2-3 sentences"},
            "stages": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_TOWN_STAGES,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "why": {"type": "string", "description": "Why the town needs it"},
                        **_stage_properties(),
                    },
                    "required": ["title", "why", "conditions", "unresolved"],
                },
            },
        },
        "required": ["text", "stages"],
    }


def stage_schema() -> dict[str, Any]:
    """今使える条件で書き直した段階の JSON スキーマ。"""
    return {
        "type": "object",
        "properties": _stage_properties(),
        "required": ["conditions", "unresolved"],
    }


def parse_stage(data: dict[str, Any], title: str, why: str) -> TownStage:
    """
    段階の条件と、まだ解決していない部分をパースする。

    Raises:
        ValueError: 条件の形が正しくないか、世界から判定できないとき
    """
    conditions = tuple(parse_spec(c) for c in data.get("conditions") or [])
    for c in conditions:
        if c.predicate not in CONDITION_PREDICATES:
            raise ValueError(f"{c.predicate.value} cannot be a condition of a town stage")
    unresolved = tuple(str(u).strip() for u in data.get("unresolved") or [] if str(u).strip())
    return TownStage(title=title, why=why, conditions=conditions, unresolved=unresolved)


def parse_town(data: dict[str, Any]) -> TownDefinition:
    """
    街の定義をパースする。

    Raises:
        ValueError: 文か段階がないか、形が正しくないとき
    """
    stages = []
    for raw in data.get("stages") or []:
        title = str(raw.get("title", "")).strip()
        if not title:
            raise ValueError("a town stage needs a title")
        stages.append(parse_stage(raw, title, str(raw.get("why", ""))))
    return TownDefinition(text=str(data.get("text", "")).strip(), stages=tuple(stages))
