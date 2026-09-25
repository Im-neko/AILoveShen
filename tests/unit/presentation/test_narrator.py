"""Tests for Narrator (goal changes said aloud)."""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.domain.events import GoalEndedEvent, GoalSetEvent, ViewerRequestRejectedEvent
from ailoveshen.domain.value_objects import Activity
from ailoveshen.presentation.services.narrator import Narrator

ACTIVITY = Activity()


@pytest.fixture
def llm():
    """Mock LLM service."""
    service = AsyncMock()
    service.generate_commentary.return_value = "次は羊を探すよ"
    return service


@pytest.fixture
def said():
    """What the narrator said."""
    return []


@pytest.fixture
def narrator(llm, said):
    async def say(text: str) -> None:
        said.append(text)

    return Narrator(llm, activity=lambda: ACTIVITY, say=say)


def _events(llm) -> list[list[str]]:
    return [c.kwargs["recent_events"] for c in llm.generate_commentary.call_args_list]


class TestNarrator:
    """Tests for Narrator."""

    @pytest.mark.asyncio
    async def test_goal_change_is_told_with_why_the_last_one_ended(self, narrator, llm, said):
        """Test the end and the next goal make one utterance, with the activity."""
        await narrator.on_goal_ended(
            GoalEndedEvent(goal="placed(bed, home)", ended_because="stalled", met=False)
        )
        await narrator.on_goal_set(GoalSetEvent(goal="through_night()", reason="日が暮れる"))
        await narrator.drain()

        assert _events(llm) == [
            [
                "目標 placed(bed, home) が未達成でやめた（stalled）",
                "新しい目標: through_night()（日が暮れる）",
            ]
        ]
        assert llm.generate_commentary.call_args.kwargs["activity"] is ACTIVITY
        assert said == ["次は羊を探すよ"]

    @pytest.mark.asyncio
    async def test_requested_goal_is_not_announced_again(self, narrator, llm):
        """Test a goal the reply already promised is not repeated."""
        await narrator.on_goal_ended(
            GoalEndedEvent(goal="have(log, 3)", ended_because="viewer neko asked: ベッド")
        )
        await narrator.on_goal_set(
            GoalSetEvent(goal="placed(bed, home)", reason="頼まれた", requested_by="neko")
        )
        await narrator.drain()

        llm.generate_commentary.assert_not_called()

    @pytest.mark.asyncio
    async def test_dropped_request_is_told_even_for_another_request(self, narrator, llm):
        """Test replacing one viewer's unmet request with another's is still told."""
        await narrator.on_goal_ended(
            GoalEndedEvent(
                goal="placed(bed, home)",
                ended_because="viewer inu asked: 探検して",
                requested_by="neko",
            )
        )
        await narrator.on_goal_set(
            GoalSetEvent(goal="explored(40)", reason="頼まれた", requested_by="inu")
        )
        await narrator.drain()

        assert _events(llm)[0][0] == (
            "目標 placed(bed, home)（nekoさんの頼み） が"
            "未達成でやめた（viewer inu asked: 探検して）"
        )

    @pytest.mark.asyncio
    async def test_rejected_request_is_told(self, narrator, llm):
        """Test a promise that cannot be kept is told (never dropped silently)."""
        await narrator.on_request_rejected(
            ViewerRequestRejectedEvent(goal="cleared()", user_name="neko", reason="it is night")
        )
        await narrator.drain()

        assert _events(llm) == [
            ["nekoさんに引き受けた cleared() は、やっぱりできなかった（it is night）"]
        ]

    @pytest.mark.asyncio
    async def test_empty_commentary_says_nothing(self, narrator, llm, said):
        """Test nothing is said when generation fails."""
        llm.generate_commentary.return_value = ""
        await narrator.on_goal_set(GoalSetEvent(goal="built()", reason=""))
        await narrator.drain()

        assert said == []
