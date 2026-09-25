"""Tests for the goal board (stream overlay API)."""

import asyncio
import json

import pytest

pytest.importorskip("fastapi", reason="ailoveshen[stream] not installed")

from fastapi.testclient import TestClient  # noqa: E402

from ailoveshen.domain.entities import MidGoalPlan, PlaySession  # noqa: E402
from ailoveshen.domain.events import GoalSetEvent  # noqa: E402
from ailoveshen.domain.value_objects import (  # noqa: E402
    Candidate,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    Mission,
    Side,
)
from ailoveshen.presentation.web.goal_board import GoalBoard, goals_snapshot  # noqa: E402

BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")


def _session() -> PlaySession:
    plan = MidGoalPlan(mission=Mission("街にしていく"))
    plan.add("自分の家を作る", (GoalSpec(GoalPredicate.BUILT),))
    plan.add("ベッドで寝る", (BED,), requested_by="neko")
    plan.add("剣を持つ", (GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1),))
    plan.drop("m3", "今は要らない")
    session = PlaySession(blueprint=HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2), plan=plan)
    session.set_goal(
        Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=3), "壁の材料", mid_goal_id="m1"),
        "day",
    )
    session.observe(
        GameObservation(
            state={},
            candidates=(Candidate("wait", {"verb": "wait"}),),
            health=20.0,
            food=20,
            goal=GoalStatus(met=False, remaining=2, lines=("have 3 log (1/3)",)),
        )
    )
    return session


class TestGoalsSnapshot:
    """Tests for the JSON the overlay reads."""

    def test_not_playing(self):
        """Test before play starts nothing is shown."""
        assert goals_snapshot(None) == {
            "playing": False,
            "mission": None,
            "mid_goals": [],
            "goal": None,
        }

    def test_the_goals_from_the_mission_down(self):
        """Test the mission, the mid goals with their states, and the small goal."""
        data = goals_snapshot(_session().activity())

        assert data["mission"] == "街にしていく"
        assert [(g["title"], g["state"], g["requested_by"]) for g in data["mid_goals"]] == [
            ("自分の家を作る", "current", None),
            ("ベッドで寝る", "pending", "neko"),
            ("剣を持つ", "dropped", None),
        ]
        assert data["mid_goals"][2]["ended_because"] == "今は要らない"
        assert data["goal"] == {
            "goal": "have(log, 3)",
            "reason": "壁の材料",
            "mid_goal": "自分の家を作る",
            "survival": False,
            "progress": ["have 3 log (1/3)"],
        }


class TestGoalBoard:
    """Tests for the web endpoints."""

    def test_api_goals_and_overlay(self):
        """Test the JSON and the overlay page are served."""
        session = _session()
        client = TestClient(GoalBoard(session.activity).app)

        assert client.get("/api/goals").json()["mission"] == "街にしていく"
        page = client.get("/overlay")
        assert page.status_code == 200
        assert "/api/goals/stream" in page.text

    @pytest.mark.asyncio
    async def test_changes_are_pushed_to_the_stream(self):
        """Test a goal event sends the goals as they are then to every listener."""
        session = _session()
        board = GoalBoard(session.activity)
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
        board._listeners.add(queue)

        session.plan.complete("m1")
        await board._on_event(GoalSetEvent(goal="placed(bed, home)"))

        data = json.loads(queue.get_nowait())
        assert data["mid_goals"][0]["title"] == "ベッドで寝る"

    @pytest.mark.asyncio
    async def test_slow_listener_keeps_only_the_latest(self):
        """Test a full queue drops the oldest snapshot instead of blocking the game."""
        board = GoalBoard(lambda: None)
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        board._listeners.add(queue)

        await board._on_event(GoalSetEvent())
        await board._on_event(GoalSetEvent())

        assert queue.qsize() == 1
