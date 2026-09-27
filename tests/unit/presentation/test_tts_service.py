"""TTSService のテスト。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from ailoveshen.application.dto.speech_dto import SpeakTextResponse
from ailoveshen.domain.value_objects import SpeechPriority
from ailoveshen.presentation.services.tts_service import TTSService


def _slow_use_case(delay: float, played: list[str]) -> AsyncMock:
    """execute() に `delay` 秒かかり、テキストを記録するユースケース。"""

    async def execute(request):
        await asyncio.sleep(delay)
        played.append(request.text)
        return SpeakTextResponse.ok(duration_ms=int(delay * 1000))

    use_case = AsyncMock()
    use_case.execute.side_effect = execute
    return use_case


class TestTTSServiceWaitUntilIdle:
    """wait_until_idle() のテスト。"""

    @pytest.mark.asyncio
    async def test_waits_for_item_being_played(self):
        """キューから取り出し済みのリクエストを待つ。"""
        played: list[str] = []
        service = TTSService(_slow_use_case(0.2, played))
        await service.start()

        await service.speak("one")
        await service.speak("two")
        await asyncio.sleep(0.05)  # "one" を再生中で、キューの長さは 1

        await asyncio.wait_for(service.wait_until_idle(), timeout=2.0)

        assert played == ["one", "two"]
        await service.stop()

    @pytest.mark.asyncio
    async def test_returns_immediately_when_nothing_queued(self):
        """再生するものがなければすぐに戻る。"""
        service = TTSService(_slow_use_case(0.0, []))
        await service.start()

        await asyncio.wait_for(service.wait_until_idle(), timeout=0.5)
        await service.stop()

    @pytest.mark.asyncio
    async def test_does_not_hang_after_use_case_error(self):
        """失敗したリクエストも済みにする。"""
        use_case = AsyncMock()
        use_case.execute.side_effect = RuntimeError("boom")
        service = TTSService(use_case)
        await service.start()

        await service.speak("fails")

        await asyncio.wait_for(service.wait_until_idle(), timeout=2.0)
        await service.stop()

    @pytest.mark.asyncio
    async def test_does_not_hang_after_stop_during_playback(self):
        """再生の途中の stop() は、再生中のリクエストと捨てたリクエストを済みにする。"""
        service = TTSService(_slow_use_case(10.0, []))
        await service.start()

        await service.speak("long")
        await service.speak("queued", priority=SpeechPriority.HIGH)
        await asyncio.sleep(0.05)  # "long" を再生中で、"queued" が待っている

        await service.stop()

        await asyncio.wait_for(service.wait_until_idle(), timeout=0.5)


@pytest.mark.asyncio
async def test_is_idle_only_after_playing_ends():
    """キューが空でも、合成中・再生中は空いていない（黙っている時間を再生の終わりから測る）。"""
    played: list[str] = []
    service = TTSService(_slow_use_case(0.2, played))
    await service.start()
    assert service.is_idle()
    await service.speak("one")
    await asyncio.sleep(0.05)  # 取り出して再生中（キューは空）
    assert service.get_queue_size() == 0 and not service.is_idle()
    await service.wait_until_idle()
    assert service.is_idle()
    await service.stop()


class TestStaleLines:
    """待っている間に古くなった実況は読まない（2026-09-27）。"""

    @pytest.mark.asyncio
    async def test_commentary_that_waited_too_long_is_dropped_but_replies_are_read(self):
        played: list[str] = []
        now = {"t": 0.0}
        service = TTSService(
            _slow_use_case(0.05, played),
            max_age_seconds={"commentary": 20},
            clock=lambda: now["t"],
        )
        await service.start()
        await service.speak("今話している", source="commentary")
        await asyncio.sleep(0.01)  # 1 行目を再生中
        await service.speak("かまどを作るよ", source="commentary")
        await service.speak("返事だよ", source="chat")
        now["t"] = 30.0  # 2 行目と 3 行目は 30 秒待った

        await asyncio.wait_for(service.wait_until_idle(), timeout=2.0)

        assert played == ["今話している", "返事だよ"]
        assert service.is_idle()
        await service.stop()
