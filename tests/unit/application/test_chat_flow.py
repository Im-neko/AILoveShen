"""チャットの返事が見る、会話の流れと時系列（設計書 81）。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    ConversationMessage,
    GenerationContext,
    MessageRole,
    MessageType,
)
from ailoveshen.infrastructure.adapters.prompts.prompt_template_builder import (
    PromptTemplateBuilder,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import (
    NO_INFORMATION,
    format_age,
    format_messages,
)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def _at(seconds_ago: float) -> datetime:
    return NOW - timedelta(seconds=seconds_ago)


def _viewer(text: str, name: str, ago: float) -> ConversationMessage:
    return ConversationMessage.from_viewer(text, name, received_at=_at(ago))


def _streamer(text: str, kind: MessageType, ago: float, to: str = "") -> ConversationMessage:
    return ConversationMessage(
        role=MessageRole.STREAMER,
        message_type=kind,
        content=text,
        timestamp=_at(ago),
        addressed_to=to,
    )


class TestConversationOrder:
    def test_a_comment_that_arrived_during_the_reply_sits_before_the_commentary_said_meanwhile(self):
        conversation = Conversation()
        conversation.add_streamer_message("洞窟だ", MessageType.COMMENTARY)
        arrived = datetime.now(timezone.utc)
        conversation.add_streamer_message("木を切るよ", MessageType.COMMENTARY)
        conversation.add_viewer_message("畑は？", "neko", received_at=arrived)
        assert [m.content for m in conversation] == ["洞窟だ", "畑は？", "木を切るよ"]

    def test_the_flow_keeps_the_exchange_with_viewers_and_the_latest_commentary(self):
        conversation = Conversation()
        for i in range(6):
            conversation.add_streamer_message(f"実況{i}", MessageType.COMMENTARY)
        conversation.add_viewer_message("こんにちは", "neko")
        conversation.add_streamer_message("こんにちは！", MessageType.RESPONSE, addressed_to="neko")
        flow = conversation.recent_chat_flow(chat_limit=10, commentary_limit=2)
        assert [m.content for m in flow] == ["実況4", "実況5", "こんにちは", "こんにちは！"]
        assert flow[-1].addressed_to == "neko"


class TestFormat:
    def test_ages(self):
        assert format_age(4) == "4秒前"
        assert format_age(90) == "1分前"
        assert format_age(7300) == "2時間前"
        assert format_age(-5) == "0秒前"

    def test_with_now_each_line_has_its_age_and_whom_it_answered(self):
        text = format_messages(
            (
                _viewer("畑作って", "neko", 120),
                _streamer("いいよ", MessageType.RESPONSE, 100, to="neko"),
                _streamer("木を切るね", MessageType.COMMENTARY, 30),
            ),
            NOW,
        )
        assert text.splitlines() == [
            "[2分前] nekoさん: 畑作って",
            "[1分前] あなた（nekoさんへの返事）: いいよ",
            "[30秒前] あなた（実況）: 木を切るね",
        ]

    def test_without_now_it_is_the_plain_form_and_empty_is_nothing(self):
        assert format_messages((_viewer("やあ", "neko", 5),)) == "nekoさん: やあ"
        assert format_messages((), NOW) == NO_INFORMATION


class TestPrompt:
    def _prompt_for(self, message: str) -> str:
        return PromptTemplateBuilder().build_chat_response_prompt(
            "neko", message, GenerationContext(now=NOW)
        )

    def _prompt(self, **context) -> str:
        return PromptTemplateBuilder().build_chat_response_prompt(
            "neko", "それやって", GenerationContext(now=NOW, **context)
        )

    def test_the_comment_says_how_long_ago_it_arrived_when_a_while_has_passed(self):
        assert "コメント（42秒前に届いた）: それやって" in self._prompt(comment_received_at=_at(42))
        assert "コメント: それやって" in self._prompt(comment_received_at=_at(1))
        assert "コメント: それやって" in self._prompt()

    def test_comments_still_waiting_are_shown_after_the_conversation(self):
        prompt = self._prompt(waiting_comments=(_viewer("次の人", "ako", 8),))
        assert "## このあとに届いた、まだ返事していないコメント\n[8秒前] akoさん: 次の人" in prompt
        assert "このあとに届いた" not in self._prompt()

    def test_the_rules_tell_it_to_read_the_flow_in_order(self):
        system = PromptTemplateBuilder().build_system_prompt(CharacterProfile(), "reply")
        assert "## 会話の流れと時系列" in system
        assert "## 会話の流れと時系列" in PromptTemplateBuilder().build_system_prompt(
            CharacterProfile(), "reply_requests"
        )

    def test_a_foreign_comment_gets_the_language_note_right_after_it(self):
        """システム指示の決まりだけでは日本語で返された（2026-10-10）: コメントのすぐ後ろにも書く。"""
        foreign = self._prompt_for("How are you doing?")
        assert foreign.index("コメント: How are you doing?") < foreign.index("日本語ではない")
        assert "日本語ではない" not in self._prompt_for("元気？")

    def test_the_reply_rules_say_the_state_now_beats_old_plans(self):
        system = PromptTemplateBuilder().build_system_prompt(CharacterProfile(), "reply")
        assert "過去の会話や自分の\n  メモにある予定は古いことがある" in system

    def test_commentary_does_not_pick_up_a_comment_that_was_already_answered(self):
        system = PromptTemplateBuilder().build_system_prompt(CharacterProfile(), "commentary")
        assert "返事済み" in system


@pytest.mark.asyncio
async def test_the_reply_is_recorded_as_an_answer_to_the_viewer_and_the_comment_keeps_its_arrival_time():
    conversation = Conversation()
    conversation.add_streamer_message("洞窟だ", MessageType.COMMENTARY)
    arrived = datetime.now(timezone.utc)
    generator = AsyncMock()
    generator.generate.return_value = "いいよ！"
    builder = Mock()
    builder.build_system_prompt.return_value = "system"
    prompts = []
    builder.build_chat_response_prompt.side_effect = lambda **kw: prompts.append(kw) or "prompt"
    use_case = GenerateResponseUseCase(
        text_generator=generator,
        prompt_builder=builder,
        event_publisher=AsyncMock(),
        conversation=conversation,
        character=CharacterProfile(),
    )
    # 返事を書く間に、実況が 1 つ入る
    conversation.add_streamer_message("木を切るよ", MessageType.COMMENTARY)
    waiting = (_viewer("次", "ako", 1),)
    response = await use_case.execute(
        GenerateResponseRequest(
            user_name="neko", message="畑は？", received_at=arrived, waiting=waiting
        )
    )
    assert response.text == "いいよ！"
    contents = [(m.role, m.content, m.addressed_to) for m in conversation]
    assert contents == [
        (MessageRole.STREAMER, "洞窟だ", ""),
        (MessageRole.VIEWER, "畑は？", ""),
        (MessageRole.STREAMER, "木を切るよ", ""),
        (MessageRole.STREAMER, "いいよ！", "neko"),
    ]
    context = prompts[0]["context"]
    assert context.comment_received_at == arrived
    assert context.waiting_comments == waiting
    assert context.now is not None


def test_the_home_line_gives_the_door_so_the_model_does_not_invent_where_home_is():
    from ailoveshen.domain.value_objects import Candidate, GameObservation, GoalStatus
    from ailoveshen.infrastructure.adapters.prompts.stream_context import _format_home

    def home_line(door):
        return _format_home(
            GameObservation(
                state={"home": {"name": "小屋", "door": door}},
                candidates=(Candidate("wait", {"verb": "wait"}),),
                health=20.0,
                food=20,
                goal=GoalStatus(met=False, remaining=1),
                time_phase="day",
                has_plan=True,
                house_complete=True,
                has_home=True,
            )
        )

    assert home_line({"x": 60, "y": 77, "z": -328}).endswith("、玄関 x=60 y=77 z=-328")
    assert "玄関" not in home_line({})
