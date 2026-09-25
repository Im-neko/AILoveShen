"""目標ボード: 配信者の目標を、配信オーバーレイのために外から読めるようにする。

- GET /api/goals         今の目標（JSON）
- GET /api/goals/stream  目標が変わるたび、ステップのたびに同じ JSON（Server-Sent Events）
- GET /overlay           OBS のブラウザソース用のページ（背景は透明）

読み取り専用: プレイセッションが持つものを出すだけで、変えることはない。プレイのループと
同じプロセスで動く（`GoalBoard.serve`）。設計: docs/design/13_goal_hierarchy.md §7。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventSubscriber
from ailoveshen.domain.events import (
    DomainEvent,
    GameActionExecutedEvent,
    GoalEndedEvent,
    GoalSetEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
    TownSiteChosenEvent,
)
from ailoveshen.domain.value_objects import Activity, GameObservation, MidGoal, MidGoalState

OVERLAY_HTML = Path(__file__).with_name("overlay.html")
KEEPALIVE_SECONDS = 15.0
QUEUE_SIZE = 16
# ボードの表示が変わるイベントすべて（ステップで小目標の進み具合が変わる）
EVENTS: tuple[type[DomainEvent], ...] = (
    GoalSetEvent,
    GoalEndedEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
    TownSiteChosenEvent,
    GameActionExecutedEvent,
)


def goals_snapshot(activity: Activity | None) -> dict[str, Any]:
    """ボードに出す形の目標（/api/goals の JSON）。"""
    if activity is None:
        return {
            "playing": False,
            "mission": None,
            "town": None,
            "site": None,
            "survey": None,
            "mid_goals": [],
            "goal": None,
            "home": None,
        }
    pending = [g for g in activity.mid_goals if g.state == MidGoalState.PENDING]
    finished = [g for g in activity.mid_goals if g.state != MidGoalState.PENDING]
    titles = {g.id: g.title for g in activity.mid_goals}
    goal = activity.goal
    obs = activity.observation
    status = obs.goal if obs else None
    return {
        "playing": True,
        "mission": activity.mission.text if activity.mission else None,
        "town": _town(activity),
        # 決めたこと（配信者）と、調べた事実（ブリッジが測った候補地の数字）
        "site": asdict(activity.site) if activity.site else None,
        "survey": (obs.state.get("survey") if obs else None) or None,
        "mid_goals": [
            _mid_goal(g, "current" if i == 0 else "pending") for i, g in enumerate(pending)
        ]
        + [_mid_goal(g, g.state.value) for g in finished],
        "goal": None
        if goal is None
        else {
            "goal": goal.spec.describe(),
            "reason": goal.reason,
            "mid_goal": titles.get(goal.mid_goal_id) if goal.mid_goal_id else None,
            "survival": goal.mid_goal_id is None,
            "progress": list(status.lines) if status else [],
        },
        "home": _home(obs),
    }


def _town(activity: Activity) -> dict[str, Any] | None:
    town = activity.town
    if town is None:
        return None
    return {
        "text": town.text,
        "complete": activity.town_stage >= len(town.stages),
        "stages": [
            {
                "title": s.title,
                "why": s.why,
                # done / current / later
                "state": "done"
                if i < activity.town_stage
                else "current"
                if i == activity.town_stage
                else "later",
                "conditions": [c.describe() for c in s.conditions],
                "met": [c.describe() for c in s.conditions if c in activity.stage_met]
                if i == activity.town_stage
                else [],
                "unresolved": list(s.unresolved),
            }
            for i, s in enumerate(town.stages)
        ],
    }


def _home(obs: GameObservation | None) -> dict[str, Any] | None:
    if obs is None or not obs.has_home:
        return None
    chests = (obs.state.get("memory") or {}).get("chests", [])
    return {
        "name": (obs.state.get("home") or {}).get("name"),
        "bed": obs.bed_in_home,
        # 最後に開けたときの中身（使うのは配信者だけ）
        "chests": [{"contents": c["contents"], "minutes_ago": c["minutes_ago"]} for c in chests],
    }


def _mid_goal(goal: MidGoal, state: str) -> dict[str, Any]:
    return {
        "id": goal.id,
        "title": goal.title,
        "state": state,  # current / pending / done / dropped
        "requested_by": goal.requested_by,
        "stage": goal.stage,  # 対応する街の段階（インデックス）。なければ None
        "prepares_town": goal.prepares_town,  # 街の準備（候補地の調査、引っ越し）
        "conditions": [c.describe() for c in goal.conditions],
        "progress": list(goal.progress),
        "summary": list(goal.summary()),  # ソルバーの細かい手順は除く
        "ended_because": goal.ended_because,
    }


class GoalBoard:
    """プレイ中のセッションの目標を配信オーバーレイに出す。"""

    def __init__(self, activity: Callable[[], Activity | None]) -> None:
        """
        ボードを初期化する。

        Args:
            activity: 配信者が今していること（ゲームのセッションの activity。プレイを
                始める前は None）
        """
        self._activity = activity
        self._listeners: set[asyncio.Queue[str]] = set()
        self.app = self._create_app()

    def subscribe(self, bus: IEventSubscriber) -> None:
        """変わるたびに、接続中のオーバーレイへ目標を送る。"""
        for event_type in EVENTS:
            bus.subscribe(event_type, self._on_event)

    async def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        """キャンセルされるまで Web サーバーを動かす。"""
        server = uvicorn.Server(uvicorn.Config(self.app, host=host, port=port, log_level="warning"))
        logger.info(f"目標ボード: http://{host}:{port}/overlay")
        await server.serve()

    def snapshot(self) -> dict[str, Any]:
        """今の目標。"""
        return goals_snapshot(self._activity())

    async def _on_event(self, event: DomainEvent) -> None:
        data = json.dumps(self.snapshot(), ensure_ascii=False)
        for queue in self._listeners:
            if queue.full():
                queue.get_nowait()  # 遅いオーバーレイには最新のものだけあればよい
            queue.put_nowait(data)

    async def _stream(self, request: Request) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._listeners.add(queue)
        try:
            yield f"data: {json.dumps(self.snapshot(), ensure_ascii=False)}\n\n"
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {data}\n\n"
        finally:
            self._listeners.discard(queue)

    def _create_app(self) -> FastAPI:
        app = FastAPI(title="AILoveShen goal board")

        @app.get("/api/goals")
        async def goals() -> dict[str, Any]:
            return self.snapshot()

        @app.get("/api/goals/stream")
        async def stream(request: Request) -> StreamingResponse:
            return StreamingResponse(
                self._stream(request),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        @app.get("/overlay", response_class=HTMLResponse)
        async def overlay() -> str:
            return OVERLAY_HTML.read_text(encoding="utf-8")

        return app
