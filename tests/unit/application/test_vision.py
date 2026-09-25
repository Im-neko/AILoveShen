"""配信の画面を見せるテスト（docs/design/23_screen_vision.md）。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import ToolChoice
from ailoveshen.application.use_cases.vision import ScreenReviewer, VisionPolicy
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import GoalStatus, Screenshot, ToolOutcome
from tests.unit.application.test_play_use_cases import HAVE_PLANKS, _obs, _session

SHOT = Screenshot(data=b"\xff\xd8jpeg", mime_type="image/jpeg")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _reviewer(review=None, shot=SHOT, clock=None):
    capture = AsyncMock()
    capture.capture.return_value = shot
    generator = AsyncMock()
    generator.generate_json.return_value = review or {
        "seen": "木の前に立っている",
        "matches_goal": True,
        "concern": "",
        "rethink": False,
    }
    builder = Mock()
    builder.build_screen_review_prompt.return_value = "review prompt"
    reviewer = ScreenReviewer(
        capture,
        generator,
        builder,
        VisionPolicy(review_interval_seconds=240, failure_interval_seconds=60),
        clock=clock or Clock(),
    )
    return reviewer, capture, generator


class TestPeriodicReview:
    @pytest.mark.asyncio
    async def test_the_screen_is_reviewed_once_per_interval(self):
        clock = Clock()
        reviewer, capture, generator = _reviewer(clock=clock)
        session = _session(HAVE_PLANKS)

        await reviewer.review_if_due(session)  # 時計を始めるだけ
        clock.now = 100
        await reviewer.review_if_due(session)
        assert generator.generate_json.await_count == 0

        clock.now = 241
        await reviewer.review_if_due(session)
        kwargs = generator.generate_json.await_args.kwargs
        assert kwargs["purpose"] == "screen_review"
        assert kwargs["images"] == [SHOT]
        assert session.screen_note.seen == "木の前に立っている"
        assert session.activity().screen_note is session.screen_note
        assert session.goal_end_reason(_obs()) == ""  # 考え直さない

        clock.now = 300
        await reviewer.review_if_due(session)
        assert generator.generate_json.await_count == 1

    @pytest.mark.asyncio
    async def test_rethink_ends_the_small_goal_at_the_next_boundary(self):
        clock = Clock()
        reviewer, _, _ = _reviewer(
            {
                "seen": "暗い縦穴の底にいる",
                "matches_goal": False,
                "concern": "穴から出られていない",
                "rethink": True,
            },
            clock=clock,
        )
        session = _session(HAVE_PLANKS)
        await reviewer.review_if_due(session)
        clock.now = 500
        await reviewer.review_if_due(session)

        reason = session.goal_end_reason(_obs())
        assert reason == "goal have(planks, 4) is reconsidered: on screen: 穴から出られていない"
        assert session.needs_new_goal(_obs())
        # 達成していれば、考え直しではなく達成として終わる
        assert session.goal_end_reason(_obs(met=True, remaining=0)) == "goal have(planks, 4) is met"

    @pytest.mark.asyncio
    async def test_no_screen_or_a_failed_review_changes_nothing(self):
        clock = Clock()
        reviewer, _, generator = _reviewer(shot=None, clock=clock)
        session = _session(HAVE_PLANKS)
        await reviewer.review_if_due(session)
        clock.now = 500
        await reviewer.review_if_due(session)
        generator.generate_json.assert_not_awaited()

        reviewer, _, generator = _reviewer(clock=clock)
        generator.generate_json.side_effect = TextGenerationError("down")
        await reviewer.review_if_due(session)
        clock.now = 1000
        await reviewer.review_if_due(session)
        assert session.screen_note is None


@pytest.mark.asyncio
async def test_a_screenshot_after_failure_at_most_once_per_interval():
    clock = Clock()
    reviewer, capture, _ = _reviewer(clock=clock)
    assert await reviewer.after_failure() is SHOT
    clock.now = 30
    assert await reviewer.after_failure() is None
    clock.now = 61
    assert await reviewer.after_failure() is SHOT
    assert capture.capture.await_count == 2


class TestToolStepWithScreen:
    """道具モード: look_screen の画像は同じステップの次の選択にだけ、失敗の後は画像を添える。"""

    def _use_case(self, screen):
        from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
        from ailoveshen.application.use_cases.notes import NoteKeeper
        from ailoveshen.application.use_cases.play import AdvancePlayUseCase
        from ailoveshen.domain.entities import Conversation

        bridge = AsyncMock()
        bridge.observe.return_value = _obs()
        bridge.set_goal.return_value = GoalStatus(met=False, remaining=3)
        bridge.state.return_value = {"action": None}
        watcher = AsyncMock()
        watcher.run.side_effect = lambda call: ToolOutcome(call, False, "failed: no path", 2.0)
        store = Mock()
        store.load.return_value = None
        notes = Mock()
        notes.load.return_value = None
        self.generator = AsyncMock()
        builder = Mock()
        builder.build_tool_prompt.return_value = "tool prompt"
        events = AsyncMock()
        return AdvancePlayUseCase(
            bridge=bridge,
            text_generator=self.generator,
            prompt_builder=builder,
            action_selector=AsyncMock(),
            event_publisher=events,
            conversation=Conversation(),
            mid_goals=MidGoalKeeper(bridge=bridge, event_publisher=events, store=store),
            town=AsyncMock(),
            notes=NoteKeeper(notes),
            control="tools",
            tool_watcher=watcher,
            screen=screen,
        )

    @pytest.mark.asyncio
    async def test_look_screen_goes_to_the_next_call_only_and_failure_brings_a_screenshot(self):
        screen = AsyncMock()
        screen.look.return_value = SHOT
        screen.after_failure.return_value = SHOT
        use_case = self._use_case(screen)
        self.generator.choose_tool.side_effect = [
            ToolChoice("look_screen", {}),
            ToolChoice("goto", {"x": 1, "y": 70, "z": 1, "intent": "穴から出る"}),
            ToolChoice("wait", {"intent": "待つ"}),
        ]
        session = _session(HAVE_PLANKS)

        await use_case.execute(session)  # 見る → 歩く（失敗）
        await use_case.execute(session)  # 失敗の後なので画面つき

        calls = self.generator.choose_tool.call_args_list
        assert "look_screen" in {t.name for t in calls[0].args[1]}
        assert [c.kwargs["images"] for c in calls] == [[], [SHOT], [SHOT]]
        screen.review_if_due.assert_awaited()

    @pytest.mark.asyncio
    async def test_without_a_screen_look_screen_is_not_offered(self):
        use_case = self._use_case(None)
        self.generator.choose_tool.side_effect = [ToolChoice("wait", {"intent": "待つ"})]
        await use_case.execute(_session(HAVE_PLANKS))
        offered = {t.name for t in self.generator.choose_tool.call_args.args[1]}
        assert "look_screen" not in offered
