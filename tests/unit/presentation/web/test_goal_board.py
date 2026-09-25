"""ゴールボード（配信のオーバーレイの API）のテスト。"""

import asyncio
import json
from dataclasses import replace

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
    TownDefinition,
    TownSite,
    TownStage,
)
from ailoveshen.presentation.web.goal_board import GoalBoard, goals_snapshot  # noqa: E402

BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")


def _session() -> PlaySession:
    plan = MidGoalPlan(mission=Mission("街にしていく"))
    plan.add("自分の家を作る", (GoalSpec(GoalPredicate.BUILT),))
    plan.add("ベッドで寝る", (BED,), requested_by="neko")
    plan.add("剣を持つ", (GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1),))
    plan.drop("m3", "今は要らない")
    plan.judged("m1", ("house blocks placed 3/72", "  have 12 log (0/12)"))
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
    """オーバーレイが読む JSON のテスト。"""

    def test_town(self):
        """街の段階を、済み・今・これからに分けて、何を待っているかとともに示す。"""
        session = _session()
        session.plan.define_town(
            TownDefinition(
                "小さな街",
                (
                    TownStage("家", "住む", conditions=(GoalSpec(GoalPredicate.BUILT),)),
                    TownStage("倉庫", "街らしく", unresolved=("2 軒目",)),
                ),
            )
        )
        session.plan.complete(session.plan.add("家", (GoalSpec(GoalPredicate.BUILT),), stage=0).id)

        town = goals_snapshot(session.activity())["town"]

        assert town["text"] == "小さな街" and not town["complete"]
        assert [(s["title"], s["state"]) for s in town["stages"]] == [
            ("家", "done"),
            ("倉庫", "current"),
        ]
        assert town["stages"][1]["unresolved"] == ["2 軒目"]
        assert goals_snapshot(session.activity())["mid_goals"][-1]["stage"] == 0

    def test_not_playing(self):
        """プレイが始まる前は何も示さない。"""
        assert goals_snapshot(None) == {
            "playing": False,
            "mission": None,
            "town": None,
            "site": None,
            "survey": None,
            "mid_goals": [],
            "goal": None,
            "home": None,
        }

    def test_site(self):
        """選んだ場所（決めたこと）と、調べた候補地（ブリッジの数字）を別に出す。"""
        session = _session()
        survey = {"planned": 9, "sites": [{"id": "E", "x": 96, "z": 0}]}
        session.observe(replace(session.last_observation, state={"survey": survey}))
        assert goals_snapshot(session.activity())["site"] is None

        session.plan.choose_site(TownSite("E", 96, 0, "石が多い", "いしのまち"))
        data = goals_snapshot(session.activity())

        assert data["site"] == {
            "site_id": "E",
            "x": 96,
            "z": 0,
            "reason": "石が多い",
            "name": "いしのまち",
        }
        assert data["survey"] == survey

    def test_the_goals_from_the_mission_down(self):
        """大目標、状態つきの中目標、小目標。"""
        data = goals_snapshot(_session().activity())

        assert data["mission"] == "街にしていく"
        assert [(g["title"], g["state"], g["requested_by"]) for g in data["mid_goals"]] == [
            ("自分の家を作る", "current", None),
            ("ベッドで寝る", "pending", "neko"),
            ("剣を持つ", "dropped", None),
        ]
        assert data["mid_goals"][2]["ended_because"] == "今は要らない"
        assert data["mid_goals"][0]["summary"] == ["house blocks placed 3/72"]
        assert data["goal"] == {
            "goal": "have(log, 3)",
            "label": "原木を 3 個そろえる",
            "reason": "壁の材料",
            "mid_goal": "自分の家を作る",
            "survival": False,
            "progress": ["have 3 log (1/3)"],
        }

        assert data["home"] is None  # まだ建てていない

    def test_home_with_its_chests(self):
        """拠点の名前と、チェストを最後に開けたときの中身。"""
        session = _session()
        memory = {
            "chests": [
                {"direction": "N", "distance_m": 3, "contents": {"oak_log": 20}, "minutes_ago": 2}
            ]
        }
        session.observe(
            GameObservation(
                state={"home": {"name": "ぽかぽかログハウス"}, "memory": memory},
                candidates=(Candidate("wait", {"verb": "wait"}),),
                health=20.0,
                food=20,
                has_home=True,
                bed_in_home=True,
            )
        )

        assert goals_snapshot(session.activity())["home"] == {
            "name": "ぽかぽかログハウス",
            "bed": True,
            "chests": [{"contents": {"oak_log": 20}, "minutes_ago": 2}],
        }


