"""Goal board: the streamer's goals, readable from outside for the stream overlay.

- GET /api/goals         the goals now (JSON)
- GET /api/goals/stream  the same JSON on every goal change and step (Server-Sent Events)
- GET /overlay           a page for an OBS browser source (transparent background)

Read-only: it serves what the play session holds and never changes it. It runs in the same
process as the play loop (`GoalBoard.serve`). Design: docs/design/13_goal_hierarchy.md §7.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
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
)
from ailoveshen.domain.value_objects import Activity, GameObservation, MidGoal, MidGoalState

OVERLAY_HTML = Path(__file__).with_name("overlay.html")
KEEPALIVE_SECONDS = 15.0
QUEUE_SIZE = 16
# Every change of what the board shows (a step moves the small goal's progress)
EVENTS: tuple[type[DomainEvent], ...] = (
    GoalSetEvent,
    GoalEndedEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
    GameActionExecutedEvent,
)


def goals_snapshot(activity: Activity | None) -> dict[str, Any]:
    """The goals as the board shows them (the /api/goals JSON)."""
    if activity is None:
        return {"playing": False, "mission": None, "mid_goals": [], "goal": None, "home": None}
    pending = [g for g in activity.mid_goals if g.state == MidGoalState.PENDING]
    finished = [g for g in activity.mid_goals if g.state != MidGoalState.PENDING]
    titles = {g.id: g.title for g in activity.mid_goals}
    goal = activity.goal
    obs = activity.observation
    status = obs.goal if obs else None
    return {
        "playing": True,
        "mission": activity.mission.text if activity.mission else None,
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


def _home(obs: GameObservation | None) -> dict[str, Any] | None:
    if obs is None or not obs.has_home:
        return None
    chests = (obs.state.get("memory") or {}).get("chests", [])
    return {
        "name": (obs.state.get("home") or {}).get("name"),
        "bed": obs.bed_in_home,
        # As they were when last opened (only the streamer uses them)
        "chests": [{"contents": c["contents"], "minutes_ago": c["minutes_ago"]} for c in chests],
    }


def _mid_goal(goal: MidGoal, state: str) -> dict[str, Any]:
    return {
        "id": goal.id,
        "title": goal.title,
        "state": state,  # current / pending / done / dropped
        "requested_by": goal.requested_by,
        "conditions": [c.describe() for c in goal.conditions],
        "progress": list(goal.progress),
        "summary": list(goal.summary()),  # without the solver's sub-steps
        "ended_because": goal.ended_because,
    }


class GoalBoard:
    """Serves the goals of the play session being played to the stream overlay."""

    def __init__(self, activity: Callable[[], Activity | None]) -> None:
        """
        Initialize the board.

        Args:
            activity: What the streamer is doing now (the game session's activity; None
                before play starts)
        """
        self._activity = activity
        self._listeners: set[asyncio.Queue[str]] = set()
        self.app = self._create_app()

    def subscribe(self, bus: IEventSubscriber) -> None:
        """Push the goals to the connected overlays on every change."""
        for event_type in EVENTS:
            bus.subscribe(event_type, self._on_event)

    async def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        """Run the web server until cancelled."""
        server = uvicorn.Server(uvicorn.Config(self.app, host=host, port=port, log_level="warning"))
        logger.info(f"Goal board on http://{host}:{port}/overlay")
        await server.serve()

    def snapshot(self) -> dict[str, Any]:
        """The goals now."""
        return goals_snapshot(self._activity())

    async def _on_event(self, event: DomainEvent) -> None:
        data = json.dumps(self.snapshot(), ensure_ascii=False)
        for queue in self._listeners:
            if queue.full():
                queue.get_nowait()  # a slow overlay only needs the latest
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
