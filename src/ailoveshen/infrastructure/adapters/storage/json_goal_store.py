"""今の小目標を JSON ファイルに保存するアダプター（docs/design/63）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.goal_store import IGoalStore, SavedGoal
from ailoveshen.domain.value_objects import Goal, GoalPredicate, GoalSpec

FORMAT = 1


class JsonGoalStore(IGoalStore):
    """
    今の小目標を 1 つの JSON ファイルに保存する。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても
    最後に完全に保存した内容が残る。
    """

    def __init__(self, path: Path | str) -> None:
        """
        Args:
            path: JSON ファイル（ディレクトリは最初の保存のときに作る）
        """
        self._path = Path(path)

    def load(self) -> Optional[SavedGoal]:
        """保存した小目標。ファイルがまだないか、読めない形なら None。"""
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("format") != FORMAT:
                return None
            return SavedGoal(
                goal=_goal(data["goal"]),
                phase=str(data["phase"]),
                steps_in_goal=int(data.get("steps_in_goal", 0)),
                consecutive_failures=int(data.get("consecutive_failures", 0)),
                stalled_steps=int(data.get("stalled_steps", 0)),
                least_remaining=data.get("least_remaining"),
                deaths_at_goal=data.get("deaths_at_goal"),
            )
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"小目標の保存を読めなかった（{self._path}）: {e}")
            return None

    def save(self, saved: SavedGoal) -> None:
        """小目標を保存する。"""
        data = {
            "format": FORMAT,
            "goal": _goal_dict(saved.goal),
            "phase": saved.phase,
            "steps_in_goal": saved.steps_in_goal,
            "consecutive_failures": saved.consecutive_failures,
            "stalled_steps": saved.stalled_steps,
            "least_remaining": saved.least_remaining,
            "deaths_at_goal": saved.deaths_at_goal,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)


def _spec_dict(spec: GoalSpec) -> dict[str, Any]:
    return {
        "predicate": spec.predicate.value,
        "item": spec.item,
        "count": spec.count,
        "where": spec.where,
        "distance": spec.distance,
        "dig_depth": spec.dig_depth,
        "name": spec.name,
    }


def _spec(data: dict[str, Any]) -> GoalSpec:
    return GoalSpec(
        predicate=GoalPredicate(data["predicate"]),
        item=data.get("item"),
        count=data.get("count"),
        where=data.get("where"),
        distance=data.get("distance"),
        dig_depth=data.get("dig_depth"),
        name=data.get("name"),
    )


def _goal_dict(goal: Goal) -> dict[str, Any]:
    return {
        "spec": _spec_dict(goal.spec),
        "reason": goal.reason,
        "objective_id": goal.objective_id,
        "set_at": goal.set_at.isoformat(),
        "diagnosis": goal.diagnosis,
        "advice": goal.advice,
        "review": goal.review,
        "lessons": list(goal.lessons),
        "together": [_spec_dict(s) for s in goal.together],
        "approach": goal.approach,
    }


def _goal(data: dict[str, Any]) -> Goal:
    return Goal(
        spec=_spec(data["spec"]),
        reason=data.get("reason", ""),
        objective_id=data.get("objective_id"),
        set_at=datetime.fromisoformat(data["set_at"]),
        diagnosis=data.get("diagnosis", ""),
        advice=data.get("advice", ""),
        review=data.get("review", ""),
        lessons=tuple(data.get("lessons", [])),
        together=tuple(_spec(s) for s in data.get("together", [])),
        approach=data.get("approach", ""),
    )
