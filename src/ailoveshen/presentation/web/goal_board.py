"""目標ボード: 配信者の目標を、配信オーバーレイのために外から読めるようにする。

- GET /api/goals         今の目標（JSON）
- GET /api/goals/stream  目標が変わるたび、ステップのたびに同じ JSON（Server-Sent Events）
- GET /overlay           OBS のブラウザソース用のページ（背景は透明）
- GET /overlay/vtuber    配信向けに飾ったページ（背景は透明。?demo=1 でサーバーなしの見本、
                         ?pos=right、?theme=mint|sky|lemon、?scale=0.8、?compact=1、?toast=0）
- GET /api/debug/gemini  デバッグ: Gemini の直近の呼び出し（用途、考える深さ、思考の要約、出力、
                         道具の呼び出し、トークン、プロンプト）。新しい順、?limit=N（既定 20）
- GET /debug/gemini      上を 2 秒ごとに読んで表示するページ（ブラウザや OBS のブラウザソース）
- GET /api/debug/screen/{id}  呼び出しに添えた画面の画像（直近の数枚だけ）

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
from fastapi.responses import HTMLResponse, Response, StreamingResponse
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
from ailoveshen.domain.value_objects import (
    Activity,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    MidGoal,
    MidGoalState,
)

OVERLAY_HTML = Path(__file__).with_name("overlay.html")
VTUBER_HTML = Path(__file__).with_name("overlay_vtuber.html")
DEBUG_HTML = Path(__file__).with_name("debug_gemini.html")
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
            "label": goal_label(goal.spec),  # 視聴者向けの日本語
            "reason": goal.reason,
            "mid_goal": titles.get(goal.mid_goal_id) if goal.mid_goal_id else None,
            "survival": goal.mid_goal_id is None,
            "progress": list(status.lines) if status else [],
        },
        "home": _home(obs),
        # 道具で操作しているとき、今やろうとしていること（配信者が書いた 1 文）
        "intent": activity.intent or None,
    }


# 視聴者向けの名前（Minecraft の日本語版に合わせる）。ないものは英語の ID のまま
ITEM_NAMES = {
    "log": "原木",
    "planks": "板材",
    "food": "食べ物",
    "bed": "ベッド",
    "white_bed": "白いベッド",
    "wool": "羊毛",
    "white_wool": "白い羊毛",
    "door": "ドア",
    "stick": "棒",
    "crafting_table": "作業台",
    "furnace": "かまど",
    "chest": "チェスト",
    "torch": "松明",
    "coal": "石炭",
    "charcoal": "木炭",
    "cobblestone": "丸石",
    "stone": "石",
    "iron_ore": "鉄鉱石",
    "raw_iron": "鉄の原石",
    "iron_ingot": "鉄インゴット",
    "wooden_sword": "木の剣",
    "stone_sword": "石の剣",
    "iron_sword": "鉄の剣",
    "wooden_pickaxe": "木のツルハシ",
    "stone_pickaxe": "石のツルハシ",
    "iron_pickaxe": "鉄のツルハシ",
    "wooden_axe": "木の斧",
    "stone_axe": "石の斧",
    "shield": "盾",
    "bread": "パン",
    "cooked_beef": "ステーキ",
    "cooked_porkchop": "焼き豚",
    "cooked_mutton": "焼き羊肉",
    "cooked_chicken": "焼き鳥",
    "wheat": "小麦",
    "wheat_seeds": "小麦の種",
}


def _item(name: str | None) -> str:
    if not name:
        return ""
    return ITEM_NAMES.get(name, name.replace("_", " "))


def goal_label(spec: GoalSpec) -> str:
    """小目標を視聴者向けの日本語にする（例: have(planks, 4) → 板材を 4 個集める）。"""
    p = spec.predicate
    item, n = _item(spec.item), spec.count
    if p == GoalPredicate.HAVE:
        return f"{item}を {n} 個そろえる" if n and n > 1 else f"{item}を手に入れる"
    if p == GoalPredicate.STORED:
        return f"チェストに{item}を {n} 個ためる"
    if p == GoalPredicate.BUILT:
        return "家を建てる"
    if p == GoalPredicate.PLACED:
        return f"家に{item}を置く"
    if p == GoalPredicate.AT_HOME:
        return "家に帰る"
    if p == GoalPredicate.THROUGH_NIGHT:
        return "夜を越す"
    if p == GoalPredicate.EXPLORED:
        return f"{spec.distance}m 先まで探検する"
    if p == GoalPredicate.CLEARED:
        return "ドアの前の敵をやっつける"
    if p == GoalPredicate.LIT:
        return f"家のまわり {spec.distance}m を明るくする"
    if p == GoalPredicate.SURVEYED:
        return f"街の候補地を {n} か所しらべる"
    return spec.describe()


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

    def __init__(
        self,
        activity: Callable[[], Activity | None],
        gemini_calls: Callable[[int], list[dict[str, Any]]] | None = None,
        gemini_image: Callable[[str], tuple[bytes, str] | None] | None = None,
    ) -> None:
        """
        ボードを初期化する。

        Args:
            activity: 配信者が今していること（ゲームのセッションの activity。プレイを
                始める前は None）
            gemini_calls: Gemini の直近の呼び出し（新しい順に最大 N 件）を返すもの。
                None なら /api/debug/gemini は空のリストを返す
            gemini_image: 呼び出しに添えた画像（データと形式）を id で返すもの
        """
        self._activity = activity
        self._gemini_calls = gemini_calls
        self._gemini_image = gemini_image
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

        @app.get("/overlay/vtuber", response_class=HTMLResponse)
        async def overlay_vtuber() -> str:
            return VTUBER_HTML.read_text(encoding="utf-8")

        @app.get("/api/debug/gemini")
        async def gemini_calls(limit: int = 20) -> list[dict[str, Any]]:
            if self._gemini_calls is None:
                return []
            return self._gemini_calls(max(1, min(limit, 200)))

        @app.get("/api/debug/screen/{image_id}")
        async def gemini_screen(image_id: str) -> Response:
            found = self._gemini_image(image_id) if self._gemini_image else None
            if found is None:
                return Response(status_code=404)
            data, mime_type = found
            return Response(content=data, media_type=mime_type)

        @app.get("/debug/gemini", response_class=HTMLResponse)
        async def gemini_page() -> str:
            return DEBUG_HTML.read_text(encoding="utf-8")

        return app
