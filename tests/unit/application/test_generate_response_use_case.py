"""Tests for GenerateResponseUseCase."""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.domain.entities import Conversation, MidGoalPlan, PlaySession
from ailoveshen.domain.events import ChatResponseGeneratedEvent, MidGoalAddedEvent
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    Candidate,
    CharacterProfile,
    ConditionStatus,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    MessageRole,
    MessageType,
    Mission,
    Side,
)

BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
SWORD = GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1)
BED_REQUEST = {
    "reply": "いいよ、剣ができたら次にベッド作るね！",
    "request": "accept",
    "title": "ベッドで寝る",
    "conditions": [{"predicate": "placed", "item": "bed"}],
    "position": 2,
    "reason": "夜を飛ばせる",
}


def _playing(time_phase: str = "day") -> PlaySession:
    """A session with a home, working on the sword (m1), then food (m2)."""
    plan = MidGoalPlan(mission=Mission("生き延びながら街にしていく"))
    plan.add("剣を持つ", (SWORD,))
    plan.add("食料を蓄える", (GoalSpec(GoalPredicate.HAVE, item="food", count=8),))
    session = PlaySession(blueprint=HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2), plan=plan)
    session.set_goal(
        Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=3), "剣の材料", mid_goal_id="m1"),
        "day",
    )
    session.observe(
        GameObservation(
            state={},
            candidates=(Candidate("wait", {"verb": "wait"}),),
            health=20.0,
            food=20,
            goal=GoalStatus(met=False, remaining=3),
            time_phase=time_phase,
            has_plan=True,
            house_complete=True,
            has_home=True,
        )
    )
    return session


@pytest.fixture
def mock_text_generator():
    """Create mock text generator."""
    generator = AsyncMock()
    generator.generate.return_value = "nekoさん、ありがとう！"
    return generator


@pytest.fixture
def mock_prompt_builder():
    """Create mock prompt builder (sync methods)."""
    builder = Mock()
    builder.build_system_prompt.return_value = "System prompt"
    builder.build_chat_response_prompt.return_value = "Response prompt"
    return builder


@pytest.fixture
def mock_event_publisher():
    """Create mock event publisher."""
    return AsyncMock()


@pytest.fixture
def conversation():
    """Create conversation history."""
    return Conversation()


@pytest.fixture
def bridge():
    """Mock bridge: every condition can be judged (none met)."""
    b = AsyncMock()

    async def check(specs):
        return [ConditionStatus(s, False) for s in specs]

    b.check.side_effect = check
    return b


@pytest.fixture
def store():
    """Mock mission store."""
    return Mock()


@pytest.fixture
def use_case(
    mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation, bridge, store
):
    """Create GenerateResponseUseCase with mocked dependencies."""
    return GenerateResponseUseCase(
        text_generator=mock_text_generator,
        prompt_builder=mock_prompt_builder,
        event_publisher=mock_event_publisher,
        conversation=conversation,
        character=CharacterProfile(),
        mid_goals=MidGoalKeeper(bridge=bridge, event_publisher=mock_event_publisher, store=store),
    )


def _added(events) -> list[MidGoalAddedEvent]:
    return [
        c.args[0] for c in events.publish.call_args_list if isinstance(c.args[0], MidGoalAddedEvent)
    ]


def _request(message: str = "がんばれ") -> GenerateResponseRequest:
    return GenerateResponseRequest(user_name="neko", message=message, user_id="42")


class TestGenerateResponseUseCase:
    """Tests for GenerateResponseUseCase."""

    @pytest.mark.asyncio
    async def test_execute_success(self, use_case, mock_text_generator):
        """Test successful chat response generation."""
        response = await use_case.execute(_request())

        assert response.success is True
        assert response.text == "nekoさん、ありがとう！"
        assert response.original_message == "がんばれ"
        assert response.user_name == "neko"
        mock_text_generator.generate.assert_called_once_with(
            prompt="Response prompt",
            system_instruction="System prompt",
        )

    @pytest.mark.asyncio
    async def test_execute_records_chat_and_reply(self, use_case, conversation):
        """Test both the viewer chat and the reply are recorded in order."""
        await use_case.execute(_request())

        chat, reply = conversation.recent_messages()
        assert chat.role == MessageRole.VIEWER
        assert chat.content == "がんばれ"
        assert chat.speaker_id == "42"
        assert reply.role == MessageRole.STREAMER
        assert reply.message_type == MessageType.RESPONSE

    @pytest.mark.asyncio
    async def test_history_excludes_current_chat(self, use_case, mock_prompt_builder, conversation):
        """Test the chat being answered is not duplicated in the history."""
        conversation.add_streamer_message("洞窟だ！", MessageType.COMMENTARY)

        await use_case.execute(_request())

        kwargs = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs
        assert kwargs["user_name"] == "neko"
        assert kwargs["message"] == "がんばれ"
        assert [m.content for m in kwargs["context"].recent_messages] == ["洞窟だ！"]

    @pytest.mark.asyncio
    async def test_execute_publishes_event(self, use_case, mock_event_publisher):
        """Test ChatResponseGeneratedEvent is published."""
        await use_case.execute(_request())

        event = mock_event_publisher.publish.call_args.args[0]
        assert isinstance(event, ChatResponseGeneratedEvent)
        assert event.text == "nekoさん、ありがとう！"
        assert event.original_message == "がんばれ"
        assert event.user_name == "neko"

    @pytest.mark.asyncio
    async def test_execute_empty_text(
        self, use_case, mock_text_generator, mock_event_publisher, conversation
    ):
        """Test empty generation keeps the chat but records no reply."""
        mock_text_generator.generate.return_value = ""

        response = await use_case.execute(_request())

        assert response.success is False
        assert len(conversation) == 1  # only the viewer chat
        mock_event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_generation_error(self, use_case, mock_text_generator):
        """Test generation failure returns an error response with empty text."""
        mock_text_generator.generate.side_effect = TextGenerationError("API down")

        response = await use_case.execute(_request())

        assert response.success is False
        assert response.text == ""
        assert "API down" in response.error
        assert response.user_name == "neko"


