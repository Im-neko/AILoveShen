"""大目標を JSON ファイルに保存するアダプター。"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.mission_store import IMissionStore, SavedPlan
from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import (
    GoalPredicate,
    GoalSpec,
    JobState,
    MidGoal,
    MidGoalState,
    Mission,
    PlannedStep,
    TownDefinition,
    TownMap,
    TownSite,
    TownStage,
    Zone,
)

# 保存の形の版。違うファイルは読まない（互換のための移行はしない。最初のリストで始める）
FORMAT = 2


class JsonMissionStore(IMissionStore):
    """
    大目標、その中目標、街とその場所を 1つの JSON ファイルに保存する。

    一時ファイルに書いてから名前を変えるので、書き込み中に落ちても
    最後に完全に保存した内容が残る。
    """

    def __init__(self, path: Path | str) -> None:
        """
        ストアを初期化する。

        Args:
            path: JSON ファイル（ディレクトリは最初の保存のときに作る）
        """
        self._path = Path(path)

    def load(self) -> Optional[SavedPlan]:
        """
        保存した計画。ファイルがまだなければ None。読めない形（版が違う、壊れている）でも例外にせず
        None にする: 最初のリストで始め、次の保存で上書きするので、その前に脇へ写しておく。
        """
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("format") != FORMAT:
                raise ValueError(f"format {data.get('format')!r}, not {FORMAT}")
            saved = SavedPlan(
                mission=Mission(text=data["mission"]),
                pending=tuple(_mid_goal(g) for g in data["pending"]),
                finished=tuple(_mid_goal(g) for g in data["finished"]),
                next_id=int(data["next_id"]),
                next_step_id=int(data["next_step_id"]),
                town=_town(data["town"]) if data.get("town") else None,
                town_stage=int(data.get("town_stage", 0)),
                stage_met=tuple(_spec(c) for c in data.get("stage_met", [])),
                site=TownSite(**data["site"]) if data.get("site") else None,
                town_map=_town_map(data["town_map"]) if data.get("town_map") else None,
            )
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            aside = self._path.with_suffix(self._path.suffix + ".unreadable")
            shutil.copy2(self._path, aside)
            logger.warning(
                f"{self._path} は今の形で読めない（{type(e).__name__}: {e}）。最初の中目標で始める"
                f"（元のファイルは {aside} に残した）"
            )
            return None
        logger.info(f"中目標を {self._path} から読み込んだ")
        return saved

    def save(self, plan: MidGoalPlan) -> None:
        """計画を保存する。"""
        data = {
            "format": FORMAT,
            "mission": plan.mission.text,
            "pending": [_to_dict(g) for g in plan.pending],
            "finished": [_to_dict(g) for g in plan.finished],
            "next_id": plan.next_id,
            "next_step_id": plan.next_step_id,
            "town": _town_dict(plan.town) if plan.town else None,
            "town_stage": plan.town_stage,
            "stage_met": [c.to_dict() for c in plan.stage_met],
            "site": asdict(plan.site) if plan.site else None,
            "town_map": asdict(plan.town_map) if plan.town_map else None,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)


def _step_to_dict(step: PlannedStep) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": step.id,
        "state": step.state.value,
        "spec": step.spec.to_dict(),
        "reason": step.reason,
        "requested_by": step.requested_by,
        "suggested_by": step.suggested_by,
        "steps": step.steps,
        "progress": list(step.progress),
    }
    if step.together:
        # 小目標の束（docs/design/44）
        out["together"] = [t.to_dict() for t in step.together]
        out["approach"] = step.approach
    return out


def _to_dict(goal: MidGoal) -> dict[str, Any]:
    return {
        "id": goal.id,
        "title": goal.title,
        "conditions": [c.to_dict() for c in goal.conditions],
        "reason": goal.reason,
        "requested_by": goal.requested_by,
        "suggested_by": goal.suggested_by,
        "state": goal.state.value,
        "ended_because": goal.ended_because,
        "steps": goal.steps,
        "budget": goal.budget,
        "plan_steps": [_step_to_dict(s) for s in goal.plan_steps],
        "progress": list(goal.progress),
        "stage": goal.stage,
        "prepares_town": goal.prepares_town,
    }


def _town_dict(town: TownDefinition) -> dict[str, Any]:
    return {
        "text": town.text,
        "stages": [
            {
                "title": s.title,
                "why": s.why,
                "conditions": [c.to_dict() for c in s.conditions],
                "unresolved": list(s.unresolved),
                "tried_with": s.tried_with,
            }
            for s in town.stages
        ],
    }


def _town(data: dict[str, Any]) -> TownDefinition:
    return TownDefinition(
        text=data["text"],
        stages=tuple(
            TownStage(
                title=s["title"],
                why=s.get("why", ""),
                conditions=tuple(_spec(c) for c in s.get("conditions", [])),
                unresolved=tuple(s.get("unresolved", [])),
                tried_with=s.get("tried_with", ""),
            )
            for s in data["stages"]
        ),
    )


def _spec(c: dict[str, Any]) -> GoalSpec:
    return GoalSpec(
        predicate=GoalPredicate(c["predicate"]),
        item=c.get("item"),
        count=c.get("count"),
        where=c.get("where"),
        distance=c.get("distance"),
        name=c.get("name"),
    )


def _mid_goal(data: dict[str, Any]) -> MidGoal:
    return MidGoal(
        id=data["id"],
        title=data["title"],
        conditions=tuple(_spec(c) for c in data["conditions"]),
        reason=data.get("reason", ""),
        requested_by=data.get("requested_by"),
        suggested_by=data.get("suggested_by"),
        state=MidGoalState(data.get("state", MidGoalState.PENDING.value)),
        ended_because=data.get("ended_because", ""),
        steps=int(data.get("steps", 0)),
        budget=int(data["budget"]) if data.get("budget") else None,
        plan_steps=tuple(
            PlannedStep(
                _spec(s["spec"]),
                s.get("reason", ""),
                together=tuple(_spec(t) for t in s.get("together", [])),
                approach=s.get("approach", ""),
                id=s["id"],
                state=JobState(s["state"]),
                requested_by=s.get("requested_by"),
                suggested_by=s.get("suggested_by"),
                steps=int(s.get("steps", 0)),
                progress=tuple(s.get("progress", [])),
            )
            for s in data.get("plan_steps", [])
        ),
        progress=tuple(data.get("progress", [])),
        stage=data.get("stage"),
        prepares_town=bool(data.get("prepares_town", False)),
    )


def _town_map(data: dict[str, Any]) -> TownMap:
    return TownMap(
        center_x=int(data["center_x"]),
        center_z=int(data["center_z"]),
        zones=tuple(Zone(**z) for z in data["zones"]),
        concept=data.get("concept", ""),
        stage=int(data.get("stage", 0)),
        history=tuple(data.get("history", [])),
    )
