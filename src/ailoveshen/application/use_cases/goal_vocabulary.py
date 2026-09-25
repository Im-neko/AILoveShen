"""The goal vocabulary as the LLM sees it: which predicates make sense now, the schema, parsing.

Shared by the goal decision and the chat replies (a reply that promises an action carries a goal
in the same vocabulary), so both offer exactly the same goals.
"""

from __future__ import annotations

from typing import Any, Optional

from ailoveshen.domain.value_objects import GameObservation, Goal, GoalPredicate, GoalSpec

MAX_GOAL_COUNT = 64
MIN_EXPLORE_DISTANCE = 8
MAX_EXPLORE_DISTANCE = 128


def goal_schema(predicates: list[GoalPredicate]) -> dict[str, Any]:
    """JSON schema of a goal limited to the given predicates."""
    return {
        "type": "object",
        "properties": {
            "predicate": {"type": "string", "enum": [p.value for p in predicates]},
            "item": {
                "type": "string",
                "description": "have / placed: an item or group, e.g. planks, log, bed, food, "
                "crafting_table, stick, wooden_sword, wooden_pickaxe",
            },
            "count": {"type": "integer", "minimum": 1, "maximum": MAX_GOAL_COUNT},
            "distance": {
                "type": "integer",
                "minimum": MIN_EXPLORE_DISTANCE,
                "maximum": MAX_EXPLORE_DISTANCE,
            },
            "reason": {"type": "string", "description": "Why this goal now, in one short sentence"},
        },
        "required": ["predicate", "reason"],
    }


def goal_properties(predicates: list[GoalPredicate]) -> dict[str, Any]:
    """The goal's properties, to embed in another schema (the chat reply)."""
    return goal_schema(predicates)["properties"]


def parse_goal(data: dict[str, Any], requested_by: Optional[str] = None) -> Goal:
    """
    Parse a goal from the model's JSON.

    Raises:
        ValueError: If an argument is missing or malformed.
    """
    try:
        predicate = GoalPredicate(data["predicate"])
        spec = GoalSpec(
            predicate=predicate,
            item=str(data["item"])
            if predicate in (GoalPredicate.HAVE, GoalPredicate.PLACED)
            else None,
            count=int(data["count"]) if predicate == GoalPredicate.HAVE else None,
            where="home" if predicate == GoalPredicate.PLACED else None,
            distance=int(data["distance"]) if predicate == GoalPredicate.EXPLORED else None,
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"missing or malformed argument: {e}") from e
    return Goal(spec=spec, reason=str(data.get("reason", "")), requested_by=requested_by)


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
    out.append(GoalPredicate.EXPLORED)
    return out


def reply_schema(predicates: list[GoalPredicate]) -> dict[str, Any]:
    """
    JSON schema of a chat reply that may take the viewer's request as a goal.

    The reply and the goal change come from one generation, so a reply that
    promises an action always carries the goal.
    """
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string", "description": "What to say to the viewer"},
            "change_goal": {
                "type": "boolean",
                "description": "true when the reply promises to do something now",
            },
            **goal_properties(predicates),
        },
        "required": ["reply", "change_goal"],
    }
