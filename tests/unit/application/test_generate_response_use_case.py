"""Tests for GenerateResponseUseCase."""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.domain.entities import Conversation, PlaySession
from ailoveshen.domain.events import ChatResponseGeneratedEvent, ViewerRequestReplacedEvent
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import (
    Candidate,
    CharacterProfile,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    MessageRole,
    MessageType,
    Side,
)

BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")


def _playing(time_phase: str = "day") -> PlaySession:
    """A session with a home, building nothing, at the given time of day."""
    session = PlaySession(blueprint=HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2))
    session.set_goal(Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=3), "剣の材料"), "day")
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
def use_case(mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation):
    """Create GenerateResponseUseCase with mocked dependencies."""
    return GenerateResponseUseCase(
        text_generator=mock_text_generator,
        prompt_builder=mock_prompt_builder,
        event_publisher=mock_event_publisher,
        conversation=conversation,
        character=CharacterProfile(),
    )


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
    """A reply while playing sees the activity and may take the viewer's request."""

    def _request(self, session, message="ベッド作って！"):
        return GenerateResponseRequest(user_name="neko", message=message, session=session)

    @pytest.mark.asyncio
    async def test_reply_sees_the_activity_and_the_goals_on_offer(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        """Test the reply is built from the session's activity with the goals valid now."""
        mock_text_generator.generate_json.return_value = {
            "reply": "木を集めてるよ",
            "change_goal": False,
        }
        session = _playing()

        response = await use_case.execute(self._request(session, "今なにしてるの？"))

        assert response.text == "木を集めてるよ"
        assert response.goal is None
        kwargs = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs
        assert kwargs["context"].activity.goal.spec.describe() == "have(log, 3)"
        assert GoalPredicate.PLACED in kwargs["predicates"]
        assert session.request is None

    @pytest.mark.asyncio
    async def test_promise_leaves_a_request_for_the_step_loop(self, use_case, mock_text_generator):
        """Test a reply that takes the request leaves it in the session (the goal is unchanged)."""
        mock_text_generator.generate_json.return_value = {
            "reply": "いいよ、ベッド作るね！",
            "change_goal": True,
            "predicate": "placed",
            "item": "bed",
            "reason": "nekoさんに頼まれた",
        }
        session = _playing()

        response = await use_case.execute(self._request(session))

        assert response.goal.spec == BED
        assert session.request.goal.spec == BED
        assert session.request.goal.requested_by == "neko"
        assert session.request.message == "ベッド作って！"
        assert session.goal.spec.describe() == "have(log, 3)"  # only the step loop changes it

    @pytest.mark.asyncio
    async def test_unusable_goal_is_regenerated_never_promised(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        """Test a promise with a goal not on offer is sent back; the retry's reply is used."""
        mock_text_generator.generate_json.side_effect = [
            {"reply": "外で戦うね！", "change_goal": True, "predicate": "cleared", "reason": ""},
            {"reply": "夜は危ないから朝まで待ってね", "change_goal": False},
        ]
        session = _playing("night")

        response = await use_case.execute(self._request(session, "外で戦って"))

        assert response.text == "夜は危ないから朝まで待ってね"
        assert session.request is None
        second = mock_prompt_builder.build_chat_response_prompt.call_args_list[1].kwargs
        assert "not one of the goals offered" in second["previous_error"]

    @pytest.mark.asyncio
    async def test_no_reply_after_repeated_unusable_goals(self, use_case, mock_text_generator):
        """Test nothing is said when every generation promises an unusable goal."""
        mock_text_generator.generate_json.return_value = {
            "reply": "やるね！",
            "change_goal": True,
            "predicate": "have",
            "reason": "",
        }
        session = _playing()

        response = await use_case.execute(self._request(session))

        assert not response.success
        assert session.request is None

    @pytest.mark.asyncio
    async def test_newer_request_replaces_a_pending_one_and_says_so(
        self, use_case, mock_text_generator, mock_event_publisher
    ):
        """Test a pending request that gives way is published (never dropped silently)."""
        bed = {
            "reply": "ベッド作るね",
            "change_goal": True,
            "predicate": "placed",
            "item": "bed",
            "reason": "",
        }
        explore = {
            "reply": "探検するね",
            "change_goal": True,
            "predicate": "explored",
            "distance": 30,
        }
        mock_text_generator.generate_json.side_effect = [bed, explore]
        session = _playing()

        await use_case.execute(self._request(session))
        await use_case.execute(
            GenerateResponseRequest(user_name="tori", message="探検して", session=session)
        )

        replaced = [
            c.args[0]
            for c in mock_event_publisher.publish.call_args_list
            if isinstance(c.args[0], ViewerRequestReplacedEvent)
        ]
        assert [(e.goal, e.user_name, e.replaced_by) for e in replaced] == [
            ("placed(bed, home)", "neko", "tori")
        ]
        assert session.request.user_name == "tori"
