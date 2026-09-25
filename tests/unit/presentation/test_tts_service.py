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
