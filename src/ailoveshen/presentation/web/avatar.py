"""
アバター（VRM）: 配信者の 3D モデルを、話したことやゲームの出来事に合わせて動かす
（docs/design/24_avatar.md）。

- GET /avatar              OBS のブラウザソース用のページ（three.js + three-vrm、背景は透明。
                           ?demo=1 でサーバーなしの見本、?view=full、?pos=left、?scale=0.9）
- GET /api/avatar/stream   アバターへの合図（Server-Sent Events）
- GET /assets/avatar.vrm   VRM のモデル（設定 avatar.model_path）
- GET /vendor/...          three.js と three-vrm（CDN につながらなくても動くように同梱）

合図は 3 種類だけ: 話す（本文と長さ。口の動きはページが本文から作る）、話し終わる、表情や
しぐさ（うれしい・驚いた・かなしい、うなずく・よろこぶ）。ゲームの判断には関わらない
（読み取るだけ）。

表情としぐさは、`AvatarDirector` があれば状況と発言から Jev が選ぶ（§8）。口はすぐ動かし、
表情は Jev の答えが来たとき（0.2〜0.6 秒後）に変える。Jev が答えられなければ、ここの規則
（本文の手がかり、出来事ごとの表）の答えを使う。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventSubscriber
from ailoveshen.application.use_cases.avatar_director import AvatarDirector, AvatarReaction
from ailoveshen.domain.events import (
    ChatResponseGeneratedEvent,
    CommentaryGeneratedEvent,
    DomainEvent,
    GameActionExecutedEvent,
    GoalEndedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    SkillLearnedEvent,
    SpeechCompletedEvent,
    SpeechStartedEvent,
    TownSiteChosenEvent,
)
from ailoveshen.domain.value_objects import EmotionType

AVATAR_HTML = Path(__file__).with_name("avatar.html")
VENDOR_DIR = Path(__file__).with_name("vendor")
KEEPALIVE_SECONDS = 15.0
QUEUE_SIZE = 32

# TTS がないときの話す長さの見積もり（1 文字あたり）。日本語はおよそ 1 秒に 7〜8 モーラ
MS_PER_CHAR = 140
MIN_SPEECH_MS = 800
MAX_SPEECH_MS = 20_000

# 話したことから表情を選ぶ手がかり（上から順に見る）。見つからなければ表情は変えない
TEXT_CUES: tuple[tuple[EmotionType, re.Pattern[str]], ...] = (
    (EmotionType.SURPRISED, re.compile(r"うわ|えっ|びっくり|まさか|なにこれ|ひゃ")),
    (EmotionType.SAD, re.compile(r"ざんねん|残念|かなしい|悲しい|しょんぼり|うう|ごめん")),
    (EmotionType.ANGRY, re.compile(r"むっ|もう[！!]|許さない|ゆるさない|くやしい|悔しい")),
    (
        EmotionType.HAPPY,
        re.compile(
            r"やった|うれしい|嬉しい|できた|ありがとう|たのしい|楽しい|かんせい|完成|わーい"
        ),
    ),
)

LIP_SYNC_SOURCES = ("auto", "text", "tts")


def speech_ms(text: str) -> int:
    """TTS がないときに、口を動かす長さを本文から見積もる。"""
    return max(MIN_SPEECH_MS, min(MAX_SPEECH_MS, len(text.strip()) * MS_PER_CHAR))


def emotion_from_text(text: str) -> EmotionType | None:
    """話したことから表情を選ぶ（手がかりがなければ None）。"""
    for emotion, pattern in TEXT_CUES:
        if pattern.search(text):
            return emotion
    return None


def _emote(emotion: EmotionType, intensity: float, seconds: float, gesture: str | None = None):
    return {
        "type": "emote",
        "emotion": emotion.value,
        "intensity": intensity,
        "seconds": seconds,
        "gesture": gesture,
    }


class AvatarStage:
    """
    ドメインイベントを、アバターのページへの合図にして送る。

    口の動きの元（`lip_sync`）:
    - "tts": 音声の再生の開始・終了（SpeechStarted/CompletedEvent）に合わせる
    - "text": 実況・返答の生成（Commentary/ChatResponseGeneratedEvent）で、長さは本文から見積もる
      （TTS を使わない結合テストの `[say]` のとき）
    - "auto": 最初は "text"。音声の再生のイベントが一度でも来たら "tts" に切り替える
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        lip_sync: str = "auto",
        director: AvatarDirector | None = None,
    ) -> None:
        """
        Args:
            model_path: VRM のファイル。None か、ファイルがなければ /assets/avatar.vrm は 404
            lip_sync: 口の動きの元（"auto"、"text"、"tts"）
            director: 表情としぐさを Jev に選ばせるもの。None なら規則だけ

        Raises:
            ValueError: lip_sync が不明なとき
        """
        if lip_sync not in LIP_SYNC_SOURCES:
            raise ValueError(f"lip_sync must be one of {LIP_SYNC_SOURCES}, got {lip_sync!r}")
        self._model = Path(model_path) if model_path else None
        self._lip_sync = lip_sync
        self._tts_seen = False
        self._listeners: set[asyncio.Queue[str | None]] = set()
        self._director = director
        self._pending: set[asyncio.Task] = set()  # Jev の答えを待っている判断

    def subscribe(self, bus: IEventSubscriber) -> None:
        """合図のもとになるイベントを購読する。"""
        for event_type in (
            SpeechStartedEvent,
            SpeechCompletedEvent,
            CommentaryGeneratedEvent,
            ChatResponseGeneratedEvent,
            GoalSetEvent,
            GoalEndedEvent,
            MidGoalAddedEvent,
            MidGoalCompletedEvent,
            HouseCompletedEvent,
            TownSiteChosenEvent,
            GameActionExecutedEvent,
            SkillLearnedEvent,
        ):
            bus.subscribe(event_type, self._on_event)

    def cues_for(self, event: DomainEvent) -> list[dict[str, Any]]:
        """1 つのイベントから、アバターへの合図（0 個以上）を作る。"""
        if isinstance(event, SpeechStartedEvent):
            self._tts_seen = True
            if self._uses_tts():
                emotion = event.emotion.primary
                cue = emotion_from_text(event.text) if emotion == EmotionType.NEUTRAL else emotion
                return [self._speak(event.text, event.duration_ms or speech_ms(event.text), cue)]
            return []
        if isinstance(event, SpeechCompletedEvent):
            return [{"type": "quiet"}] if self._uses_tts() else []
        if isinstance(event, (CommentaryGeneratedEvent, ChatResponseGeneratedEvent)):
            if self._uses_tts() or not event.text.strip():
                return []
            return [self._speak(event.text, speech_ms(event.text), emotion_from_text(event.text))]
        if isinstance(event, (MidGoalCompletedEvent, HouseCompletedEvent, SkillLearnedEvent)):
            return [_emote(EmotionType.HAPPY, 1.0, 4.0, "cheer")]
        if isinstance(event, TownSiteChosenEvent):
            return [_emote(EmotionType.HAPPY, 0.8, 3.0, "nod")]
        if isinstance(event, MidGoalAddedEvent) and event.requested_by:
            return [_emote(EmotionType.HAPPY, 0.7, 2.5, "nod")]
        if isinstance(event, GoalEndedEvent):
            if event.met:
                return [_emote(EmotionType.HAPPY, 0.5, 2.0, "nod")]
            if " is stuck " in event.ended_because or " stalled " in event.ended_because:
                return [_emote(EmotionType.SAD, 0.6, 3.0, None)]
            return []
        if isinstance(event, GoalSetEvent):
            return [{"type": "gesture", "gesture": "nod"}]
        if isinstance(event, GameActionExecutedEvent) and not event.ok:
            if "took damage" in event.result:
                return [_emote(EmotionType.SURPRISED, 0.9, 1.8, "flinch")]
        return []

    def install(self, app: FastAPI) -> None:
        """ページ、合図のストリーム、モデル、ライブラリの経路を足す。"""

        @app.get("/avatar", response_class=HTMLResponse)
        async def avatar() -> str:
            return AVATAR_HTML.read_text(encoding="utf-8")

        @app.get("/api/avatar/stream")
        async def stream(request: Request) -> StreamingResponse:
            return StreamingResponse(
                self._stream(request),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        @app.get("/assets/avatar.vrm")
        async def model() -> Response:
            if self._model is None or not self._model.is_file():
                return Response(status_code=404)
            return FileResponse(self._model, media_type="model/gltf-binary")

        app.mount("/vendor", StaticFiles(directory=VENDOR_DIR), name="vendor")

    def _uses_tts(self) -> bool:
        return self._lip_sync == "tts" or (self._lip_sync == "auto" and self._tts_seen)

    @staticmethod
    def _speak(text: str, duration_ms: int, emotion: EmotionType | None) -> dict[str, Any]:
        return {
            "type": "speak",
            "text": text,
            "duration_ms": duration_ms,
            "emotion": emotion.value if emotion else None,
        }

    async def _on_event(self, event: DomainEvent) -> None:
        for cue in self.cues_for(event):
            if self._director is None or cue["type"] not in ("speak", "emote"):
                self._send(cue)
                continue
            # Jev に選ばせる: 口はすぐ動かし、表情としぐさは答えが来たら
            if cue["type"] == "speak":
                rule = AvatarReaction(emotion=cue["emotion"], intensity=0.7)
                seconds = max(2.5, cue["duration_ms"] / 1000)
                self._send({**cue, "emotion": None})
                self._decide("speaking", cue["text"], rule, seconds)
            else:
                rule = AvatarReaction(cue["emotion"], cue["intensity"], cue["gesture"])
                self._decide(moment_of(event), "", rule, cue["seconds"])

    async def close(self) -> None:
        """待っている判断をやめ、判断モデルを閉じる。"""
        for task in list(self._pending):
            task.cancel()
        if self._director is not None:
            await self._director.close()

    def _decide(self, moment: str, line: str, rule: AvatarReaction, seconds: float) -> None:
        task = asyncio.ensure_future(self._react(moment, line, rule, seconds))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _react(self, moment: str, line: str, rule: AvatarReaction, seconds: float) -> None:
        assert self._director is not None
        reaction = await self._director.react(moment, line, rule)
        if reaction.emotion is None and reaction.gesture is None:
            return
        self._send(
            {
                "type": "emote",
                "emotion": reaction.emotion,
                "intensity": reaction.intensity,
                "seconds": seconds,
                "gesture": reaction.gesture,
                "source": reaction.source,
            }
        )

    def end_streams(self) -> None:
        """開いているページへのストリームを終わらせる（サーバーを止めるとき）。"""
        for queue in self._listeners:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)

    def _send(self, cue: dict[str, Any]) -> None:
        self._broadcast(json.dumps(cue, ensure_ascii=False))

    def _broadcast(self, data: str) -> None:
        for queue in self._listeners:
            if queue.full():
                queue.get_nowait()  # 遅いページには新しいものを優先する
            queue.put_nowait(data)

    async def _stream(self, request: Request) -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._listeners.add(queue)
        logger.debug(f"アバターのページがつながった（{len(self._listeners)}）")
        try:
            yield ": connected\n\n"
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


def moment_of(event: DomainEvent) -> str:
    """ゲームの出来事を、Jev に渡す「今起きていること」の 1 行にする。"""
    if isinstance(event, MidGoalCompletedEvent):
        return f"a mid goal was completed: {event.title}"
    if isinstance(event, HouseCompletedEvent):
        return f"the house was completed: {event.name}"
    if isinstance(event, TownSiteChosenEvent):
        return f"chose where to build the town: {event.name}"
    if isinstance(event, MidGoalAddedEvent):
        return f"accepted a request from viewer {event.requested_by}: {event.title}"
    if isinstance(event, GoalEndedEvent):
        state = "done" if event.met else "gave up"
        return f"a small goal ended ({state}): {event.goal}, because {event.ended_because}"
    if isinstance(event, GameActionExecutedEvent):
        return f"took damage while doing {event.action_id}: {event.result}"
    return type(event).__name__
