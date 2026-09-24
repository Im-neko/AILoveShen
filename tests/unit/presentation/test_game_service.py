"""Tests for GameService."""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.application.dto.game_dto import HouseStepReport
from ailoveshen.domain.entities import HouseProject
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    Goal,
    GoalType,
    HouseBlueprint,
    Side,
)
from ailoveshen.presentation.services.game_service import GameService


def _report(complete=False, waiting=False) -> HouseStepReport:
    return HouseStepReport(
        goal=Goal(GoalType.GATHER_WOOD),
        goal_changed=False,
        decision=None if waiting else ActionDecision("collect_log", 0.9),
        result=None if waiting else ActionResult("collect_log", True, "ok", 1.0),
        complete=complete,
        waiting=waiting,
    )


@pytest.fixture
def start():
    """Mock start use case returning a project."""
    use_case = AsyncMock()
    use_case.execute.return_value = HouseProject(
        blueprint=HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2)
    )
    return use_case


def _service(start, advance, closers=None):
    closers = closers or (AsyncMock(), AsyncMock(), AsyncMock())
    return GameService(start, advance, *closers)


class TestGameService:
    """Tests for play and close."""

    @pytest.mark.asyncio
    async def test_plays_on_after_the_house_is_complete(self, start):
        """Test the session keeps going after completion until the step budget."""
        advance = AsyncMock()
        advance.execute.side_effect = [_report(), _report(complete=True), _report(complete=True)]

        outcome = await _service(start, advance).play(max_steps=3)

        assert outcome.complete
        assert outcome.steps == 3
        assert advance.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_stops_at_step_budget(self, start):
        """Test the run ends after max_steps actions, complete or not."""
        advance = AsyncMock()
        advance.execute.return_value = _report()

        outcome = await _service(start, advance).play(max_steps=4)

        assert not outcome.complete
        assert outcome.steps == 4
        assert advance.execute.await_count == 4

    @pytest.mark.asyncio
    async def test_waiting_for_the_reflex_is_not_a_step(self, start, monkeypatch):
        """Test steps skipped while the bridge is busy do not use the budget."""
        monkeypatch.setattr(GameService, "WAIT_SECONDS", 0)
        advance = AsyncMock()
        advance.execute.side_effect = [_report(waiting=True), _report(), _report()]

        outcome = await _service(start, advance).play(max_steps=2)

        assert outcome.steps == 2
        assert advance.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_close_closes_all_clients(self, start):
        """Test close() closes the bridge, the LLM and the selector."""
        closers = (AsyncMock(), AsyncMock(), AsyncMock())

        await _service(start, AsyncMock(), closers).close()

        for c in closers:
            c.close.assert_awaited_once()
