"""Stream（本番の配信）のテスト。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.presentation.services.stream import Stream


class Board:
    def __init__(self):
        self.ports = []

    async def serve(self, host="127.0.0.1", port=8765):
        self.ports.append(port)
        await asyncio.Event().wait()


def _game():
    game = Mock()
    game.close = AsyncMock()

    async def play(max_steps, keep_going):
        game.play_args = (max_steps, keep_going)
        await asyncio.Event().wait()

    game.play = play
    return game


@pytest.mark.asyncio
async def test_runs_until_cancelled_then_closes_everything():
    game, llm, board = _game(), Mock(close=AsyncMock()), Board()
    chat = Mock(close=AsyncMock())
    responder = Mock()

    async def read_forever(source):
        await asyncio.Event().wait()

    responder.run = AsyncMock(side_effect=read_forever)
    closed = []

    async def stop_tts():
        closed.append("tts")

    async def broken():
        raise RuntimeError("already closed")

    stream = Stream(
        game,
        llm,
        board=board,
        board_port=9999,
        chat=chat,
        responder=responder,
        closers=(broken, stop_tts),
    )
    task = asyncio.create_task(stream.run())
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert game.play_args == (None, True)  # 上限なし、失敗しても止めない
    assert board.ports == [9999]
    responder.run.assert_awaited_once_with(chat)
    chat.close.assert_awaited_once()
    game.close.assert_awaited_once()
    llm.close.assert_awaited_once()
    assert closed == ["tts"]  # 1 つ閉じられなくても残りを閉じる


def test_chat_and_responder_go_together():
    with pytest.raises(ValueError):
        Stream(_game(), Mock(), chat=Mock())


@pytest.mark.asyncio
async def test_a_background_task_that_dies_is_reported_and_the_play_goes_on():
    from loguru import logger

    class BrokenBoard:
        async def serve(self, host="127.0.0.1", port=8765):
            raise OSError("in use")

    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="ERROR")
    game = _game()
    task = asyncio.create_task(Stream(game, Mock(close=AsyncMock()), board=BrokenBoard()).run())
    try:
        for _ in range(20):
            await asyncio.sleep(0)
        assert not task.done()  # プレイは続く
        assert any("目標ボード" in m and "OSError" in m for m in messages)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        logger.remove(sink)
