"""Stream: 本番の配信を 1 つにまとめて動かす（python -m ailoveshen.stream、docs/streaming.md）。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from loguru import logger

from ailoveshen.application.ports.output.chat_source import IChatSource
from ailoveshen.application.ports.output.event_publisher import IEventSubscriber
from ailoveshen.domain.events import (
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
)
from ailoveshen.presentation.services.chat_responder import ChatResponder
from ailoveshen.presentation.services.game_service import GameService
from ailoveshen.presentation.services.llm_service import LLMService


class _Board(Protocol):
    async def serve(self, host: str = ..., port: int = ...) -> None: ...


class Stream:
    """
    配信の全部（プレイ、実況、チャットへの返事、目標ボード）を、止められるまで動かす。

    - プレイは失敗しても止めない（`GameService.play(keep_going=True)`、ステップの上限なし）
    - チャット（あれば）はプレイと並んで読み、1 つずつ返事をする
    - 止めるのはキャンセル（Ctrl-C）。止めるときは全部のクライアントを閉じる
    """

    def __init__(
        self,
        game: GameService,
        llm: LLMService,
        board: _Board | None = None,
        board_port: int = 0,
        chat: IChatSource | None = None,
        responder: ChatResponder | None = None,
        closers: tuple[Callable[[], Awaitable[Any]], ...] = (),
    ) -> None:
        """
        Args:
            game: プレイ
            llm: 実況と返事
            board: 目標ボード（OBS のブラウザソース）。None なら出さない
            board_port: 目標ボードのポート
            chat: 配信のチャット。None なら読まない
            responder: チャットへの返事（chat と組で渡す）
            closers: 止めるときに呼ぶもの（アバターの close、TTS の stop など）
        """
        if (chat is None) != (responder is None):
            raise ValueError("chat and responder go together")
        self._game = game
        self._llm = llm
        self._board = board
        self._board_port = board_port
        self._chat = chat
        self._responder = responder
        self._closers = closers

    def subscribe(self, bus: IEventSubscriber) -> None:
        """目標の変化をログに出す（配信の画面の外で、何が起きているかを追うため）。"""

        async def designed(e: HouseDesignedEvent) -> None:
            logger.info(f"[design] {e.name}: {e.concept}")

        async def goal(e: GoalSetEvent) -> None:
            serves = f" [{e.mid_goal} のため]" if e.mid_goal else " [生存]"
            logger.info(f"[goal] {e.goal}{serves}: {e.reason}")

        async def mid_added(e: MidGoalAddedEvent) -> None:
            who = f" [{e.requested_by} の頼み]" if e.requested_by else ""
            logger.info(f"[mid+] #{e.position} {e.title}{who}: {e.reason}")

        async def mid_done(e: MidGoalCompletedEvent) -> None:
            logger.info(f"[mid✓] {e.title}")

        async def mid_dropped(e: MidGoalDroppedEvent) -> None:
            logger.info(f"[mid×] {e.title}: {e.reason}")

        async def house(e: HouseCompletedEvent) -> None:
            logger.info(f"[done] {e.name} が完成")

        bus.subscribe(HouseDesignedEvent, designed)
        bus.subscribe(GoalSetEvent, goal)
        bus.subscribe(MidGoalAddedEvent, mid_added)
        bus.subscribe(MidGoalCompletedEvent, mid_done)
        bus.subscribe(MidGoalDroppedEvent, mid_dropped)
        bus.subscribe(HouseCompletedEvent, house)

    async def run(self) -> None:
        """キャンセルされるまで配信する。"""
        tasks: list[asyncio.Task[Any]] = []
        try:
            if self._board is not None:
                board = asyncio.create_task(self._board.serve(port=self._board_port))
                board.add_done_callback(_report_end("目標ボード（OBS のブラウザソース）"))
                tasks.append(board)
                base = f"http://127.0.0.1:{self._board_port}"
                logger.info(f"[obs] 目標: {base}/overlay/vtuber  アバター: {base}/avatar")
                logger.info(f"[debug] Gemini の思考: {base}/debug/gemini")
            if self._chat is not None and self._responder is not None:
                chat = asyncio.create_task(self._responder.run(self._chat))
                chat.add_done_callback(_report_end("チャットへの返事"))
                tasks.append(chat)
            await self._game.play(max_steps=None, keep_going=True)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._close()

    async def _close(self) -> None:
        closers: list[Callable[[], Awaitable[Any]]] = []
        if self._chat is not None:
            closers.append(self._chat.close)
        closers += [self._game.close, self._llm.close, *self._closers]
        for close in closers:
            try:
                await close()
            except Exception as e:  # noqa: BLE001 - 1 つ閉じられなくても残りを閉じる
                logger.warning(f"閉じるときに失敗した（{type(e).__name__}: {e}）")
        logger.info("配信を止めた")


def _report_end(what: str) -> Callable[[asyncio.Task[Any]], None]:
    """止めていないのに終わった裏のタスクを、ERROR でログに出す（黙って止まらない）。"""

    def report(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        reason = f"{type(error).__name__}: {error}" if error else "終わった"
        logger.opt(exception=error).error(
            f"{what}が止まった（{reason}）。配信は続くが、直すには再起動する"
        )

    return report
