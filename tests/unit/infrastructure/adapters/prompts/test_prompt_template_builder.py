"""PromptTemplateBuilder アダプタのテスト。"""

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
    """PromptTemplateBuilder のテスト。"""

    def test_system_prompt_includes_character(self):
        """システムプロンプトはキャラクタープロフィールを反映する。"""
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
        """実況のプロンプトは状態、出来事、履歴、感情を含む。"""
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
        assert "[m1]" not in prompt  # id は小目標の決定のためのもので、読み上げるものではない
        assert "- ゾンビを倒した" in prompt
        assert "nekoさん: がんばれ" in prompt
        assert "あなた: ありがとう！" in prompt
        assert "happy（強度: 0.8）" in prompt

    def test_commentary_prompt_without_information(self):
        """コンテキストが空ならプレースホルダを使う。"""
        prompt = PromptTemplateBuilder().build_commentary_prompt(GenerationContext())

        assert "## 今していること\nゲームはしていない" in prompt
        assert "## 最近のイベント（最後のものが今起きたこと）\n特になし" in prompt
        assert "## 最近の会話\n特になし" in prompt

    def test_chat_response_prompt(self):
        """返答のプロンプトは視聴者のコメントを含む。"""
        prompt = PromptTemplateBuilder().build_chat_response_prompt(
            user_name="neko",
            message="がんばれ",
            context=GenerationContext(),
        )

        assert "ユーザー名: neko" in prompt
        assert "コメント: がんばれ" in prompt
        assert "neutral（強度: 0.5）" in prompt

    def test_chat_response_prompt_sees_the_real_goal(self):
        """返答は配信者が実際にしていることを見る（作り話のことはしない）。"""
        prompt = PromptTemplateBuilder().build_chat_response_prompt(
            user_name="neko",
            message="今なにしてるの？",
            context=GenerationContext(activity=BUILDING),
        )

        assert "- 大目標: 生き延びながら家を建て、街にしていく" in prompt
        assert "1. 自分の家を作る [取り組み中] 完了条件: built()" in prompt
        assert "2. ベッドで寝る（nekoさんの頼み） 完了条件: placed(bed, home)" in prompt
        # 決まりはシステム指示の側（26 §4）。頼みを受けないときは、返答はテキストだけ
        system = PromptTemplateBuilder().build_system_prompt(CharacterProfile(), "reply")
        assert "「今していること」のとおりに答える" in system
        assert "視聴者の頼みについて" not in system
        assert "返答テキストのみを出力してください。" in system
        # コメントは本文の最後（状態の部分が先頭から同じになるように）
        assert prompt.rstrip().endswith("コメント: 今なにしてるの？")

    def test_chat_response_prompt_takes_requests_as_mid_goals(self):
        """頼みを受けられる返答には、条件と規則を並べる。"""
        prompt = PromptTemplateBuilder().build_chat_response_prompt(
            user_name="neko",
            message="ベッド作って",
            context=GenerationContext(activity=BUILDING),
            takes_requests=True,
            previous_error="neko already has a request in the list",
        )

        system = PromptTemplateBuilder().build_system_prompt(CharacterProfile(), "reply_requests")
        assert "placed(crafting_table)" in system and "have(item, count)" in system and "built" in system
        assert "explored(distance)" not in system and "cleared:" not in system
        assert "今の小目標は中断しない" in system
        assert "同じ人の頼みは同時に1つまで" in system
        assert "大目標と今の目標は変えない" in system
        # やり方についての頼みには、配信者が実際にできることから答える
        assert "## 自分でできること（これ以外はできない）" in system
        assert "松明を持っていれば置いて湧き潰しする" in system
        assert "できない約束はしない" in system
        assert "頼んだ人の名前は入れない" in system
        assert "JSON" in system
        assert "頼みは受けられなかった: neko already has a request" in prompt
        assert "decline" in prompt
        assert "視聴者の頼みについて" not in prompt

    def test_system_prompts_do_not_change_with_the_state(self):
        """用途ごとのシステム指示は状態を含まない（暗黙のキャッシュが効く）。"""
        builder = PromptTemplateBuilder()
        for purpose in ("commentary", "reply", "reply_requests"):
            system = builder.build_system_prompt(CharacterProfile(), purpose)
            assert "$" not in system
            assert system.startswith(builder.build_system_prompt(CharacterProfile()))
        assert "実況テキストのみ" in builder.build_system_prompt(CharacterProfile(), "commentary")
