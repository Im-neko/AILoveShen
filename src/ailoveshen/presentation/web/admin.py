"""
管理画面（docs/design/54_admin_panel.md）: 配信中に、任意の言葉を喋らせる・プリセットを使う・
配信者の指示として介入する。

目標ボードと同じサーバーの /admin。このマシン（127.0.0.1）からだけ開ける: ホストを外に
向けても、ループバック以外からの接続は断る。別のサイトのページから叩かれないよう、書き込みには
専用のヘッダーを要る（ブラウザは別のオリジンから付けられない）。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from loguru import logger
from pydantic import BaseModel

from ailoveshen.application.use_cases.presets import PresetError, SpeechPresets, clean_text

ADMIN_HTML = Path(__file__).with_name("admin.html")
# 書き込み（POST / PUT / DELETE）に付けるヘッダー。別のオリジンのページは、付けるには CORS の許可が
# 要り、このサーバーは許可しない
GUARD_HEADER = "x-admin-request"
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
LOG_SIZE = 30


class _Say(BaseModel):
    text: str


class _Preset(BaseModel):
    title: str = ""
    text: str
    category: str = ""


class _Instruct(BaseModel):
    text: str
    reply: bool = True  # 指示への返事（今やることの一言）を喋る


class _Rethink(BaseModel):
    reason: str = ""


def _host_of(value: str) -> str:
    """`host:port` / `[::1]:port` / URL のホスト部分。"""
    value = value.split("://", 1)[-1].split("/", 1)[0]
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _node(
    key: str,
    layer: str,
    label: str,
    state: str = "",
    notes: list[str] | None = None,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "layer": layer,  # mission / objective / job / goal
        "label": label,
        "state": state,  # current / pending / waiting / done / dropped
        "notes": [n for n in (notes or []) if n],
        "children": children or [],
    }


def _who(item: dict[str, Any]) -> str:
    if item.get("requested_by"):
        return f"{item['requested_by']}さんの頼み"
    if item.get("suggested_by"):
        return f"{item['suggested_by']}さんの提案"
    return ""


def _step_nodes(job_key: str, steps: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    """計画の小目標（depth 0 が小目標、その次の depth が前提）を、前提を子にした木にする。"""
    roots: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []
    for i, step in enumerate(steps):
        depth = step.get("depth", 0)
        state = step["state"]
        node = _node(
            f"{job_key}.s{i}",
            "goal",
            step["label"],
            state,
            [
                step.get("reason", ""),
                "いまの小目標" if state == "current" and step["label"] == now else "",
            ],
        )
        while stack and stack[-1][0] >= depth:
            stack.pop()
        (stack[-1][1]["children"] if stack else roots).append(node)
        stack.append((depth, node))
    return roots


def goal_tree(goals: dict[str, Any]) -> dict[str, Any] | None:
    """
    最終目標・大目標・中目標・小目標を 1 本の木にする（管理画面の「目標ツリー」）。

    大目標の下に中目標、その下に計画の小目標（前提はさらにその下）。いまの小目標は、
    計画の中にあればそこに印を付け、なければ、その大目標の今の中目標の下（中目標がなければ大目標の下）に足す。
    生存のための小目標は大目標に属さないので、最終目標の直下。プレイ前は None。
    """
    if not goals.get("playing"):
        return None
    current = goals.get("goal") or {}
    now = current.get("label", "")
    survival = []
    if current and current.get("survival"):
        notes = [
            current.get("reason", ""),
            "生存のため（大目標を待てない）",
            *current.get("progress", []),
        ]
        survival.append(_node("goal", "goal", now, "current", notes))

    objectives = []
    for obj in goals.get("objectives") or []:
        serves = (
            bool(current)
            and not current.get("survival")
            and current.get("objective_id") == obj["id"]
        )
        jobs = []
        placed = False
        for job in obj.get("jobs") or []:
            key = f"{obj['id']}.{job['id']}"
            children = _step_nodes(key, job.get("steps") or [], now if serves else "")
            placed = placed or any(_has_now(c) for c in children)
            jobs.append(
                _node(
                    key,
                    "job",
                    job["label"],
                    job["state"],
                    [job.get("reason", ""), _who(job), *job.get("progress", [])],
                    children,
                )
            )
        if serves and not placed:
            here = next((j for j in jobs if j["state"] == "current"), None)
            (here["children"] if here else jobs).insert(
                0,
                _node(
                    f"{obj['id']}.now",
                    "goal",
                    now,
                    "current",
                    [current.get("reason", ""), "いまの小目標", *current.get("progress", [])],
                ),
            )
        objectives.append(
            _node(
                obj["id"],
                "objective",
                obj["title"],
                obj["state"],
                [
                    "条件: " + " / ".join(obj.get("conditions") or [])
                    if obj.get("conditions")
                    else "",
                    _who(obj),
                    *obj.get("progress", []),
                    obj.get("ended_because") or "",
                ],
                jobs,
            )
        )
    return _node(
        "mission",
        "mission",
        goals.get("mission") or "（最終目標なし）",
        "current",
        [],
        survival + objectives,
    )


def _has_now(node: dict[str, Any]) -> bool:
    return "いまの小目標" in node["notes"] or any(_has_now(c) for c in node["children"])


class AdminPanel:
    """管理画面の API とページ。"""

    def __init__(
        self,
        presets: SpeechPresets,
        speak: Callable[[str], Awaitable[bool]],
        instruct: Callable[[str, bool], Awaitable[str]],
        rethink: Callable[[str], bool],
        allowed_hosts: frozenset[str] = LOOPBACK,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        """
        Args:
            presets: 喋らせる文章のプリセット
            speak: 文章を（視聴者への返事と同じ優先度で）喋らせる。喋らせられたか（読み上げがなければ偽）
            instruct: 配信者の指示として渡す（第 2 引数: 返事を喋るか）。返事の文章（なければ空）
            rethink: 今の小目標を次の切れ目で決め直させる。受け付けたか（プレイ前なら偽）
            allowed_hosts: 接続を受けるクライアントのホストと、Host / Origin のホスト（既定: このマシンだけ）
            clock: 時計（テストで差し替える）
        """
        self._presets = presets
        self._speak = speak
        self._instruct = instruct
        self._rethink = rethink
        self._allowed = allowed_hosts
        self._clock = clock
        self._log: deque[dict[str, str]] = deque(maxlen=LOG_SIZE)

    def _note(self, kind: str, text: str, result: str = "") -> None:
        self._log.appendleft(
            {"at": self._clock().strftime("%H:%M:%S"), "kind": kind, "text": text, "result": result}
        )

    def _guard(self, request: Request) -> None:
        """ループバックからの、この画面のページ（か、専用のヘッダーを付けた書き込み）だけ通す。"""
        client = request.client.host if request.client else ""
        if client not in self._allowed:
            raise HTTPException(status_code=403, detail="the admin page is for this machine only")
        host = _host_of(request.headers.get("host", ""))
        if host not in self._allowed:
            raise HTTPException(status_code=403, detail="unexpected Host header")
        origin = request.headers.get("origin")
        if origin and _host_of(origin) not in self._allowed:
            raise HTTPException(status_code=403, detail="unexpected Origin")
        if request.method not in ("GET", "HEAD") and request.headers.get(GUARD_HEADER) != "1":
            raise HTTPException(status_code=403, detail=f"missing the {GUARD_HEADER} header")

    def install(self, app: FastAPI, snapshot: Callable[[], dict[str, Any]]) -> None:
        """ページと API の経路を足す。`snapshot` は今の目標（ゴールボードのもの）。"""

        @app.get("/admin", response_class=HTMLResponse)
        async def page(request: Request) -> str:
            self._guard(request)
            return ADMIN_HTML.read_text(encoding="utf-8")

        @app.get("/api/admin/state")
        async def state(request: Request) -> dict[str, Any]:
            self._guard(request)
            goals = snapshot()
            return {
                "goal": goals.get("goal"),
                "objectives": goals.get("objectives") or [],
                "tree": goal_tree(goals),
                "presets": [asdict(p) for p in self._presets.all()],
                "log": list(self._log),
            }

        @app.post("/api/admin/say")
        async def say(body: _Say, request: Request) -> dict[str, Any]:
            self._guard(request)
            return await self._say(body.text, "say")

        @app.post("/api/admin/presets")
        async def add_preset(body: _Preset, request: Request) -> dict[str, Any]:
            self._guard(request)
            try:
                return asdict(self._presets.add(body.title, body.text, body.category))
            except PresetError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e

        @app.put("/api/admin/presets/{preset_id}")
        async def update_preset(preset_id: str, body: _Preset, request: Request) -> dict[str, Any]:
            self._guard(request)
            try:
                return asdict(self._presets.update(preset_id, body.title, body.text, body.category))
            except PresetError as e:
                raise HTTPException(status_code=_status(e), detail=str(e)) from e

        @app.delete("/api/admin/presets/{preset_id}")
        async def remove_preset(preset_id: str, request: Request) -> dict[str, Any]:
            self._guard(request)
            try:
                self._presets.remove(preset_id)
            except PresetError as e:
                raise HTTPException(status_code=_status(e), detail=str(e)) from e
            return {"ok": True}

        @app.post("/api/admin/presets/{preset_id}/say")
        async def say_preset(preset_id: str, request: Request) -> dict[str, Any]:
            self._guard(request)
            try:
                preset = self._presets.get(preset_id)
            except PresetError as e:
                raise HTTPException(status_code=404, detail=str(e)) from e
            return await self._say(preset.text, f"preset:{preset.title}")

        @app.post("/api/admin/instruct")
        async def instruct(body: _Instruct, request: Request) -> dict[str, Any]:
            self._guard(request)
            try:
                text = clean_text(body.text)
            except PresetError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            logger.info(f"[admin] 指示: {text}")
            reply = await self._instruct(text, body.reply)
            self._note("instruct", text, reply)
            return {"ok": True, "reply": reply}

        @app.post("/api/admin/rethink")
        async def rethink(body: _Rethink, request: Request) -> dict[str, Any]:
            self._guard(request)
            reason = body.reason.strip()
            accepted = self._rethink(reason)
            self._note("rethink", reason or "(理由なし)", "受け付けた" if accepted else "プレイ前")
            return {"ok": accepted}

    async def _say(self, text: str, kind: str) -> dict[str, Any]:
        try:
            text = clean_text(text)
        except PresetError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        logger.info(f"[admin] 喋らせる: {text}")
        spoken = await self._speak(text)
        self._note(kind, text, "" if spoken else "読み上げがオフ（声は出ていない）")
        return {"ok": True, "spoken": spoken}


def _status(error: PresetError) -> int:
    return 404 if str(error).startswith("no preset") else 400
