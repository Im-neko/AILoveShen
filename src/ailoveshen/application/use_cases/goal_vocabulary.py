"""The goal vocabulary as the LLM sees it: which predicates make sense now, the schemas, parsing.

Shared by the goal decision and the chat replies, so both speak of goals the same way:
- the small goal (one predicate) and whether it serves the mid goal at the top of the list or
  survival, with the changes to the mid-goal list decided with it
- a mid goal's completion conditions (predicates judged from the world alone), used both by the
  goal decision's changes and by a reply that accepts a viewer's request
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
# The order the conditions are offered in (a frozenset has none)
_CONDITION_ORDER = [p for p in GoalPredicate if p in CONDITION_PREDICATES]


class Serves(str, Enum):
    """What a small goal is for."""

    CURRENT = "current"  # the mid goal at the top of the list (after the changes)
    SURVIVAL = "survival"  # staying alive: the night, home, the door, food


class PlanOp(str, Enum):
    """An edit of the mid-goal list."""

    ADD = "add"
    MOVE = "move"
    DROP = "drop"


class RequestHandling(str, Enum):
    """What a reply does with the viewer's comment."""

    NONE = "none"  # chat or a question
    ACCEPT = "accept"  # added to the mid goals
    DECLINE = "decline"  # the reply says why not


@dataclass(frozen=True)
class MidGoalProposal:
    """A mid goal to add: `position` 0-based among the pending ones (None: last)."""

    title: str
    conditions: tuple[GoalSpec, ...]
    reason: str = ""
    position: Optional[int] = None


@dataclass(frozen=True)
class PlanChange:
    """One edit of the mid-goal list (`proposal` for add, `mid_goal_id` for move and drop)."""

    op: PlanOp
    reason: str
    mid_goal_id: str = ""
    position: Optional[int] = None  # 0-based
    proposal: Optional[MidGoalProposal] = None


@dataclass(frozen=True)
class GoalDecision:
    """The next small goal, what it serves, and the mid-goal list edits made with it."""

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
    """JSON schema of a goal decision: the small goal and the mid-goal list edits."""
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
    JSON schema of a chat reply and what it does with the comment.

    The reply and its handling come from one generation, so a reply that
    accepts a request always adds it to the mid goals.
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
    Parse one predicate with its arguments.

    Raises:
        ValueError: If an argument is missing or malformed.
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
    Parse a mid goal to add (title, conditions, 1-based position, reason).

    Raises:
        ValueError: If the title or conditions are missing or malformed.
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
    Parse a goal decision.

    Raises:
        ValueError: If the goal or an edit is missing or malformed.
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
    """The predicates that make sense now (e.g. none about the home before it exists)."""
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
