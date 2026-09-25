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
    MidGoal,
    Mission,
)
from ailoveshen.infrastructure.adapters.prompts.prompt_template_builder import (
    PromptTemplateBuilder,
)

BUILDING = Activity(
    mission=Mission("生き延びながら家を建て、街にしていく"),
    mid_goals=(
        MidGoal("m1", "自分の家を作る", (GoalSpec(GoalPredicate.BUILT),)),
        MidGoal(
            "m3",
            "ベッドで寝る",
            (GoalSpec(GoalPredicate.PLACED, item="bed", where="home"),),
            requested_by="neko",
        ),
    ),
    goal=Goal(GoalSpec(GoalPredicate.BUILT), reason="日暮れまでに家を完成させる", mid_goal_id="m1"),
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

        assert (
            "今の小目標: built()（「自分の家を作る」のため）: 日暮れまでに家を完成させる" in prompt
        )
        assert "house blocks placed 30/70" in prompt
        assert "体力 20.0/20" in prompt
        assert "[m1]" not in prompt  # ids are for the goal decision, not to be read out
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

        assert "- 大目標: 生き延びながら家を建て、街にしていく" in prompt
        assert "1. 自分の家を作る [取り組み中] 完了条件: built()" in prompt
        assert "2. ベッドで寝る（nekoさんの頼み） 完了条件: placed(bed, home)" in prompt
        assert "「今していること」のとおりに答える" in prompt
        # Not taking requests, the reply is text only
        assert "視聴者の頼みについて" not in prompt
        assert "返答テキストのみを出力してください。" in prompt

    def test_chat_response_prompt_takes_requests_as_mid_goals(self):
        """Test a reply that may accept a request lists the conditions and the rules."""
        prompt = PromptTemplateBuilder().build_chat_response_prompt(
            user_name="neko",
            message="ベッド作って",
            context=GenerationContext(activity=BUILDING),
            takes_requests=True,
            previous_error="neko already has a request in the list",
        )

        assert "placed(item=bed)" in prompt and "have(item, count)" in prompt and "built" in prompt
        assert "explored(distance)" not in prompt and "cleared:" not in prompt
        assert "今の小目標は中断しない" in prompt
        assert "同じ人の頼みは同時に1つまで" in prompt
        assert "大目標と今の目標は変えない" in prompt
        assert "前回の返答の頼みは受けられなかった: neko already has a request" in prompt
        assert "decline" in prompt