class TestGoalBoard:
    """Web のエンドポイントのテスト。"""

    def test_api_goals_and_overlay(self):
        """JSON とオーバーレイのページを返す。"""
        session = _session()
        client = TestClient(GoalBoard(session.activity).app)

        assert client.get("/api/goals").json()["mission"] == "街にしていく"
        page = client.get("/overlay")
        assert page.status_code == 200
        assert "/api/goals/stream" in page.text

    @pytest.mark.asyncio
    async def test_changes_are_pushed_to_the_stream(self):
        """目標のイベントで、その時点の目標をすべての購読者に送る。"""
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
        """キューが満杯なら、ゲームを止めずに一番古いスナップショットを捨てる。"""
        board = GoalBoard(lambda: None)
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        board._listeners.add(queue)

        await board._on_event(GoalSetEvent())
        await board._on_event(GoalSetEvent())

        assert queue.qsize() == 1


def test_debug_gemini_calls_as_json_and_a_page():
    """デバッグ: Gemini の直近の呼び出しを JSON で、表示するページと一緒に出す。"""
    from ailoveshen.infrastructure.adapters.storage import InMemoryGenerationLog

    log = InMemoryGenerationLog(size=3)
    for i in range(5):
        log.record({"purpose": "tool", "thoughts": f"考え {i}"})
    client = TestClient(GoalBoard(lambda: None, gemini_calls=log.recent).app)

    calls = client.get("/api/debug/gemini", params={"limit": 2}).json()
    assert [c["thoughts"] for c in calls] == ["考え 4", "考え 3"]  # 新しい順、古いものは消える
    assert [c["id"] for c in calls] == [5, 4]
    assert "/api/debug/gemini" in client.get("/debug/gemini").text
    # 記録を渡していなければ空
    assert TestClient(GoalBoard(lambda: None).app).get("/api/debug/gemini").json() == []


def test_debug_screen_images_by_id():
    """呼び出しに添えた画面を id で返す（記録の JSON には画像そのものを入れない）。"""
    from ailoveshen.infrastructure.adapters.storage import InMemoryGenerationLog

    log = InMemoryGenerationLog()
    image_id = log.record_image(b"\xff\xd8jpeg", "image/jpeg")
    client = TestClient(
        GoalBoard(lambda: None, gemini_calls=log.recent, gemini_image=log.image).app
    )
    r = client.get(f"/api/debug/screen/{image_id}")
    assert (r.status_code, r.content, r.headers["content-type"]) == (
        200,
        b"\xff\xd8jpeg",
        "image/jpeg",
    )
    assert client.get("/api/debug/screen/img999").status_code == 404


def test_goal_labels_for_viewers():
    """小目標は、述語ではなく視聴者向けの日本語でも出す。"""
    from ailoveshen.presentation.web.goal_board import goal_label

    assert goal_label(GoalSpec(GoalPredicate.HAVE, item="planks", count=4)) == "板材を 4 個そろえる"
    assert (
        goal_label(GoalSpec(GoalPredicate.HAVE, item="iron_sword", count=1)) == "鉄の剣を手に入れる"
    )
    assert (
        goal_label(GoalSpec(GoalPredicate.HAVE, item="mud_bricks", count=2))
        == "mud bricksを 2 個そろえる"
    )
    assert (
        goal_label(GoalSpec(GoalPredicate.PLACED, item="bed", where="home")) == "家にベッドを置く"
    )
    assert goal_label(GoalSpec(GoalPredicate.THROUGH_NIGHT)) == "夜を越す"


def test_the_vtuber_overlay_reads_the_label_and_the_intent():
    session = PlaySession(blueprint=None, plan=MidGoalPlan(mission=Mission("街にしていく")))
    session.set_goal(
        Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=3), reason="壁の材料"), "day"
    )
    session.set_intent("近くの木を切りにいく")
    client = TestClient(GoalBoard(session.activity).app)

    goals = client.get("/api/goals").json()
    assert goals["goal"]["label"] == "原木を 3 個そろえる"
    assert goals["intent"] == "近くの木を切りにいく"
    page = client.get("/overlay/vtuber").text
    assert "/api/goals/stream" in page and "goal.label" in page
