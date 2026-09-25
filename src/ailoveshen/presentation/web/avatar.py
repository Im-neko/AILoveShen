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

    def __init__(self, model_path: str | Path | None = None, lip_sync: str = "auto") -> None:
        """
        Args:
            model_path: VRM のファイル。None か、ファイルがなければ /assets/avatar.vrm は 404
            lip_sync: 口の動きの元（"auto"、"text"、"tts"）

        Raises:
            ValueError: lip_sync が不明なとき
        """
        if lip_sync not in LIP_SYNC_SOURCES:
            raise ValueError(f"lip_sync must be one of {LIP_SYNC_SOURCES}, got {lip_sync!r}")
        self._model = Path(model_path) if model_path else None
        self._lip_sync = lip_sync
        self._tts_seen = False
        self._listeners: set[asyncio.Queue[str]] = set()

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
        if isinstance(event, (MidGoalCompletedEvent, HouseCompletedEvent)):
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
            self._broadcast(json.dumps(cue, ensure_ascii=False))

    def _broadcast(self, data: str) -> None:
        for queue in self._listeners:
            if queue.full():
                queue.get_nowait()  # 遅いページには新しいものを優先する
            queue.put_nowait(data)

    async def _stream(self, request: Request) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_SIZE)
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
                yield f"data: {data}\n\n"
        finally:
            self._listeners.discard(queue)
