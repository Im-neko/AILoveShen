"""小目標の終わりを 1 件ずつ構造化して残す（実行の成績の集計用。docs/design/82）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ailoveshen.application.use_cases.lessons import OBJECTIVE_STALLED
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.value_objects import GameObservation

# 終わり方の分類（成功率は met / (met + failed + died)。cut は成功でも失敗でもないので別に数える）
MET = "met"
FAILED = "failed"
DIED = "died"
CUT = "cut"

CATEGORIES = (MET, FAILED, DIED, CUT)


def end_category(reason: str) -> str:
    """
    小目標の終わり方の分類（goal_end_reason の文から）。

    - met: 達成した
    - failed: 行き詰まった、進まなかった、ステップの予算を使い切った
    - died: 死んでリスポーンした
    - cut: 成否と関係なく切った（時間帯の変化、夜が明けた、考え直し、大目標が終わった）

    大目標が進まない印（`; ... has made no progress`）は後ろに足されるだけなので、小目標自身の
    終わり（最初の節）で分ける。
    """
    head = reason.split("; ")[0]
    if " is cut because the streamer died" in head:
        return DIED
    if " is reconsidered: " in head:
        return CUT
    if head.endswith(" is met"):
        return MET
    if " is stuck" in head or " stalled " in head or " ran for " in head:
        return FAILED
    if OBJECTIVE_STALLED in head:
        return FAILED
    return CUT


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def goal_end_entry(
    session: PlaySession, obs: GameObservation, reason: str, now: datetime
) -> dict[str, Any]:
    """
    いま終わる小目標の 1 件の記録。`session.set_goal` が歩みの数えを消す前に作る。

    Args:
        session: 終わる小目標を持つセッション（目標がなければ呼ばない）
        obs: 終わりの観測
        reason: `goal_end_reason` の文
        now: 終わった時刻（UTC）
    """
    goal = session.goal
    assert goal is not None
    spec = goal.spec
    thing = spec.item or spec.name or spec.where
    objective = session.plan.get(goal.objective_id) if goal.objective_id else None
    deaths_now = _count(obs.state.get("deaths"))
    deaths_then = session.deaths_at_goal
    # 再起動で引き継いだ小目標は、止まっていた間（数時間のこともある）を所要時間に数えず、続きから追い始めた
    # 時刻から測る（歩数と死亡は引き継いだ数えのまま）
    started = session.goal_resumed_at or goal.set_at
    return {
        "event": "goal_end",
        "goal": goal.describe(),
        "predicate": spec.predicate.value,
        # 同じ種類の小目標を数を無視してまとめるための鍵（have(food, 16) と have(food, 4) は同じ）
        "kind": f"{spec.predicate.value}({thing})" if thing else spec.predicate.value,
        "together": len(goal.together),
        "survival": goal.objective_id is None,
        "objective": objective.title if objective is not None else None,
        "playbook": objective.playbook if objective is not None else None,
        "viewer": bool(objective is not None and objective.requested_by),
        "category": end_category(reason),
        "reason": reason,
        "resumed": session.goal_resumed_at is not None,
        "started_at": started.isoformat(),
        "ended_at": now.isoformat(),
        "seconds": round((now - started).total_seconds(), 1),
        "phase_start": session.goal_phase,
        "phase_end": obs.time_phase,
        "steps": session.steps_in_goal,
        "consecutive_failures": session.consecutive_failures,
        "stalled_steps": session.stalled_steps,
        "least_remaining": session.least_remaining,
        "remaining_end": obs.goal.remaining if obs.goal is not None else None,
        "deaths": (
            deaths_now - deaths_then if deaths_now is not None and deaths_then is not None else None
        ),
    }