class TestReplyWhilePlaying:
    """A reply while playing sees the activity and may accept the request as a mid goal."""

    def _request(self, session, message="ベッド作って！", user_name="neko"):
        return GenerateResponseRequest(user_name=user_name, message=message, session=session)

    @pytest.mark.asyncio
    async def test_reply_sees_the_activity(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        """Test the reply is built from the session's activity, able to take requests."""
        mock_text_generator.generate_json.return_value = {
            "reply": "剣のために木を集めてるよ",
            "request": "none",
        }
        session = _playing()

        response = await use_case.execute(self._request(session, "今なにしてるの？"))

        assert response.text == "剣のために木を集めてるよ"
        assert response.mid_goal is None
        kwargs = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs
        assert kwargs["context"].activity.goal.spec.describe() == "have(log, 3)"
        assert kwargs["takes_requests"] is True
        assert len(session.plan.pending) == 2

    @pytest.mark.asyncio
    async def test_accepted_request_joins_the_mid_goals_behind_the_current(
        self, use_case, mock_text_generator, mock_event_publisher, store
    ):
        """Test an accepted request is a mid goal (after the current one); the goal goes on."""
        mock_text_generator.generate_json.return_value = BED_REQUEST
        session = _playing()

        response = await use_case.execute(self._request(session))

        assert response.mid_goal.conditions == (BED,)
        assert [(g.title, g.requested_by) for g in session.plan.pending] == [
            ("剣を持つ", None),
            ("ベッドで寝る", "neko"),
            ("食料を蓄える", None),
        ]
        assert session.goal.spec.describe() == "have(log, 3)"  # not interrupted
        added = _added(mock_event_publisher)[0]
        assert (added.title, added.requested_by, added.position) == ("ベッドで寝る", "neko", 2)
        store.save.assert_called_with(session.plan)

    @pytest.mark.asyncio
    async def test_request_never_cuts_in(self, use_case, mock_text_generator):
        """Test a request asked to go first still goes behind the current mid goal."""
        mock_text_generator.generate_json.return_value = {**BED_REQUEST, "position": 1}
        session = _playing()

        await use_case.execute(self._request(session, "今すぐベッド作って！"))

        assert session.plan.current.title == "剣を持つ"

    @pytest.mark.asyncio
    async def test_second_request_from_the_same_viewer_is_declined(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        """Test a limit hit goes back to the model, whose declining reply is used."""
        explore = {
            **BED_REQUEST,
            "title": "板を集める",
            "conditions": [{"predicate": "have", "item": "planks", "count": 16}],
        }
        declined = {"reply": "ベッドの約束が先だから、また今度ね", "request": "decline"}
        mock_text_generator.generate_json.side_effect = [BED_REQUEST, explore, declined]
        session = _playing()

        await use_case.execute(self._request(session))
        response = await use_case.execute(self._request(session, "板も集めて"))

        assert response.text == "ベッドの約束が先だから、また今度ね"
        assert len(session.plan.pending) == 3
        error = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs["previous_error"]
        assert "neko already has a request" in error

    @pytest.mark.asyncio
    async def test_condition_the_bridge_cannot_judge_is_never_promised(
        self, use_case, mock_text_generator, mock_prompt_builder, bridge
    ):
        """Test an accepted request the world cannot judge is sent back, not added."""
        bridge.check.side_effect = GoalRejectedError("conditions rejected: unknown item diamond")
        mock_text_generator.generate_json.side_effect = [
            {**BED_REQUEST, "conditions": [{"predicate": "have", "item": "diamond", "count": 1}]},
            {"reply": "ダイヤはまだ無理かな", "request": "decline"},
        ]
        session = _playing()

        response = await use_case.execute(self._request(session, "ダイヤ掘って"))

        assert response.text == "ダイヤはまだ無理かな"
        assert len(session.plan.pending) == 2
        error = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs["previous_error"]
        assert "unknown item diamond" in error

    @pytest.mark.asyncio
    async def test_no_reply_after_repeated_unusable_requests(self, use_case, mock_text_generator):
        """Test nothing is said when every generation accepts something unusable."""
        mock_text_generator.generate_json.return_value = {
            "reply": "やるね！",
            "request": "accept",
            "title": "探検",
            "conditions": [{"predicate": "explored", "distance": 30}],
        }
        session = _playing()

        response = await use_case.execute(self._request(session, "探検して"))

        assert not response.success
        assert len(session.plan.pending) == 2

    @pytest.mark.asyncio
    async def test_without_the_mid_goals_replies_only_talk(
        self, mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation
    ):
        """Test a use case given no mid goals replies with text only."""
        use_case = GenerateResponseUseCase(
            text_generator=mock_text_generator,
            prompt_builder=mock_prompt_builder,
            event_publisher=mock_event_publisher,
            conversation=conversation,
            character=CharacterProfile(),
        )

        response = await use_case.execute(self._request(_playing()))

        assert response.text == "nekoさん、ありがとう！"
        mock_text_generator.generate_json.assert_not_called()
