"""Tests for PromptTemplateBuilder adapter."""

from ailoveshen.domain.value_objects import (
    CharacterProfile,
    ConversationMessage,
    EmotionState,
    EmotionType,
    GenerationContext,
    MessageType,
)
from ailoveshen.infrastructure.adapters.prompts.prompt_template_builder import (
    PromptTemplateBuilder,
)


class TestPromptTemplateBuilder:
    """Tests for PromptTemplateBuilder."""

    def test_system_prompt_includes_character(self):
        """Test the system prompt reflects the character profile."""
        character = CharacterProfile(
            name="シェン",
            first_person="ボク",
            sentence_endings=("のだ",),
            personality_traits=("元気",),
        )

        prompt = PromptTemplateBuilder().build_system_prompt(character)

        assert "「シェン」" in prompt
        assert "一人称は「ボク」" in prompt
        assert "「のだ」" in prompt
        assert "元気" in prompt

    def test_commentary_prompt_with_context(self):
        """Test commentary prompt includes state, events, history and emotion."""
        context = GenerationContext(
            emotion_state=EmotionState(EmotionType.HAPPY, 0.8),
            game_state_summary="体力: 20/20",
            recent_events=("ゾンビを倒した",),
            recent_messages=(
                ConversationMessage.from_viewer("がんばれ", "neko"),
                ConversationMessage.from_streamer("ありがとう！", MessageType.RESPONSE),
            ),
        )

        prompt = PromptTemplateBuilder().build_commentary_prompt(context)

        assert "体力: 20/20" in prompt
        assert "- ゾンビを倒した" in prompt
        assert "neko: がんばれ" in prompt
        assert "あなた: ありがとう！" in prompt
        assert "happy（強度: 0.8）" in prompt

    def test_commentary_prompt_without_information(self):
        """Test placeholders are used when context is empty."""
        prompt = PromptTemplateBuilder().build_commentary_prompt(GenerationContext())

        assert "## 現在のゲーム状況\n不明" in prompt
        assert "## 最近のイベント\n特になし" in prompt
        assert "## 最近の会話\n特になし" in prompt

    def test_chat_response_prompt(self):
        """Test chat response prompt includes the viewer's comment."""
        prompt = PromptTemplateBuilder().build_chat_response_prompt(
            user_name="neko",
            message="がんばれ",
            context=GenerationContext(),
        )

        assert "ユーザー名: neko" in prompt
        assert "コメント: がんばれ" in prompt
        assert "neutral（強度: 0.5）" in prompt
