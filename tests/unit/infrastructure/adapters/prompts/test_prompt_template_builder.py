"""Tests for PromptTemplateBuilder adapter."""

from ailoveshen.domain.value_objects import (
    Activity,
    Candidate,
    CharacterProfile,
    ConversationMessage,
    EmotionState,
    EmotionType,
    GameObservation,
    GenerationContext,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    MessageType,
)
from ailoveshen.infrastructure.adapters.prompts.prompt_template_builder import (
    PromptTemplateBuilder,
)

BUILDING = Activity(
    goal=Goal(GoalSpec(GoalPredicate.BUILT), reason="日暮れまでに家を完成させる"),
    observation=GameObservation(
        state={"time": {"phase": "day", "time_of_day": 6000}},
        candidates=(Candidate("wait", {"verb": "wait"}),),
        health=20.0,
        food=20,
        goal=GoalStatus(met=False, remaining=40, lines=("house blocks placed 30/70",)),
    ),
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
            activity=BUILDING,
            recent_events=("ゾンビを倒した",),
            recent_messages=(
                ConversationMessage.from_viewer("がんばれ", "neko"),
                ConversationMessage.from_streamer("ありがとう！", MessageType.RESPONSE),
            ),
        )

        prompt = PromptTemplateBuilder().build_commentary_prompt(context)

        assert "今の目標: built(): 日暮れまでに家を完成させる" in prompt
        assert "house blocks placed 30/70" in prompt
        assert "体力 20.0/20" in prompt
        assert "- ゾンビを倒した" in prompt
        assert "nekoさん: がんばれ" in prompt
        assert "あなた: ありがとう！" in prompt
        assert "happy（強度: 0.8）" in prompt

    def test_commentary_prompt_without_information(self):
        """Test placeholders are used when context is empty."""
        prompt = PromptTemplateBuilder().build_commentary_prompt(GenerationContext())

        assert "## 今していること\nゲームはしていない" in prompt
        assert "## 最近のイベント（最後のものが今起きたこと）\n特になし" in prompt
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

    def test_chat_response_prompt_sees_the_real_goal(self):
        """Test the reply sees what the streamer is actually doing (no made-up activity)."""
        prompt = PromptTemplateBuilder().build_chat_response_prompt(
            user_name="neko",
            message="今なにしてるの？",
            context=GenerationContext(activity=BUILDING),
        )

        assert "今の目標: built(): 日暮れまでに家を完成させる" in prompt
        assert "「今していること」のとおりに答える" in prompt
        # Without goals to offer, the reply is text only
        assert "行動の頼みについて" not in prompt
        assert "返答テキストのみを出力してください。" in prompt

    def test_chat_response_prompt_offers_goals_for_requests(self):
        """Test a reply that may take a request lists the goals and the promise rule."""
        prompt = PromptTemplateBuilder().build_chat_response_prompt(
            user_name="neko",
            message="ベッド作って",
            context=GenerationContext(activity=BUILDING),
            predicates=[GoalPredicate.PLACED, GoalPredicate.HAVE],
            previous_error="have needs an item",
        )

        assert "placed(item=bed)" in prompt and "have(item, count)" in prompt
        assert "cleared" not in prompt
        assert "「やるね」と言うなら必ず目標を出す" in prompt
        assert "先の予定を約束しない" in prompt
        assert "やめることを返答で言う" in prompt
        assert "前回の返答の目標は使えなかった: have needs an item" in prompt
        assert "change_goal" in prompt
