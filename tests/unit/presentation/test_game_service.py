"""GameService のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.game_dto import PlayStepReport
from ailoveshen.domain.entities import MidGoalPlan, PlaySession
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    Mission,
    Side,
)
from ailoveshen.presentation.services.game_service import GameService


def _report(complete=False, waiting=False) -> PlayStepReport:
    return PlayStepReport(
        goal=Goal(GoalSpec(GoalPredicate.BUILT)),
        goal_changed=False,
        status=GoalStatus(met=complete, remaining=0 if complete else 5),
        decision=None if waiting else ActionDecision("dig oak_log at 1,2,3", 0.9),
        result=None if waiting else ActionResult("dig oak_log at 1,2,3", True, "ok", 1.0),
        house_complete=complete,
        waiting=waiting,
    )


@pytest.fixture
def start():
    """セッションを返す開始のユースケースのモック。"""
    use_case = AsyncMock()
    use_case.execute.return_value = PlaySession(
        blueprint=HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2),
        plan=MidGoalPlan(mission=Mission("家を建てる")),
    )
    return use_case


def _service(start, advance, closers=None):
    closers = closers or (AsyncMock(), AsyncMock(), AsyncMock())
    return GameService(start, advance, *closers, mid_goals=Mock())


class TestGameService:
    """play と close のテスト。"""

    @pytest.mark.asyncio
    async def test_plays_on_after_the_house_is_complete(self, start):
        """完了しても、ステップの予算までセッションは続く。"""
        advance = AsyncMock()
        advance.execute.side_effect = [_report(), _report(complete=True), _report(complete=True)]

        outcome = await _service(start, advance).play(max_steps=3)

        assert outcome.house_complete
        assert outcome.steps == 3
        assert advance.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_stops_at_step_budget(self, start):
        """実行は、完了したかどうかに関係なく max_steps 回の行動で終わる。"""
        advance = AsyncMock()
        advance.execute.return_value = _report()

        outcome = await _service(start, advance).play(max_steps=4)

        assert not outcome.house_complete
        assert outcome.steps == 4
        assert advance.execute.await_count == 4

    @pytest.mark.asyncio
    async def test_waiting_for_the_reflex_is_not_a_step(self, start, monkeypatch):
        """ブリッジが忙しくて飛ばしたステップは予算を使わない。"""
        monkeypatch.setattr(GameService, "WAIT_SECONDS", 0)
        advance = AsyncMock()
        advance.execute.side_effect = [_report(waiting=True), _report(), _report()]

        outcome = await _service(start, advance).play(max_steps=2)

        assert outcome.steps == 2
        assert advance.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_close_closes_all_clients(self, start):
        """close() はブリッジ、LLM、行動選択を閉じる。"""
        closers = (AsyncMock(), AsyncMock(), AsyncMock())

        await _service(start, AsyncMock(), closers).close()

        for c in closers:
            c.close.assert_awaited_once()


class TestKeepGoing:
    """配信用（keep_going）: 失敗しても止めずに待ってやり直す。"""

    @pytest.mark.asyncio
    async def test_failures_are_retried_with_growing_waits(self, start, monkeypatch):
        waits = []

        async def fake_sleep(seconds):
            waits.append(seconds)

        monkeypatch.setattr("asyncio.sleep", fake_sleep)
        start.execute.side_effect = [ConnectionError("bridge down"), start.execute.return_value]
        advance = AsyncMock()
        advance.execute.side_effect = [
            RuntimeError("gemini 503"),
            RuntimeError("gemini 503"),
            _report(),
            RuntimeError("jev"),
            _report(),
        ]
        service = _service(start, advance)

        outcome = await service.play(max_steps=2, keep_going=True)

        assert outcome.steps == 2
        # 開始の失敗 1 回、ステップの失敗 2 回（倍に）、成功で戻って 1 回
        assert waits == [5.0, 5.0, 10.0, 5.0]

    @pytest.mark.asyncio
    async def test_without_keep_going_errors_propagate(self, start):
        advance = AsyncMock()
        advance.execute.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await _service(start, advance).play(max_steps=1)

    @pytest.mark.asyncio
    async def test_no_step_limit_runs_until_cancelled(self, start):
        import asyncio

        advance = AsyncMock()
        calls = 0

        async def step(session):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return _report()

        advance.execute.side_effect = step
        task = asyncio.create_task(_service(start, advance).play(max_steps=None, keep_going=True))
        while calls < 50:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
