"""Tests for TTSService."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from ailoveshen.application.dto.speech_dto import SpeakTextResponse
from ailoveshen.domain.value_objects import SpeechPriority
from ailoveshen.presentation.services.tts_service import TTSService


def _slow_use_case(delay: float, played: list[str]) -> AsyncMock:
    """Use case whose execute() takes `delay` seconds and records the text."""

    async def execute(request):
        await asyncio.sleep(delay)
        played.append(request.text)
        return SpeakTextResponse.ok(duration_ms=int(delay * 1000))

    use_case = AsyncMock()
    use_case.execute.side_effect = execute
    return use_case


class TestTTSServiceWaitUntilIdle:
    """Tests for wait_until_idle()."""

    @pytest.mark.asyncio
    async def test_waits_for_item_being_played(self):
        """Test it waits for the request already taken off the queue."""
        played: list[str] = []
        service = TTSService(_slow_use_case(0.2, played))
        await service.start()

        await service.speak("one")
        await service.speak("two")
        await asyncio.sleep(0.05)  # "one" is playing, queue size is 1

        await asyncio.wait_for(service.wait_until_idle(), timeout=2.0)

        assert played == ["one", "two"]
        await service.stop()

    @pytest.mark.asyncio
    async def test_returns_immediately_when_nothing_queued(self):
        """Test it returns right away when there is nothing to play."""
        service = TTSService(_slow_use_case(0.0, []))
        await service.start()

        await asyncio.wait_for(service.wait_until_idle(), timeout=0.5)
        await service.stop()

    @pytest.mark.asyncio
    async def test_does_not_hang_after_use_case_error(self):
        """Test a failing request is still marked done."""
        use_case = AsyncMock()
        use_case.execute.side_effect = RuntimeError("boom")
        service = TTSService(use_case)
        await service.start()

        await service.speak("fails")

        await asyncio.wait_for(service.wait_until_idle(), timeout=2.0)
        await service.stop()

    @pytest.mark.asyncio
    async def test_does_not_hang_after_stop_during_playback(self):
        """Test stop() mid-playback marks the playing and discarded requests done."""
        service = TTSService(_slow_use_case(10.0, []))
        await service.start()

        await service.speak("long")
        await service.speak("queued", priority=SpeechPriority.HIGH)
        await asyncio.sleep(0.05)  # "long" is playing, "queued" is waiting

        await service.stop()

        await asyncio.wait_for(service.wait_until_idle(), timeout=0.5)
