"""JSON file mission store adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.mission_store import IMissionStore, SavedPlan
from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import GoalPredicate, GoalSpec, MidGoal, MidGoalState, Mission


class JsonMissionStore(IMissionStore):
    """
    Keeps the mission and its mid goals in one JSON file.

    Written to a temporary file and renamed, so a crash mid-write leaves the
    last complete save.
    """

    def __init__(self, path: Path | str) -> None:
        """
        Initialize the store.

        Args:
            path: The JSON file (its directory is created on the first save)
        """
        self._path = Path(path)

    def load(self) -> Optional[SavedPlan]:
        """The saved plan, or None when there is no file yet."""
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        saved = SavedPlan(
            mission=Mission(text=data["mission"]),
            pending=tuple(_mid_goal(g) for g in data["pending"]),
            finished=tuple(_mid_goal(g) for g in data["finished"]),
            next_id=int(data["next_id"]),
        )
        logger.info(f"Mid goals loaded from {self._path}")
        return saved

    def save(self, plan: MidGoalPlan) -> None:
        """Save the plan."""
        data = {
            "mission": plan.mission.text,
            "pending": [_to_dict(g) for g in plan.pending],
            "finished": [_to_dict(g) for g in plan.finished],
            "next_id": plan.next_id,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)


def _to_dict(goal: MidGoal) -> dict[str, Any]:
    return {
        "id": goal.id,
        "title": goal.title,
        "conditions": [c.to_dict() for c in goal.conditions],
        "reason": goal.reason,
        "requested_by": goal.requested_by,
        "state": goal.state.value,
        "ended_because": goal.ended_because,
        "steps": goal.steps,
        "progress": list(goal.progress),
    }


def _mid_goal(data: dict[str, Any]) -> MidGoal:
    return MidGoal(
        id=data["id"],
        title=data["title"],
        conditions=tuple(
            GoalSpec(
                predicate=GoalPredicate(c["predicate"]),
                item=c.get("item"),
                count=c.get("count"),
                where=c.get("where"),
                distance=c.get("distance"),
            )
            for c in data["conditions"]
        ),
        reason=data.get("reason", ""),
        requested_by=data.get("requested_by"),
        state=MidGoalState(data.get("state", MidGoalState.PENDING.value)),
        ended_because=data.get("ended_because", ""),
        steps=int(data.get("steps", 0)),
        progress=tuple(data.get("progress", [])),
    )
