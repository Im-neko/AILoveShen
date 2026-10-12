"""GenerateCommentaryUseCase のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.ports.output.text_generator import CACHE_BREAK
from ailoveshen.application.dto.llm_dto import GenerateCommentaryRequest
from ailoveshen.application.use_cases.generate_commentary import GenerateCommentaryUseCase
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import CommentaryGeneratedEvent
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import (
    Activity,
    CharacterProfile,
    EmotionState,
    EmotionType,
    Goal,
    GoalPredicate,
    GoalSpec,
    MessageType,
)

ACTIVITY = Activity(goal=Goal(GoalSpec(GoalPredicate.BUILT), reason="家を建てる"))


@pytest.fixture
def mock_text_generator():
    """テキスト生成のモック。"""
    generator = AsyncMock()
    generator.generate.return_value = "わー、洞窟を見つけたよ！"
    return generator


@pytest.fixture
def mock_prompt_builder():
    """プロンプトビルダーのモック（同期メソッド）。"""
    builder = Mock()
    builder.build_system_prompt.return_value = "System prompt"
    builder.build_commentary_prompt.return_value = "Commentary prompt"
    return builder


@pytest.fixture
def mock_event_publisher():
    """イベント発行のモック。"""
    return AsyncMock()


@pytest.fixture
def conversation():
    """会話履歴。"""
    return Conversation()


@pytest.fixture
def use_case(mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation):
    """依存をモックにした GenerateCommentaryUseCase。"""
    return GenerateCommentaryUseCase(
        text_generator=mock_text_generator,
        prompt_builder=mock_prompt_builder,
        event_publisher=mock_event_publisher,
        conversation=conversation,
        character=CharacterProfile(),
    )


class TestGenerateCommentaryUseCase:
    """GenerateCommentaryUseCase のテスト。"""

    @pytest.mark.asyncio
    async def test_execute_success(self, use_case, mock_text_generator, mock_prompt_builder):
        """実況が生成される。"""
        response = await use_case.execute(GenerateCommentaryRequest())

        assert response.success is True
        assert response.text == "わー、洞窟を見つけたよ！"
        mock_text_generator.generate.assert_called_once_with(
            prompt=f"System prompt{CACHE_BREAK}Commentary prompt",  # 性格と話し方は本文の先頭に置く（設計書 175）
            purpose="commentary",
        )
        assert mock_prompt_builder.build_system_prompt.call_args.args[1] == "commentary"

    @pytest.mark.asyncio
    async def test_execute_builds_context(self, use_case, mock_prompt_builder, conversation):
        """プロンプトビルダーに渡すコンテキスト。"""
        conversation.add_viewer_message("がんばれ", "neko")
        happy = EmotionState(EmotionType.HAPPY, 0.8)
        request = GenerateCommentaryRequest(
            emotion_state=happy,
            recent_events=["e1", "e2", "e3", "e4"],
            activity=ACTIVITY,
        )

        await use_case.execute(request)

        context = mock_prompt_builder.build_commentary_prompt.call_args.args[0]
        assert context.emotion_state == happy
        assert context.activity is ACTIVITY
        assert context.recent_events == ("e2", "e3", "e4")  # 最新の 3 件
        assert [m.content for m in context.recent_messages] == ["がんばれ"]

    @pytest.mark.asyncio
    async def test_execute_records_commentary(self, use_case, conversation):
        """生成した実況が会話に加わる。"""
        await use_case.execute(GenerateCommentaryRequest())

        last = conversation.recent_messages(1)[0]
        assert last.content == "わー、洞窟を見つけたよ！"
        assert last.message_type == MessageType.COMMENTARY

    @pytest.mark.asyncio
    async def test_execute_publishes_event(self, use_case, mock_event_publisher):
        """CommentaryGeneratedEvent が発行される。"""
        await use_case.execute(GenerateCommentaryRequest())

        event = mock_event_publisher.publish.call_args.args[0]
        assert isinstance(event, CommentaryGeneratedEvent)
        assert event.text == "わー、洞窟を見つけたよ！"

    @pytest.mark.asyncio
    async def test_execute_empty_text(
        self, use_case, mock_text_generator, mock_event_publisher, conversation
    ):
        """生成が空なら記録も発行もしない。"""
        mock_text_generator.generate.return_value = ""

        response = await use_case.execute(GenerateCommentaryRequest())

        assert response.success is False
        assert response.text == ""
        assert response.error is None
        assert len(conversation) == 0
        mock_event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_generation_error(self, use_case, mock_text_generator, conversation):
        """生成に失敗したら、空のテキストでエラーの応答を返す。"""
        mock_text_generator.generate.side_effect = TextGenerationError("API down")

        response = await use_case.execute(GenerateCommentaryRequest())

        assert response.success is False
        assert response.text == ""
        assert "API down" in response.error
        assert len(conversation) == 0


def test_a_planning_note_line_is_dropped_from_the_commentary():
    """実測（2026-10-10 19:58）: 「実況の状況: …述べる。」の行まで読み上げられ、視聴者に指摘された。"""
    from ailoveshen.application.use_cases.generate_commentary import without_preamble

    leaked = (
        "実況の状況: 家の残り4ブロックを仕上げるため。直前の発言を繰り返さず、理由を述べる。\n\n"
        "よし、工房はいったんストップして、家の仕上げに戻るね！"
    )
    assert without_preamble(leaked) == "よし、工房はいったんストップして、家の仕上げに戻るね！"
    assert without_preamble("今は丸石を掘ってるよ！") == "今は丸石を掘ってるよ！"
    assert without_preamble("") == ""
