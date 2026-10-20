"""目標ボード: 配信者の目標を、配信オーバーレイのために外から読めるようにする。

- GET /api/goals         今の目標（JSON）
- GET /api/goals/stream  目標が変わるたび、ステップのたびに同じ JSON（Server-Sent Events）
- GET /overlay           OBS のブラウザソース用のページ（背景は透明）
- GET /overlay/vtuber    配信向けに飾ったページ（背景は透明。?demo=1 でサーバーなしの見本、
                         ?pos=right、?theme=mint|sky|lemon、?scale=0.8、?compact=1、?toast=0）
- GET /api/readings      視聴者の名前の読みの辞書（?name= で 1 人だけ。設計書 30）
- GET /api/debug/gemini  デバッグ: Gemini の直近の呼び出し（用途、考える深さ、思考の要約、出力、
                         道具の呼び出し、トークン、プロンプト）。新しい順、?limit=N（既定 20）
- GET /debug/gemini      上を 2 秒ごとに読んで表示するページ（ブラウザや OBS のブラウザソース）
- GET /api/debug/screen/{id}  呼び出しに添えた画面の画像（直近の数枚だけ）

読み取り専用: プレイセッションが持つものを出すだけで、変えることはない。プレイのループと
同じプロセスで動く（`GoalBoard.serve`）。設計: docs/design/13_goal_hierarchy.md §7。
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventSubscriber
from ailoveshen.application.ports.output.reading_store import NameReading
from ailoveshen.application.use_cases.item_names import item_name as _item
from ailoveshen.domain.events import (
    DomainEvent,
    GameActionExecutedEvent,
    GoalDecidedEvent,
    GoalDecidingEvent,
    GoalEndedEvent,
    GoalSetEvent,
    JobDoneEvent,
    ObjectiveAddedEvent,
    ObjectiveCompletedEvent,
    ObjectiveDroppedEvent,
    ObjectivesResetEvent,
    SkillLearnedEvent,
    TownMapDrawnEvent,
    TownSiteChosenEvent,
)
from ailoveshen.domain.value_objects import (
    Activity,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    Job,
    JobState,
    Objective,
    ObjectiveState,
    Step,
)
from ailoveshen.presentation.web.admin import AdminPanel
from ailoveshen.presentation.web.avatar import AvatarStage

OVERLAY_HTML = Path(__file__).with_name("overlay.html")
VTUBER_HTML = Path(__file__).with_name("overlay_vtuber.html")
DEBUG_HTML = Path(__file__).with_name("debug_gemini.html")
TOWN_HTML = Path(__file__).with_name("town_map.html")
KEEPALIVE_SECONDS = 15.0
QUEUE_SIZE = 16
# ボードの表示が変わるイベントすべて（ステップで小目標の進み具合が変わる）
EVENTS: tuple[type[DomainEvent], ...] = (
    GoalSetEvent,
    GoalEndedEvent,
    ObjectiveAddedEvent,
    ObjectiveCompletedEvent,
    ObjectiveDroppedEvent,
    ObjectivesResetEvent,
    JobDoneEvent,
    TownSiteChosenEvent,
    TownMapDrawnEvent,
    SkillLearnedEvent,
    GameActionExecutedEvent,
    GoalDecidingEvent,
    GoalDecidedEvent,
)


# 建物・施設の呼び名（識別子 → 呼び名）。ラベルを作る深い所まで引数で運ばず、スナップショットを作る間だけ持つ
_TITLES: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("board_titles", default={})


def goals_snapshot(activity: Activity | None) -> dict[str, Any]:
    """ボードに出す形の目標（/api/goals の JSON）。建物・施設は呼び名で出す（設計書 160）。"""
    obs = activity.observation if activity is not None else None
    token = _TITLES.set(dict(obs.titles) if obs is not None else {})
    try:
        return _goals_snapshot(activity)
    finally:
        _TITLES.reset(token)


def _named(name: str | None) -> str:
    """識別子の呼び名（なければ識別子のまま）。"""
    return _TITLES.get().get(name or "", name or "")


def _goals_snapshot(activity: Activity | None) -> dict[str, Any]:
    if activity is None:
        return {
            "playing": False,
            "mission": None,
            "town": None,
            "site": None,
            "survey": None,
            "objectives": [],
            "history": [],
            "goal": None,
            "home": None,
            "town_map": None,
        }
    pending = [g for g in activity.objectives if g.state == ObjectiveState.PENDING]
    finished = [g for g in activity.objectives if g.state != ObjectiveState.PENDING]
    titles = {g.id: g.title for g in activity.objectives}
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
        "objectives": [
            _objective(g, "current" if i == 0 else "pending") for i, g in enumerate(pending)
        ]
        + [_objective(g, g.state.value) for g in finished],
        "history": [asdict(d) for d in activity.history],
        "goal": None
        if goal is None
        else {
            "goal": goal.describe(),
            # 視聴者向けの日本語。束（docs/design/44）なら、まだ済んでいない先頭と「ほか N つ」
            "label": goal_label(goal.unmet(status)[0])
            + (f"（ほか {len(goal.unmet(status)) - 1} つ）" if len(goal.unmet(status)) > 1 else ""),
            "together": [goal_label(s) for s in goal.unmet(status)[1:]],
            "reason": goal.reason,
            "objective": titles.get(goal.objective_id) if goal.objective_id else None,
            "objective_id": goal.objective_id,
            "survival": goal.objective_id is None,
            "progress": list(status.lines) if status else [],
        },
        "home": _home(obs),
        # 道具で操作しているとき、今やろうとしていること（配信者が書いた 1 文）
        "intent": activity.intent or None,
        # 実行している手の動作（小目標の欄。docs/design/91）。まだ手がなければ None で、述語の label を出す
        "action": activity.action or None,
        # 街の理想図（docs/design/47 §6。/town が描く）
        "town_map": asdict(activity.town_map) if activity.town_map else None,
    }


def goal_label(spec: GoalSpec) -> str:
    """小目標を視聴者向けの日本語にする（例: have(planks, 4) → 板材を 4 個集める）。"""
    p = spec.predicate
    item, n = _item(spec.item), spec.count
    if p == GoalPredicate.HAVE:
        return f"{item}を {n} 個そろえる" if n and n > 1 else f"{item}を手に入れる"
    if p == GoalPredicate.STORED:
        return f"チェストに{item}を {n} 個ためる"
    if p == GoalPredicate.BUILT:
        return f"「{_named(spec.name)}」を建てる" if spec.name else "家を建てる"
    if p == GoalPredicate.PLACED:
        where = {"home": "家に", "near_home": "家のまわりに"}.get(spec.where or "home")
        if where is None:
            where = f"「{_named(spec.where[6:])}」に" if (spec.where or "").startswith("build:") else "そこに"
        return f"{where}{item}を{f' {n} 個' if n and n > 1 else ''}置く"
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
    if p == GoalPredicate.LEVELED:
        where = "家" if spec.where == "home" else "そこ"
        return f"{where}のまわり {spec.distance}m を整地する"
    if p == GoalPredicate.PAVED:
        return "街の道を敷く"
    if p == GoalPredicate.CUSTOM:
        return f"{spec.name} を仕上げる"
    if p == GoalPredicate.SURVEYED:
        return f"街の候補地を {n} か所しらべる"
    if p == GoalPredicate.PLANTED:
        return f"木を {n} 本植える"
    if p == GoalPredicate.FARMED:
        return f"畑に{item}を {n} マス植える"
    if p == GoalPredicate.FACILITY:
        return f"{_named(spec.name)} を作る"
    return spec.describe()


# 中目標に題がないときの種類（大目標に必要なことの 1 つ。例: 材料集め、ブロックの設置、整地）
_JOB_KINDS = {
    GoalPredicate.HAVE: "材料集め",
    GoalPredicate.STORED: "材料の保管",
    GoalPredicate.PLACED: "ブロックの設置",
    GoalPredicate.BUILT: "ブロックの設置",
    GoalPredicate.LEVELED: "整地",
    GoalPredicate.PAVED: "道づくり",
    GoalPredicate.PLANTED: "植林",
    GoalPredicate.FARMED: "畑づくり",
    GoalPredicate.LIT: "明かりの設置",
    GoalPredicate.SURVEYED: "場所の選定",
    GoalPredicate.FACILITY: "施設づくり",
}


def job_label(spec: GoalSpec) -> str:
    """題のない中目標の表示: 「種類: 条件」（例: ブロックの設置: 家を建てる）。種類が決まらなければ条件だけ。"""
    kind = _JOB_KINDS.get(spec.predicate)
    return f"{kind}: {goal_label(spec)}" if kind else goal_label(spec)


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


def _steps(job: Job, live: bool = False) -> list[dict[str, Any]]:
    """
    中目標の計画の小目標（前提を先に、depth 0 が小目標、1 以降が前提）。済み・いま満たしている・次・待ち。

    済み: 一度満たした（ラッチ）、または中目標が済んでいる（中目標を満たしたなら、その小目標はもう要らない）。
    met: 世界と照合したら今満たしている（`Job.met_now`。まだ済みにしていない、または一番上の大目標ではない）。
    closed: 大目標が終わって、やらずに閉じた。current: `live`（今の大目標の今の中目標）の、済んでいない前提の
    いちばん奥。
    """
    done = set(job.steps_done)
    met = set(job.met_now)
    order = [x for step in job.steps for x in _walk(step, 0)]
    first_open = job.next_step({k: True for k in met}) if live else None  # 前提が先
    finished = job.state == JobState.DONE
    closed = job.state in (JobState.CLOSED, JobState.DROPPED)

    def state(s: Step) -> str:
        if finished or s.key in done:
            return "done"
        if s.key in met:
            return "met"
        if closed:
            return "closed"
        return "current" if s is first_open else "waiting"

    return [
        {
            "label": goal_label(s.spec),
            "reason": s.reason,
            "depth": depth,
            "state": state(s),
        }
        for s, depth in order
    ]


def _walk(step: Step, depth: int) -> list[tuple[Step, int]]:
    """小目標を先に、その前提を続けて（画面に上から読める順）。"""
    return [(step, depth)] + [x for n in step.needs for x in _walk(n, depth + 1)]


def _met(job: Job) -> bool:
    """世界と照合したら、今、中目標の条件（束も）がすべて満たされているか。"""
    return all(s.describe() in job.met_now for s in job.specs)


def _jobs(goal: Objective, live: bool = False) -> list[dict[str, Any]]:
    """
    中目標: done（一度満たしたら済みのまま）/ met（今満たしている。まだ済みにしていない）/ current（まだの
    一番先）/ waiting / closed（大目標が終わって、やらずに閉じた）。`live` は、一番上の大目標か。
    """
    first_open = next(
        (s for s in goal.jobs if s.state == JobState.PENDING and not _met(s)), None
    )

    def state(s: Job) -> str:
        if s.state == JobState.DONE:
            return "done"
        if s.state != JobState.PENDING:
            return "closed"
        if _met(s):
            return "met"
        return "current" if s is first_open else "waiting"

    return [
        {
            "id": s.id,
            "label": s.title
            or job_label(s.spec) + (f"（ほか {len(s.together)} つ）" if s.together else ""),
            "steps": _steps(s, live and s is first_open),
            "state": state(s),
            "reason": s.reason,
            "requested_by": s.requested_by,
            "suggested_by": s.suggested_by,
            "progress": list(s.progress),
        }
        for s in goal.jobs
    ]


def _objective(goal: Objective, state: str) -> dict[str, Any]:
    jobs = _jobs(goal, state == "current")
    return {
        "id": goal.id,
        "title": goal.title,
        "kind": goal.kind.value,  # build / facility / infrastructure
        "state": state,  # current / pending / done / dropped
        "jobs": jobs,
        "jobs_done": sum(1 for s in jobs if s["state"] in ("done", "met")),
        "jobs_total": sum(1 for s in jobs if s["state"] != "closed"),  # 閉じた中目標は数えない
        "requested_by": goal.requested_by,
        "suggested_by": goal.suggested_by,  # 採った助言をくれた視聴者（頼みではない）
        "stage": goal.stage,  # 対応する街の段階（インデックス）。なければ None
        "prepares_town": goal.prepares_town,  # 街の準備（候補地の調査、引っ越し）
        "playbook": goal.playbook,  # 定石の段の名前（None: 定石ではない。docs/design/74）
        "conditions": [c.describe() for c in goal.conditions],
        "progress": list(goal.progress),
        "summary": list(goal.summary()),  # ソルバーの細かい手順は除く
        "ended_because": goal.ended_because,
    }


class _EmbeddedServer(uvicorn.Server):
    """
    プレイの処理の中で動かす uvicorn。Ctrl-C（SIGINT / SIGTERM）を横取りしない: uvicorn は
    シグナルを取ると、開いている接続（OBS のブラウザソースの SSE は閉じない）が閉じるまで待ち、
    プレイも止まらなかった。止めるのはシグナルを受けた呼び出し側で、このタスクをキャンセルする。
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class GoalBoard:
    """プレイ中のセッションの目標を配信オーバーレイに出す。"""

    def __init__(
        self,
        activity: Callable[[], Activity | None],
        gemini_calls: Callable[[int], list[dict[str, Any]]] | None = None,
        gemini_image: Callable[[str], tuple[bytes, str] | None] | None = None,
        avatar: AvatarStage | None = None,
        readings: Callable[[], tuple[NameReading, ...]] | None = None,
        admin: AdminPanel | None = None,
    ) -> None:
        """
        ボードを初期化する。

        Args:
            activity: 配信者が今していること（ゲームのセッションの activity。プレイを
                始める前は None）
            gemini_calls: Gemini の直近の呼び出し（新しい順に最大 N 件）を返すもの。
                None なら /api/debug/gemini は空のリストを返す
            gemini_image: 呼び出しに添えた画像（データと形式）を id で返すもの
            avatar: アバター（VRM）のページと合図。あれば同じサーバーで /avatar を出す
            readings: 視聴者の名前の読みの辞書の全部を返すもの（None: /api/readings は空）
            admin: 管理画面（あれば同じサーバーで /admin を出す。docs/design/54_admin_panel.md）
        """
        self._activity = activity
        self._gemini_calls = gemini_calls
        self._gemini_image = gemini_image
        self._avatar = avatar
        self._readings = readings
        self._admin = admin
        self._listeners: set[asyncio.Queue[str | None]] = set()
        # 最後に覚えた技（seq が変わったらオーバーレイがお知らせを出す。起動してからのもの）
        self._learned: dict[str, Any] | None = None
        # LLM が次の小目標か大目標の計画を考えている間の {after_failure}（考えていなければ None）
        self._thinking: dict[str, Any] | None = None
        self.app = self._create_app()

    def subscribe(self, bus: IEventSubscriber) -> None:
        """変わるたびに、接続中のオーバーレイへ目標を送る。"""
        for event_type in EVENTS:
            bus.subscribe(event_type, self._on_event)

    async def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        """キャンセルされるまで Web サーバーを動かす。"""
        server = _EmbeddedServer(
            uvicorn.Config(
                self.app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=2
            )
        )
        logger.info(f"目標ボード: http://{host}:{port}/overlay")
        task = asyncio.ensure_future(_serve_or_raise(server, host, port))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # SSE を終わらせてから uvicorn 自身に止めさせる。タスクを直接キャンセルすると、
            # 開いたままの接続（OBS のブラウザソース）の処理が打ち切られてトレースバックが出る
            end_streams(self._listeners)
            if self._avatar is not None:
                self._avatar.end_streams()
            server.should_exit = True
            await task
            raise

    def snapshot(self) -> dict[str, Any]:
        """今の目標と、最後に覚えた技、考え中かどうか。"""
        return {
            **goals_snapshot(self._activity()),
            "skill_learned": self._learned,
            "thinking": self._thinking,
        }

    async def _on_event(self, event: DomainEvent) -> None:
        if isinstance(event, SkillLearnedEvent):
            seq = (self._learned["seq"] if self._learned else 0) + 1
            self._learned = {"seq": seq, "name": event.name, "description": event.description}
        if isinstance(event, GoalDecidingEvent):
            self._thinking = {"after_failure": event.after_failure}
        elif isinstance(event, GoalDecidedEvent):
            self._thinking = None
        data = json.dumps(self.snapshot(), ensure_ascii=False)
        for queue in self._listeners:
            if queue.full():
                queue.get_nowait()  # 遅いオーバーレイには最新のものだけあればよい
            queue.put_nowait(data)

    async def _stream(self, request: Request) -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._listeners.add(queue)
        try:
            yield f"data: {json.dumps(self.snapshot(), ensure_ascii=False)}\n\n"
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if data is None:  # サーバーを止める
                    return
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

        @app.get("/api/readings")
        async def readings(name: str = "") -> list[dict[str, Any]]:
            found = self._readings() if self._readings else ()
            wanted = name.strip().casefold()
            return [asdict(r) for r in found if not wanted or r.name.casefold() == wanted]

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

        if self._avatar is not None:
            self._avatar.install(app)
        if self._admin is not None:
            self._admin.install(app, self.snapshot)

        @app.get("/town", response_class=HTMLResponse)
        async def town_page() -> str:
            return TOWN_HTML.read_text(encoding="utf-8")

        @app.get("/debug/gemini", response_class=HTMLResponse)
        async def gemini_page() -> str:
            return DEBUG_HTML.read_text(encoding="utf-8")

        return app


async def _serve_or_raise(server: uvicorn.Server, host: str, port: int) -> None:
    """uvicorn はポートを開けないと SystemExit を投げ、配信の全体が落ちる。OSError にする。"""
    try:
        await server.serve()
    except SystemExit as e:
        raise OSError(f"the goal board could not serve on {host}:{port} (in use?)") from e


def end_streams(listeners: set[asyncio.Queue[str | None]]) -> None:
    """開いている SSE に終わりの合図（None）を入れる。"""
    for queue in listeners:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(None)
