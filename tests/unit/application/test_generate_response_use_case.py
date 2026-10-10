"""GenerateResponseUseCase のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase, unjudged
from ailoveshen.application.use_cases.goal_vocabulary import ObjectiveProposal, reply_schema
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.domain.entities import Conversation, ObjectivePlan, PlaySession
from ailoveshen.domain.events import ChatResponseGeneratedEvent, ObjectiveAddedEvent
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
    """家があり、剣（m1）、次に食料（m2）に取り組んでいるセッション。"""
    plan = ObjectivePlan(mission=Mission("生き延びながら街にしていく"))
    plan.add("剣を持つ", (SWORD,))
    plan.add("食料を蓄える", (GoalSpec(GoalPredicate.HAVE, item="food", count=8),))
    session = PlaySession(blueprint=HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2), plan=plan)
    session.set_goal(
        Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=3), "剣の材料", objective_id="m1"),
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
    """テキスト生成のモック。"""
    generator = AsyncMock()

    async def generate(prompt="", system_instruction="", purpose=""):
        # 結果に合わせた書き直し（reply_final）は下書きをそのまま返す（書き直しの中身は別のテストで見る）
        return prompt if purpose == "reply_final" else "nekoさん、ありがとう！"

    generator.generate.side_effect = generate
    return generator


@pytest.fixture
def mock_prompt_builder():
    """プロンプトビルダーのモック（同期メソッド）。"""
    builder = Mock()
    builder.build_system_prompt.return_value = "System prompt"
    builder.build_chat_response_prompt.return_value = "Response prompt"
    builder.build_final_reply_prompt.side_effect = lambda **kwargs: kwargs["draft"]
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
def bridge():
    """ブリッジのモック。どの条件も判定できる（どれも満たしていない）。"""
    b = AsyncMock()

    async def check(specs):
        return [ConditionStatus(s, False) for s in specs]

    b.check.side_effect = check
    return b


@pytest.fixture
def store():
    """ミッションストアのモック。"""
    return Mock()


@pytest.fixture
def use_case(
    mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation, bridge, store
):
    """依存をモックにした GenerateResponseUseCase。"""
    return GenerateResponseUseCase(
        text_generator=mock_text_generator,
        prompt_builder=mock_prompt_builder,
        event_publisher=mock_event_publisher,
        conversation=conversation,
        character=CharacterProfile(),
        objectives=ObjectiveKeeper(bridge=bridge, event_publisher=mock_event_publisher, store=store),
    )


def _added(events) -> list[ObjectiveAddedEvent]:
    return [
        c.args[0] for c in events.publish.call_args_list if isinstance(c.args[0], ObjectiveAddedEvent)
    ]


def _request(message: str = "がんばれ") -> GenerateResponseRequest:
    return GenerateResponseRequest(user_name="neko", message=message, user_id="42")


class TestGenerateResponseUseCase:
    """GenerateResponseUseCase のテスト。"""

    @pytest.mark.asyncio
    async def test_execute_success(self, use_case, mock_text_generator, mock_prompt_builder):
        """コメントへの返答が生成される。"""
        response = await use_case.execute(_request())

        assert response.success is True
        assert response.text == "nekoさん、ありがとう！"
        assert response.original_message == "がんばれ"
        assert response.user_name == "neko"
        mock_text_generator.generate.assert_called_once_with(
            prompt="Response prompt",
            system_instruction="System prompt",
            purpose="reply",
        )
        assert mock_prompt_builder.build_system_prompt.call_args.args[1] == "reply"

    @pytest.mark.asyncio
    async def test_execute_records_chat_and_reply(self, use_case, conversation):
        """視聴者のコメントと返答が、この順で記録される。"""
        await use_case.execute(_request())

        chat, reply = conversation.recent_messages()
        assert chat.role == MessageRole.VIEWER
        assert chat.content == "がんばれ"
        assert chat.speaker_id == "42"
        assert reply.role == MessageRole.STREAMER
        assert reply.message_type == MessageType.RESPONSE

    @pytest.mark.asyncio
    async def test_the_chat_is_not_in_the_conversation_while_the_reply_is_written(
        self, use_case, mock_text_generator, conversation
    ):
        """返事を書く間に実況が書かれても、返事のないコメントを見て先に答えない（逆を言った）。"""
        seen: list[list[str]] = []

        async def generate(**_kwargs):
            seen.append([m.content for m in conversation.recent_messages()])
            return "nekoさん、ありがとう！"

        mock_text_generator.generate = AsyncMock(side_effect=generate)

        await use_case.execute(_request())

        assert seen == [[]]
        assert [m.content for m in conversation.recent_messages()] == ["がんばれ", "nekoさん、ありがとう！"]

    @pytest.mark.asyncio
    async def test_the_chat_is_recorded_even_when_the_reply_fails(
        self, use_case, mock_text_generator, conversation
    ):
        mock_text_generator.generate = AsyncMock(side_effect=TextGenerationError("x"))

        response = await use_case.execute(_request())

        assert response.success is False
        assert [m.content for m in conversation.recent_messages()] == ["がんばれ"]

    @pytest.mark.asyncio
    async def test_history_excludes_current_chat(self, use_case, mock_prompt_builder, conversation):
        """返答するコメントが履歴に重複して入らない。"""
        conversation.add_streamer_message("洞窟だ！", MessageType.COMMENTARY)

        await use_case.execute(_request())

        kwargs = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs
        assert kwargs["user_name"] == "neko"
        assert kwargs["message"] == "がんばれ"
        assert [m.content for m in kwargs["context"].recent_messages] == ["洞窟だ！"]

    @pytest.mark.asyncio
    async def test_execute_publishes_event(self, use_case, mock_event_publisher):
        """ChatResponseGeneratedEvent が発行される。"""
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
        """生成が空なら、コメントは残し、返答は記録しない。"""
        mock_text_generator.generate.side_effect = None
        mock_text_generator.generate.return_value = ""

        response = await use_case.execute(_request())

        assert response.success is False
        assert len(conversation) == 1  # 視聴者のコメントだけ
        mock_event_publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_generation_error(self, use_case, mock_text_generator):
        """生成に失敗したら、空のテキストでエラーの応答を返す。"""
        mock_text_generator.generate.side_effect = TextGenerationError("API down")

        response = await use_case.execute(_request())

        assert response.success is False
        assert response.text == ""
        assert "API down" in response.error
        assert response.user_name == "neko"


class TestReplyWhilePlaying:
    """プレイ中の返答は「今していること」を見て、頼みを大目標として受けられる。"""

    def _request(self, session, message="ベッド作って！", user_name="neko"):
        return GenerateResponseRequest(user_name=user_name, message=message, session=session)

    @pytest.mark.asyncio
    async def test_reply_sees_the_activity(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        """返答はセッションの「今していること」から作り、頼みを受けられる。"""
        mock_text_generator.generate_json.return_value = {
            "reply": "剣のために木を集めてるよ",
            "request": "none",
        }
        session = _playing()

        response = await use_case.execute(self._request(session, "今なにしてるの？"))

        assert response.text == "剣のために木を集めてるよ"
        assert response.objective is None
        kwargs = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs
        assert kwargs["context"].activity.goal.spec.describe() == "have(log, 3)"
        assert kwargs["takes_requests"] is True
        assert len(session.plan.pending) == 2

    @pytest.mark.asyncio
    async def test_a_japanese_reply_to_a_foreign_comment_is_sent_back_once(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        """実測（2026-10-10 19:45）: 英語の「How are you doing?」に日本語で返した。その言語で書き直させる。"""
        mock_text_generator.generate_json.side_effect = [
            {"reply": "ねこかもさん、こんにちは！元気だよ！", "request": "none"},
            {"reply": "I'm great, neko-san! I'm digging stone right now!", "request": "none"},
        ]

        response = await use_case.execute(
            self._request(_playing(), message="How are you doing?")
        )

        assert response.text == "I'm great, neko-san! I'm digging stone right now!"
        error = mock_prompt_builder.build_chat_response_prompt.call_args_list[1].kwargs["previous_error"]
        assert "not in Japanese" in error

    @pytest.mark.asyncio
    async def test_a_japanese_comment_is_answered_in_japanese(
        self, use_case, mock_text_generator
    ):
        mock_text_generator.generate_json.return_value = {
            "reply": "ありがとう！がんばるね！",
            "request": "none",
        }

        response = await use_case.execute(self._request(_playing(), message="gg lol"))

        assert response.text == "ありがとう！がんばるね！"
        assert mock_text_generator.generate_json.await_count == 1

    @pytest.mark.asyncio
    async def test_accepted_request_joins_the_objectives_behind_the_current(
        self, use_case, mock_text_generator, mock_event_publisher, store
    ):
        """受けた頼みは（今の大目標の後ろに）大目標になり、小目標は続く。"""
        mock_text_generator.generate_json.return_value = BED_REQUEST
        session = _playing()

        response = await use_case.execute(self._request(session))

        assert response.objective.conditions == (BED,)
        assert [(g.title, g.requested_by) for g in session.plan.pending] == [
            ("剣を持つ", None),
            ("ベッドで寝る", "neko"),
            ("食料を蓄える", None),
        ]
        assert session.goal.spec.describe() == "have(log, 3)"  # 中断しない
        added = _added(mock_event_publisher)[0]
        assert (added.title, added.requested_by, added.position) == ("ベッドで寝る", "neko", 2)
        store.save.assert_called_with(session.plan)

    @pytest.mark.asyncio
    async def test_while_waiting_the_night_a_request_can_be_done_now(
        self, use_case, mock_text_generator
    ):
        """夜に家の中で待っている間は、中でできる頼みを今やる（先頭に入れ、待ちを区切る）。"""
        chest = {
            "reply": "いいね、待ってる間にチェスト作っちゃおう！",
            "request": "accept",
            "title": "チェストを作る",
            "conditions": [{"predicate": "have", "item": "chest", "count": 1}],
            "reason": "待ち時間を使える",
            "when": "now",
        }
        mock_text_generator.generate_json.return_value = chest
        session = _playing("night")
        session.set_goal(Goal(GoalSpec(GoalPredicate.THROUGH_NIGHT), "夜を越す"), "night")

        response = await use_case.execute(self._request(session, "チェスト作ったら？"))

        assert session.plan.current.title == "チェストを作る"
        assert response.objective.requested_by == "neko"
        assert "now" in session.rethink_reason and "チェストを作る" in session.rethink_reason

    @pytest.mark.asyncio
    async def test_now_while_working_cuts_the_small_goal_when_gemini_judges_it_better(
        self, use_case, mock_text_generator
    ):
        """作業中でも、今やるのが合理的と判断したら先頭に入れ、今の小目標を区切る。"""
        mock_text_generator.generate_json.return_value = {**BED_REQUEST, "when": "now"}
        session = _playing()

        await use_case.execute(self._request(session))

        assert session.plan.current.title == "ベッドで寝る"
        assert "have(log, 3)" in session.rethink_reason  # 戻る先を覚えている

    @pytest.mark.asyncio
    async def test_request_never_cuts_in(self, use_case, mock_text_generator):
        """先にしてと頼まれても、今の大目標の後ろに入る。"""
        mock_text_generator.generate_json.return_value = {**BED_REQUEST, "position": 1}
        session = _playing()

        await use_case.execute(self._request(session, "今すぐベッド作って！"))

        assert session.plan.current.title == "剣を持つ"

    @pytest.mark.asyncio
    async def test_second_request_from_the_same_viewer_is_declined(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        """上限にかかったらモデルに返し、断る返答が使われる。"""
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
        """受けた頼みを世界から判定できなければ、足さずに差し戻す。"""
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
        """どの生成も使えない頼みを受けたら、何も言わない。"""
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
    async def test_without_the_objectives_replies_only_talk(
        self, mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation
    ):
        """大目標を渡されないユースケースは、テキストだけで返答する。"""
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


class TestRethinkFromChat:
    """もっともな指摘で小目標を決め直す（今の目標に固執しない）。"""

    @pytest.mark.asyncio
    async def test_an_agreed_point_cuts_the_small_goal_once_per_interval(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        now = [100.0]
        use_case = GenerateResponseUseCase(
            text_generator=mock_text_generator,
            prompt_builder=mock_prompt_builder,
            event_publisher=mock_event_publisher,
            conversation=conversation,
            character=CharacterProfile(),
            objectives=ObjectiveKeeper(
                bridge=bridge, event_publisher=mock_event_publisher, store=store
            ),
            clock=lambda: now[0],
        )
        mock_text_generator.generate_json.return_value = {
            "reply": "ほんとだ、そこの木を切るね！",
            "request": "none",
            "rethink": "There is a tree right next to you; cut it instead of exploring.",
        }
        session = _playing()

        await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="すぐ横に木あるよ", session=session)
        )
        assert (
            "neko pointed out in chat: There is a tree right next to you" in session.rethink_reason
        )
        assert "you were on have(log, 3)" in session.rethink_reason

        # まだ使われていない考え直しには、間が短くても足す（小目標をもう一度切らない）
        now[0] += 10
        mock_text_generator.generate_json.return_value = {
            "reply": "そっちの木も見るね",
            "request": "none",
            "rethink": "Another tree is to the east.",
        }
        await use_case.execute(
            GenerateResponseRequest(user_name="mame", message="東にも木", session=session)
        )
        assert "neko pointed out" in session.rethink_reason
        assert "mame pointed out in chat: Another tree" in session.rethink_reason

        session.set_goal(Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=3)), "day")
        now[0] += 20  # 切ってから 60 秒以内: rethink は使えない。同意だけの返答は書き直させる
        mock_text_generator.generate_json.side_effect = [
            {"reply": "ほんとだ、そっちに行くね", "request": "none", "rethink": "Go there."},
            {"reply": "今の木を切り終わったら見てみるね", "request": "none"},
        ]
        response = await use_case.execute(
            GenerateResponseRequest(user_name="tama", message="こっちにも木", session=session)
        )
        assert response.text == "今の木を切り終わったら見てみるね"
        assert session.rethink_reason == ""
        error = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs["previous_error"]
        assert "rethink は今は使えない" in error

    @pytest.mark.asyncio
    async def test_no_reply_when_every_attempt_rethinks_too_soon(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        """間が短くて考え直せないのに、何度書かせても考え直すと言うなら言わない。"""
        now = [100.0]
        use_case = GenerateResponseUseCase(
            text_generator=mock_text_generator,
            prompt_builder=mock_prompt_builder,
            event_publisher=mock_event_publisher,
            conversation=conversation,
            character=CharacterProfile(),
            objectives=ObjectiveKeeper(
                bridge=bridge, event_publisher=mock_event_publisher, store=store
            ),
            clock=lambda: now[0],
        )
        mock_text_generator.generate_json.return_value = {
            "reply": "そうするね",
            "request": "none",
            "rethink": "Cut the tree.",
        }
        session = _playing()
        await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="木あるよ", session=session)
        )
        session.set_goal(Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=3)), "day")
        now[0] += 30

        response = await use_case.execute(
            GenerateResponseRequest(user_name="tama", message="木あるよ", session=session)
        )

        assert not response.success
        assert session.rethink_reason == ""

    @pytest.mark.asyncio
    async def test_no_rethink_without_the_field(self, use_case, mock_text_generator):
        mock_text_generator.generate_json.return_value = {"reply": "がんばる！", "request": "none"}
        session = _playing()
        await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="がんばれ", session=session)
        )
        assert not session.rethink_reason


class TestWithdraw:
    """頼んだ本人の「もういいよ」で、その人の大目標をやめる。"""

    @pytest.mark.asyncio
    async def test_the_requester_withdraws_their_own_objective(
        self, use_case, mock_text_generator, mock_event_publisher
    ):
        from ailoveshen.domain.events import ObjectiveDroppedEvent

        session = _playing()
        mock_text_generator.generate_json.return_value = BED_REQUEST
        await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="ベッド作って", session=session)
        )
        assert any(g.requested_by == "neko" for g in session.plan.pending)

        mock_text_generator.generate_json.return_value = {
            "reply": "わかった、ベッドはやめておくね",
            "request": "withdraw",
        }
        # ほかの人の「もういいよ」では消えない
        await use_case.execute(
            GenerateResponseRequest(user_name="tama", message="もういいよ", session=session)
        )
        assert any(g.requested_by == "neko" for g in session.plan.pending)

        await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="やっぱりもういいよ", session=session)
        )
        assert not any(g.requested_by == "neko" for g in session.plan.pending)
        dropped = [
            c.args[0]
            for c in mock_event_publisher.publish.call_args_list
            if isinstance(c.args[0], ObjectiveDroppedEvent)
        ]
        assert dropped[-1].requested_by == "neko"
        assert "withdrew" in dropped[-1].reason


    @pytest.mark.asyncio
    async def test_a_withdrawal_answered_with_none_is_sent_back(
        self, use_case, mock_text_generator
    ):
        """取り下げの仕分けなのに request が none だと、「要らない」と言うだけでリストに残る。"""
        session = _playing()
        mock_text_generator.generate_json.side_effect = [
            {"reply": "丸太はいらないんだね、助かった～", "request": "none"},
            {"reply": "わかった、丸太はやめておくね", "request": "withdraw"},
        ]
        await use_case.execute(
            GenerateResponseRequest(
                user_name="kuma", message="やっぱりいいや", session=session, kind="withdraw"
            )
        )
        assert mock_text_generator.generate_json.await_count == 2


@pytest.mark.asyncio
async def test_advice_the_streamer_agrees_with_becomes_a_lesson(
    mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation, bridge, store
):
    notes = Mock()
    use_case = GenerateResponseUseCase(
        text_generator=mock_text_generator,
        prompt_builder=mock_prompt_builder,
        event_publisher=mock_event_publisher,
        conversation=conversation,
        character=CharacterProfile(),
        objectives=ObjectiveKeeper(bridge=bridge, event_publisher=mock_event_publisher, store=store),
        notes=notes,
    )
    mock_text_generator.generate_json.return_value = {
        "reply": "たしかに！これからは焼いてから食べるね",
        "request": "none",
        "lesson": "肉は焼いてから食べる",
    }
    session = _playing()
    await use_case.execute(
        GenerateResponseRequest(user_name="neko", message="肉は焼いた方がいいよ", session=session)
    )
    notebook, lesson, viewer, _day = notes.learn_from_viewer.call_args.args
    assert (notebook, lesson, viewer) == (session.notebook, "肉は焼いてから食べる", "neko")


@pytest.mark.asyncio
async def test_with_a_lesson_book_the_advice_is_kept_with_when_it_applies(
    mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation, bridge, store
):
    """教訓帳があれば、アドバイスはメモではなく条件つきで教訓帳へ（docs/design/35）。"""
    notes, lessons = Mock(), Mock()
    use_case = GenerateResponseUseCase(
        text_generator=mock_text_generator,
        prompt_builder=mock_prompt_builder,
        event_publisher=mock_event_publisher,
        conversation=conversation,
        character=CharacterProfile(),
        objectives=ObjectiveKeeper(bridge=bridge, event_publisher=mock_event_publisher, store=store),
        notes=notes,
        lessons=lessons,
    )
    mock_text_generator.generate_json.return_value = {
        "reply": "覚えておくね",
        "request": "none",
        "lesson": "夜は外に出ずに家で寝る",
        "lesson_when": {"text": "夜になったとき", "time": "night", "place": "moon"},
    }
    await use_case.execute(
        GenerateResponseRequest(
            user_name="neko", message="夜は出歩かない方がいいよ", session=_playing()
        )
    )
    notes.learn_from_viewer.assert_not_called()
    text, condition, keys = lessons.learn.call_args.args
    assert (text, condition, keys) == (
        "夜は外に出ずに家で寝る",
        "夜になったとき",
        {"time": "night"},
    )
    assert lessons.learn.call_args.kwargs == {"explicit": True, "source": "viewer:neko"}


class TestEmptyPromise:
    """約束だけで何もしない返事は書き直させる（docs/design/34 §10）。"""

    def _use_case(self, generator, builder, events, conversation, bridge, store, promises):
        check = AsyncMock()
        check.promises.side_effect = promises
        lessons = Mock()
        use_case = GenerateResponseUseCase(
            text_generator=generator,
            prompt_builder=builder,
            event_publisher=events,
            conversation=conversation,
            character=CharacterProfile(),
            objectives=ObjectiveKeeper(bridge=bridge, event_publisher=events, store=store),
            lessons=lessons,
            promise_check=check,
        )
        return use_case, check, lessons

    @pytest.mark.asyncio
    async def test_a_promise_becomes_an_objective_done_now(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        """実例（2026-09-27 12:17）: 「目の前の牛さんからお肉を分けてもらうね」は、生存の小目標で
        していても大目標にし、今やるなら今の小目標を区切る。返答はそのまま話す。"""
        use_case, check, lessons = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True],
        )
        mock_text_generator.generate_json.side_effect = [
            {
                "reply": "今はまず目の前の牛さんからお肉を分けてもらうね！",
                "request": "none",
                "lesson": "お腹が空いたら動物から肉",
            },
            {
                "decision": "now",
                "level": "objective",
                "title": "牛からお肉をもらう",
                "conditions": [{"predicate": "have", "item": "beef", "count": 3}],
                "reason": "牛が目の前にいて、お腹が空いている",
            },
        ]
        session = _playing()

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="牛いるよ", session=session)
        )

        assert response.text == "今はまず目の前の牛さんからお肉を分けてもらうね！"
        assert response.objective is not None and response.objective.title == "牛からお肉をもらう"
        assert session.plan.current.title == "牛からお肉をもらう"
        assert session.plan.current.requested_by == "neko"
        assert 'you told neko you would do "牛からお肉をもらう" now' in session.rethink_reason
        check.promises.assert_awaited_once_with(
            "牛いるよ", "今はまず目の前の牛さんからお肉を分けてもらうね！", "none"
        )
        _, schema = mock_text_generator.generate_json.await_args_list[1].args
        assert schema["properties"]["decision"]["enum"] == ["none", "already", "now", "later", "cannot"]
        assert mock_text_generator.generate_json.await_args_list[1].kwargs["purpose"] == "promise"
        mock_prompt_builder.build_system_prompt.assert_any_call(CharacterProfile(), "promise")
        assert lessons.learn.call_count == 1

    @pytest.mark.asyncio
    async def test_a_promise_for_later_goes_behind_the_current_objective(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        """断った返答（「落ち着いたらチャレンジするね」）の約束は、今やらず後ろに入れる。"""
        use_case, _, _ = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True],
        )
        mock_text_generator.generate_json.side_effect = [
            {
                "reply": "今は小麦が足りないから、落ち着いたら牛さんを増やしてみるね！",
                "request": "decline",
            },
            {
                "decision": "later",
                "level": "objective",
                "title": "牛を増やす",
                "conditions": [{"predicate": "have", "item": "wheat", "count": 4}],
                "position": 2,
                "reason": "エサの小麦から",
            },
        ]
        session = _playing()

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="牛増やそう", session=session)
        )

        assert response.objective is not None and response.objective.title == "牛を増やす"
        assert [g.title for g in session.plan.pending][:2] == ["剣を持つ", "牛を増やす"]
        assert session.rethink_reason == ""
        _, schema = mock_text_generator.generate_json.await_args_list[1].args
        assert "now" not in schema["properties"]["decision"]["enum"]

    @pytest.mark.asyncio
    async def test_a_promise_an_objective_already_keeps_is_spoken_as_it_is(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        use_case, _, _ = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True],
        )
        mock_text_generator.generate_json.side_effect = [
            {"reply": "剣を作るために木を切ってくるね！", "request": "none"},
            {"decision": "already", "reason": "剣を持つが先頭にある"},
        ]
        session = _playing()

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="剣は？", session=session)
        )

        assert response.text == "剣を作るために木を切ってくるね！"
        assert response.objective is None
        assert len(session.plan.pending) == 2 and session.rethink_reason == ""

    @pytest.mark.asyncio
    async def test_a_promise_that_cannot_be_kept_is_said_without_it_from_the_same_decision(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        """守れない約束は、約束の判断が書き直した返答を話す（返答を作り直さない。2026-09-27:
        同じコメントに 2 回返答を作っていた）。"""
        use_case, check, lessons = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True],
        )
        mock_text_generator.generate_json.side_effect = [
            {"reply": "ダンスしてみるね！", "request": "none", "lesson": "踊る"},
            {
                "decision": "cannot",
                "reason": "dancing is not a condition",
                "rewritten_reply": "ダンスはまだできないんだ、ごめんね！",
            },
        ]

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="踊って", session=_playing())
        )

        assert response.text == "ダンスはまだできないんだ、ごめんね！"
        assert mock_text_generator.generate_json.await_count == 2
        assert check.promises.await_count == 1
        lessons.learn.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_reply_jev_misread_as_a_promise_is_spoken_as_it_is(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        """実例（2026-09-27 13:07）: 今していることの説明は約束ではない（none: そのまま話す）。"""
        use_case, _, _ = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True],
        )
        mock_text_generator.generate_json.side_effect = [
            {"reply": "今作業台を作って資材保管庫を準備してるよ！", "request": "none"},
            {"decision": "none", "reason": "it only tells what the streamer is doing now"},
        ]
        session = _playing()

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="なんて？", session=session)
        )

        assert response.text == "今作業台を作って資材保管庫を準備してるよ！"
        assert response.objective is None and len(session.plan.pending) == 2
        assert mock_text_generator.generate_json.await_count == 2

    @pytest.mark.asyncio
    async def test_a_promise_that_cannot_be_kept_without_a_rewrite_is_written_again(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        use_case, _, lessons = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True, False, False],
        )
        mock_text_generator.generate_json.side_effect = [
            {"reply": "ダンスしてみるね！", "request": "none", "lesson": "踊る"},
            {"decision": "cannot", "reason": "dancing is not a condition"},
            {"reply": "ダンスはまだできないんだ、ごめんね！", "request": "decline"},
        ]

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="踊って", session=_playing())
        )

        assert response.text == "ダンスはまだできないんだ、ごめんね！"
        error = mock_prompt_builder.build_chat_response_prompt.call_args.kwargs["previous_error"]
        assert "dancing is not a condition" in error and "約束しない" in error
        lessons.learn.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_objective_the_plan_refuses_is_decided_again(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        """大目標に入らない守り方（条件の形が違う）は、理由をつけてもう一度決めさせる。"""
        use_case, _, _ = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True],
        )
        mock_text_generator.generate_json.side_effect = [
            {"reply": "松明を置いておくね！", "request": "none"},
            {"decision": "later", "level": "objective", "title": "明るく", "conditions": [], "reason": "r"},
            {
                "decision": "later",
                "level": "objective",
                "title": "明るく",
                "conditions": [{"predicate": "placed", "item": "torch", "count": 2}],
                "reason": "r",
            },
        ]

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="暗いね", session=_playing())
        )

        assert response.objective is not None and response.objective.title == "明るく"
        error = mock_prompt_builder.build_promise_prompt.call_args.kwargs["previous_error"]
        assert "needs conditions" in error

    @pytest.mark.asyncio
    async def test_a_promise_without_a_level_is_sent_back_and_a_job_goes_into_the_current_objective(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        """層（大目標か中目標か）を先に決めさせる。層のない約束は、必ず大目標になっていた（2026-10-07）。"""
        use_case, _, _ = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True],
        )
        pick = [{"predicate": "have", "item": "wooden_pickaxe", "count": 1}]
        mock_text_generator.generate_json.side_effect = [
            {"reply": "ツルハシ作るね！", "request": "none"},
            {"decision": "now", "title": "木のツルハシ", "conditions": pick, "reason": "r"},
            {"decision": "now", "level": "job", "title": "木のツルハシ", "conditions": pick, "reason": "r"},
        ]
        session = _playing()
        before = len(session.plan.objectives) if hasattr(session.plan, "objectives") else None

        response = await use_case.execute(self._request(session, "ツルハシ作ったら？"))

        error = mock_prompt_builder.build_promise_prompt.call_args.kwargs["previous_error"]
        assert "level is missing" in error
        # 中目標として、今の大目標（m1）に入った。大目標は増えていない
        assert response.objective is not None and response.objective.id == "m1"
        assert any(j.spec.item == "wooden_pickaxe" for j in session.plan.current.jobs)
        if before is not None:
            assert len(session.plan.objectives) == before

    @pytest.mark.asyncio
    async def test_now_right_after_a_chat_rethink_waits_behind(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        """チャットで小目標を切ったばかりなら、今やる約束も後ろに入れる（小目標をばたつかせない）。"""
        use_case, _, _ = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True, True],
        )
        beef = [{"predicate": "have", "item": "beef", "count": 3}]
        mock_text_generator.generate_json.side_effect = [
            {"reply": "牛さん倒してくるね！", "request": "none"},
            {"decision": "now", "level": "objective", "title": "牛", "conditions": beef, "reason": "r"},
            {"reply": "羊さんも倒してくるね！", "request": "none"},
            {
                "decision": "now",
                "level": "objective",
                "title": "羊",
                "conditions": [{"predicate": "have", "item": "mutton", "count": 3}],
                "reason": "r",
            },
        ]
        session = _playing()
        await use_case.execute(self._request(session, "牛いるよ"))
        # 次の切れ目で小目標を決め直した（考え直しは使われた）
        session.set_goal(
            Goal(SWORD, "剣", objective_id="m1"), "day", rethinks_seen=session.rethink_mark
        )

        response = await use_case.execute(self._request(session, "羊もいるよ", user_name="mame"))

        assert response.objective is not None and response.objective.title == "羊"
        assert session.rethink_reason == ""
        assert session.plan.current.title == "牛"

    @pytest.mark.asyncio
    async def test_a_reply_that_promises_nothing_is_spoken(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        use_case, check, lessons = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [False],
        )
        mock_text_generator.generate_json.return_value = {
            "reply": "覚えておくね",
            "request": "none",
            "lesson": "夜は家で寝る",
        }

        response = await use_case.execute(
            GenerateResponseRequest(
                user_name="neko", message="夜は出歩かないで", session=_playing()
            )
        )

        assert response.text == "覚えておくね"
        assert mock_text_generator.generate_json.await_count == 1
        lessons.learn.assert_called_once()

    @pytest.mark.asyncio
    async def test_accept_and_rethink_are_not_checked(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        use_case, check, _ = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True, True],
        )
        mock_text_generator.generate_json.side_effect = [
            BED_REQUEST,
            {
                "reply": "そうだね、先に木を切るね",
                "request": "none",
                "rethink": "a tree is right there",
            },
        ]
        session = _playing()
        await use_case.execute(self._request(session, "ベッド作って"))
        await use_case.execute(self._request(session, "そこに木あるよ", user_name="mame"))
        check.promises.assert_not_awaited()
        assert "a tree is right there" in session.rethink_reason

    @pytest.mark.asyncio
    async def test_no_reply_when_no_attempt_keeps_its_promise(
        self,
        mock_text_generator,
        mock_prompt_builder,
        mock_event_publisher,
        conversation,
        bridge,
        store,
    ):
        """何度書かせても守れない約束なら言わない（受けられない頼みと同じ）。"""
        use_case, _, lessons = self._use_case(
            mock_text_generator,
            mock_prompt_builder,
            mock_event_publisher,
            conversation,
            bridge,
            store,
            [True, True],
        )
        mock_text_generator.generate_json.side_effect = [
            {"reply": "踊るね！", "request": "none", "lesson": "踊る"},
            {"decision": "cannot", "reason": "no"},
            {"reply": "踊るね！", "request": "none", "lesson": "踊る"},
            {"decision": "cannot", "reason": "no"},
        ]

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="踊って", session=_playing())
        )

        assert not response.success
        lessons.learn.assert_not_called()

    @staticmethod
    def _request(session, message, user_name="neko"):
        return GenerateResponseRequest(user_name=user_name, message=message, session=session)


class TestSuggestionsAreNotBrushedOff:
    """
    頼み・助言を「今は○○中だから後で」と流さない（2026-09-28: 受けようとして視聴者の上限に当たり、
    none / decline で流して何も残らなかった）。
    """

    FARM = {
        "reply": "たしかに！ 今の鉄集めが済んだら畑を広げるね",
        "request": "accept",
        "title": "畑を広げる",
        "conditions": [{"predicate": "farmed", "item": "wheat", "count": 16}],
        "position": 2,
        "reason": "食べ物が安定する",
        "when": "next",
        "relevance": "partly",
        "relevance_reason": "a bigger farm serves the objective, not the current step",
    }

    @staticmethod
    def _request(session, message, kind, user_name="neko"):
        return GenerateResponseRequest(
            user_name=user_name, message=message, session=session, kind=kind
        )

    @pytest.mark.asyncio
    async def test_advice_becomes_her_own_objective_beyond_the_viewer_limit(
        self, use_case, mock_text_generator
    ):
        """採った助言は配信者の大目標（提案者つき）。同じ人が頼みを持っていても入る。"""
        mock_text_generator.generate_json.side_effect = [BED_REQUEST, self.FARM]
        session = _playing()
        await use_case.execute(self._request(session, "ベッド作って！", "request"))

        response = await use_case.execute(self._request(session, "畑広げない？", "advice"))

        assert response.objective is not None
        assert response.objective.suggested_by == "neko"
        assert response.objective.requested_by is None
        assert "畑を広げる" in [g.title for g in session.plan.pending]

    @pytest.mark.asyncio
    async def test_the_reply_may_tell_a_suggestion_the_triage_took_for_a_request(
        self, use_case, mock_text_generator
    ):
        """「畑広げない？」を Jev は頼みと仕分けた（2026-09-28）: 返答の suggestion で自分の大目標に。"""
        mock_text_generator.generate_json.side_effect = [
            BED_REQUEST,
            {**self.FARM, "suggestion": True},
        ]
        session = _playing()
        await use_case.execute(self._request(session, "ベッド作って！", "request"))

        response = await use_case.execute(self._request(session, "畑広げない？", "request"))

        assert response.objective is not None
        assert response.objective.suggested_by == "neko"
        assert response.objective.requested_by is None

    @pytest.mark.asyncio
    async def test_the_same_conditions_are_not_added_twice(self, use_case, mock_text_generator):
        """同じ整地が 2 つ並んだ（2026-09-28）: 同じ完了条件の大目標があれば、それを返す。"""
        mock_text_generator.generate_json.side_effect = [self.FARM, {**self.FARM, "when": "now"}]
        session = _playing()
        first = await use_case.execute(self._request(session, "畑広げない？", "advice"))
        second = await use_case.execute(self._request(session, "畑を広げようよ", "advice", "inu"))

        assert second.objective is not None and first.objective is not None
        assert second.objective.id == first.objective.id
        assert [g.title for g in session.plan.pending].count("畑を広げる") == 1
        assert session.plan.current.id == first.objective.id  # 今やる: 先頭に
        assert session.rethink_reason

    @pytest.mark.asyncio
    async def test_advice_put_off_while_the_small_goal_fails_is_asked_once(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        """家に入れない理由の指摘（家の前の穴）を後回しにして、入る失敗を続けた（2026-09-28）。"""
        mock_text_generator.generate_json.side_effect = [
            self.FARM,
            {**self.FARM, "when": "now", "rethink": "the hole blocks the door"},
        ]
        session = _playing()
        session.consecutive_failures = 3

        response = await use_case.execute(self._request(session, "家の前の穴を埋めて", "advice"))

        assert session.plan.current.id == response.objective.id
        assert session.rethink_reason
        errors = [
            c.kwargs["previous_error"]
            for c in mock_prompt_builder.build_chat_response_prompt.call_args_list
        ]
        assert "3 回続けて失敗" in errors[1]

    @pytest.mark.asyncio
    async def test_unrelated_advice_may_still_wait(self, use_case, mock_text_generator):
        mock_text_generator.generate_json.side_effect = [self.FARM, self.FARM]
        session = _playing()
        session.consecutive_failures = 3

        response = await use_case.execute(self._request(session, "畑広げない？", "advice"))

        assert response.objective is not None
        assert session.plan.current.id == "m1"
        assert mock_text_generator.generate_json.await_count == 2

    @pytest.mark.asyncio
    async def test_brushing_off_advice_is_sent_back(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        busy = {"reply": "今は鉄を掘ってるから、畑は後だね", "request": "none"}
        declined = {"reply": "今は忙しいからまた今度", "request": "decline"}
        mock_text_generator.generate_json.side_effect = [busy, declined, self.FARM]
        session = _playing()

        response = await use_case.execute(self._request(session, "畑拡張は？", "advice"))

        assert response.objective is not None and response.objective.title == "畑を広げる"
        errors = [
            c.kwargs["previous_error"]
            for c in mock_prompt_builder.build_chat_response_prompt.call_args_list
        ]
        assert "none で答えると何も残らない" in errors[1]
        assert "decline には decline_reason" in errors[2]

    @pytest.mark.asyncio
    async def test_an_honest_decline_or_a_lesson_is_kept(self, use_case, mock_text_generator):
        wrong = {
            "reply": "チェストを見たら、もう苗木は入ってたよ！",
            "request": "decline",
            "decline_reason": "wrong",
        }
        mock_text_generator.generate_json.side_effect = [wrong]
        session = _playing()

        response = await use_case.execute(self._request(session, "苗木集めたら？", "advice"))

        assert response.text == "チェストを見たら、もう苗木は入ってたよ！"
        assert mock_text_generator.generate_json.await_count == 1

    @pytest.mark.asyncio
    async def test_the_last_try_is_spoken_rather_than_staying_silent(
        self, use_case, mock_text_generator
    ):
        busy = {"reply": "今は鉄を掘ってるから、畑は後だね", "request": "none"}
        mock_text_generator.generate_json.side_effect = [busy, busy, busy]
        session = _playing()

        response = await use_case.execute(self._request(session, "畑拡張は？", "advice"))

        assert response.text == "今は鉄を掘ってるから、畑は後だね"
        assert mock_text_generator.generate_json.await_count == 3

    @pytest.mark.asyncio
    async def test_chat_and_unknown_kinds_are_not_checked(self, use_case, mock_text_generator):
        mock_text_generator.generate_json.side_effect = [
            {"reply": "ありがとう！", "request": "none"},
            {"reply": "ねこも好き！", "request": "none"},
        ]
        session = _playing()
        await use_case.execute(self._request(session, "がんばれ", "chat"))
        await use_case.execute(self._request(session, "ねこ好き？", None))
        assert mock_text_generator.generate_json.await_count == 2


class TestAdviceIsComparedWithTheCurrentGoal:
    """
    助言が来たら、今の目標との関わりを見て、関わると納得したなら今の小目標を決め直す
    （docs/design/49 §8。助言が教訓や後ろの大目標になるだけで、今の小目標が見直されなかった）。
    """

    LESSON = {"reply": "たしかに、覚えておくね", "request": "none", "lesson": "木の近くで作業台を作る"}

    @staticmethod
    def _request(session, message="そこに木あるよ", kind="advice"):
        return GenerateResponseRequest(user_name="neko", message=message, session=session, kind=kind)

    @staticmethod
    def _errors(mock_prompt_builder):
        return [
            c.kwargs["previous_error"]
            for c in mock_prompt_builder.build_chat_response_prompt.call_args_list
        ]

    @pytest.mark.asyncio
    async def test_advice_without_a_relevance_is_sent_back(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        judged = {**self.LESSON, "relevance": "unrelated", "relevance_reason": "for later"}
        mock_text_generator.generate_json.side_effect = [self.LESSON, judged]
        session = _playing()

        await use_case.execute(self._request(session))

        assert "relevance" in self._errors(mock_prompt_builder)[1]
        assert not session.rethink_reason

    @pytest.mark.asyncio
    async def test_related_advice_agreed_with_needs_a_rethink(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        related = {**self.LESSON, "relevance": "related", "relevance_reason": "a tree is right here"}
        rethought = {**related, "rethink": "chop the tree right here instead of walking"}
        mock_text_generator.generate_json.side_effect = [related, rethought]
        session = _playing()

        await use_case.execute(self._request(session))

        assert "rethink" in self._errors(mock_prompt_builder)[1]
        assert "chop the tree right here" in session.rethink_reason

    @pytest.mark.asyncio
    async def test_a_related_suggestion_accepted_for_later_needs_a_rethink_too(
        self, use_case, mock_text_generator
    ):
        farm = TestSuggestionsAreNotBrushedOff.FARM
        related = {**farm, "relevance": "related", "relevance_reason": "the farm is the current job"}
        mock_text_generator.generate_json.side_effect = [related, {**related, "rethink": "expand the farm first"}]
        session = _playing()

        response = await use_case.execute(self._request(session, "畑広げない？"))

        assert response.objective is not None
        assert session.rethink_reason
        assert mock_text_generator.generate_json.await_count == 2

    @pytest.mark.asyncio
    async def test_partly_or_unrelated_advice_needs_no_rethink(self, use_case, mock_text_generator):
        partly = {**self.LESSON, "relevance": "partly", "relevance_reason": "a later job"}
        mock_text_generator.generate_json.side_effect = [partly]
        session = _playing()

        await use_case.execute(self._request(session))

        assert mock_text_generator.generate_json.await_count == 1
        assert not session.rethink_reason

    @pytest.mark.asyncio
    async def test_a_declined_advice_needs_no_relevance(self, use_case, mock_text_generator):
        wrong = {"reply": "もうあるよ", "request": "decline", "decline_reason": "wrong"}
        mock_text_generator.generate_json.side_effect = [wrong]
        await use_case.execute(self._request(_playing()))
        assert mock_text_generator.generate_json.await_count == 1

    @pytest.mark.asyncio
    async def test_no_rethink_is_demanded_while_the_chat_cap_holds(
        self, use_case, mock_text_generator
    ):
        """前の指摘から 60 秒たっていないと rethink は使えない: 使えないものを書かせて詰まらせない。"""
        related = {**self.LESSON, "relevance": "related", "relevance_reason": "a tree is right here"}
        first = {**related, "rethink": "chop the tree"}
        mock_text_generator.generate_json.side_effect = [first, related]
        session = _playing()
        await use_case.execute(self._request(session))
        session._rethinks.clear()  # 前の考え直しはもう使われた

        await use_case.execute(self._request(session, "こっちにも木あるよ"))

        assert mock_text_generator.generate_json.await_count == 2


class TestRelatedAdviceIsNotParkedForLater:
    """
    「斧があれば楽」と言いながら「材料集めが終わったら斧を作る」と言う食い違い（2026-10-07、docs/design/92）。
    関わる（related）と見た助言を引き受けて今の作業の後（when=next）に回すと、効果が今の作業に届かない。
    """

    AXE = {
        "reply": "斧があるとずっと楽だね。作るね！",
        "request": "accept",
        "suggestion": True,
        "relevance": "related",
        "relevance_reason": "An axe makes gathering logs faster",
        "level": "job",
        "title": "斧を作る",
        "conditions": [{"predicate": "have", "item": "wooden_axe", "count": 1}],
        "position": 2,
        "reason": "faster logs",
        "when": "next",
    }

    def test_the_reply_is_written_after_the_decisions_it_must_agree_with(self):
        keys = list(reply_schema()["properties"])
        assert keys[0] == "request"
        for decided in ("relevance", "relevance_reason", "level", "position", "when", "rethink", "lesson"):
            assert keys.index(decided) < keys.index("reply"), decided
        assert keys.index("reply") < keys.index("name_reading") < keys.index("self_facts")

    def test_accepting_related_advice_for_later_is_sent_back_when_a_rethink_cannot_be_used(self):
        problem = unjudged("advice", self.AXE, can_rethink=False)
        assert "when=now" in problem and "partly" in problem

    def test_now_partly_or_a_rethink_are_fine(self):
        assert unjudged("advice", {**self.AXE, "when": "now"}, can_rethink=False) == ""
        assert unjudged("advice", {**self.AXE, "relevance": "partly"}, can_rethink=False) == ""
        assert unjudged("advice", {**self.AXE, "rethink": "make the axe first"}, can_rethink=False) == ""

    def test_when_a_rethink_can_be_used_it_still_asks_for_one(self):
        assert "rethink" in unjudged("advice", self.AXE, can_rethink=True)

    def test_a_reply_that_only_remembers_is_not_sent_back_while_the_rethink_is_throttled(self):
        lesson = {"reply": "覚えておくね", "request": "none", "relevance": "related", "relevance_reason": "x"}
        assert unjudged("advice", lesson, can_rethink=False) == ""


class TestProducerInstruction:
    """管理画面のディレクターの指示は、視聴者の頼みの上限に数えない自分の大目標になる（設計書 54）。"""

    NOW = {
        "reply": "了解、先に畑を広げるね！",
        "request": "accept",
        "title": "畑を広げる",
        "conditions": [{"predicate": "farmed", "item": "wheat", "count": 16}],
        "position": 1,
        "reason": "ディレクターの指示",
        "when": "now",
        "rethink": "ディレクターの指示で畑を先にする",
    }

    @staticmethod
    def _request(session, message="畑を先に広げて", producer=True):
        return GenerateResponseRequest(
            user_name="ディレクター", message=message, session=session, producer=producer
        )

    @pytest.mark.asyncio
    async def test_the_instruction_is_her_own_objective_at_the_top_and_cuts_the_small_goal(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        session = _playing()
        mock_text_generator.generate_json.side_effect = [BED_REQUEST]
        # 視聴者の頼みが 1 つ入っている（1 人 1 つの上限）: ディレクターはそれに数えない
        await use_case.execute(GenerateResponseRequest(
            user_name="neko", message="ベッド作って！", session=session, kind="request"))
        mock_text_generator.generate_json.side_effect = [self.NOW]

        response = await use_case.execute(self._request(session))

        assert response.success and response.text == "了解、先に畑を広げるね！"
        assert response.objective is not None
        assert response.objective.suggested_by == "ディレクター"
        assert response.objective.requested_by is None
        assert session.plan.current.title == "畑を広げる"
        assert session.rethink_reason  # 今やる: 小目標を次の切れ目で決め直す
        assert mock_prompt_builder.build_chat_response_prompt.call_args.kwargs["producer"] is True

    @pytest.mark.asyncio
    async def test_a_viewer_comment_is_not_a_producer_instruction(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        mock_text_generator.generate_json.side_effect = [
            {"reply": "うん", "request": "none"},
        ]
        await use_case.execute(GenerateResponseRequest(
            user_name="neko", message="こんにちは", session=_playing(), kind="chat"))
        assert mock_prompt_builder.build_chat_response_prompt.call_args.kwargs["producer"] is False


class TestReplyFacts:
    """持っている物を「ない」と言う返事は、事実を添えて 1 回だけ書き直させる。"""

    def _use_case(self, generator, builder, events, conversation, bridge, store, problems):
        check = AsyncMock()
        check.problem.side_effect = problems
        use_case = GenerateResponseUseCase(
            text_generator=generator,
            prompt_builder=builder,
            event_publisher=events,
            conversation=conversation,
            character=CharacterProfile(),
            objectives=ObjectiveKeeper(bridge=bridge, event_publisher=events, store=store),
            fact_check=check,
        )
        return use_case, check

    @pytest.mark.asyncio
    async def test_a_reply_that_denies_what_is_held_is_written_again_with_the_facts(
        self, mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation, bridge, store
    ):
        use_case, check = self._use_case(
            mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation, bridge, store,
            ["事実は チェスト: 2 raw_iron", ""],
        )
        mock_text_generator.generate_json.side_effect = [
            {"reply": "鉄はまだ見つからなくて", "request": "none"},
            {"reply": "鉄は 2 個チェストにあるよ、あと 1 個！", "request": "none"},
        ]
        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="鉄集まった？", session=_playing())
        )
        assert response.text == "鉄は 2 個チェストにあるよ、あと 1 個！"
        assert mock_prompt_builder.build_chat_response_prompt.call_args.kwargs["previous_error"] == (
            "事実は チェスト: 2 raw_iron"
        )

    @pytest.mark.asyncio
    async def test_the_last_attempt_is_spoken_even_when_it_still_differs(
        self, mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation, bridge, store
    ):
        use_case, check = self._use_case(
            mock_text_generator, mock_prompt_builder, mock_event_publisher, conversation, bridge, store,
            ["食い違う"] * 5,
        )
        mock_text_generator.generate_json.side_effect = [
            {"reply": "ない", "request": "none"},
            {"reply": "やっぱりない", "request": "none"},
        ]
        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="鉄集まった？", session=_playing())
        )
        assert response.text == "やっぱりない"
        assert check.problem.await_count == 1  # 最後の試しは確かめずに話す


class TestJobAdviceRethink:
    """
    助言を中目標として採って今やると言うとき、約束したのはその中目標（2026-10-10 19:57）。大目標の名前を
    約束したことにして、今の中目標を止めて別のことを始めた。もう今の中目標なら、小目標は区切らない。
    """

    def _proposal(self, item="wooden_pickaxe"):
        return ObjectiveProposal(
            title="木のツルハシ",
            conditions=(GoalSpec(GoalPredicate.HAVE, item=item, count=1),),
            reason="r",
            now=True,
            job=True,
        )

    @pytest.mark.asyncio
    async def test_a_new_job_is_named_by_the_job_not_by_the_objective(self, use_case):
        session = _playing()

        await use_case._accept(session, "neko", self._proposal(), True)

        assert 'you would do "木のツルハシ" now (a job of the objective "剣を持つ"' in session.rethink_reason

    @pytest.mark.asyncio
    async def test_the_job_that_is_already_the_current_one_does_not_cut_the_small_goal(self, use_case):
        session = _playing()
        await use_case._accept(session, "neko", self._proposal(), True)
        session.set_goal(
            Goal(GoalSpec(GoalPredicate.HAVE, item="wooden_pickaxe", count=1), "r", objective_id="m1"),
            "day",
        )
        assert session.rethink_reason == ""

        await use_case._accept(session, "neko", self._proposal(), True)

        assert session.rethink_reason == ""


class TestAdviceThatCannotBeAcceptedStillGetsAnswer:
    """実測（2026-10-10 19:39）: 「苗木の上に土を置かないで」に、条件の形が 3 回合わず、返事が出なかった。"""

    BROKEN = {
        "reply": "気をつけるね、教えてくれてありがとう！",
        "request": "accept",
        "suggestion": False,
        "relevance": "related",
        "relevance_reason": "the sapling job",
        "level": "job",
        "title": "苗木を植えるときは上に土を置かない",
        "conditions": [{"predicate": "planted", "count": 16}],  # item がない
        "position": 2,
        "reason": "r",
        "when": "now",
        "lesson": "苗木の上には土ブロックを置かない。",
        "lesson_when": {"text": "苗木を植えるとき"},
    }

    @pytest.mark.asyncio
    async def test_the_last_attempt_speaks_and_keeps_the_lesson(
        self, use_case, mock_text_generator
    ):
        mock_text_generator.generate_json.return_value = self.BROKEN
        session = _playing()

        response = await use_case.execute(
            GenerateResponseRequest(
                user_name="neko",
                message="苗木の上に土ブロックを置かないでください",
                session=session,
                kind="advice",
            )
        )

        assert response.success is True
        assert response.text == "気をつけるね、教えてくれてありがとう！"
        assert response.objective is None
        assert mock_text_generator.generate_json.await_count == 3


BED_NOW = {
    "request": "accept",
    "suggestion": False,
    "level": "job",
    "title": "家にベッドを置く",
    "conditions": [{"predicate": "placed", "item": "bed", "where": "home", "count": 1}],
    "position": 2,
    "reason": "夜を越すため",
    "when": "now",
}
CHEST_NOW = {
    **BED_NOW,
    "title": "家にチェストを置く",
    "conditions": [{"predicate": "placed", "item": "chest", "where": "home", "count": 1}],
}


def _night() -> PlaySession:
    """夜を越している間（through_night）。"""
    session = _playing("night")
    session.set_goal(Goal(GoalSpec(GoalPredicate.THROUGH_NIGHT), "夜は危ない"), "night")
    return session


class TestTheReplyFollowsWhatWasDecided:
    """
    返答の文は、行動が決まってから、その結果（いつやるか・断った理由）を事実として書き直す
    （docs/design/159。2026-10-10 21:36: 「朝になってから置くね」と言って、夜に寝るためのベッドを置かなかった）。
    """

    def _final_prompts(self, mock_prompt_builder):
        return [c.kwargs for c in mock_prompt_builder.build_final_reply_prompt.call_args_list]

    @pytest.mark.asyncio
    async def test_the_reply_is_written_again_from_the_outcome_and_that_is_spoken(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        mock_text_generator.generate_json.return_value = {
            "reply": "朝になってから置くね", "relevance": "related", "relevance_reason": "x", **BED_NOW
        }
        mock_text_generator.generate.side_effect = None
        mock_text_generator.generate.return_value = "ねこかもさん、ベッド、今すぐ置くね！"

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="ベッド設置して", session=_night(), kind="request")
        )

        assert response.text == "ねこかもさん、ベッド、今すぐ置くね！"
        (final,) = self._final_prompts(mock_prompt_builder)
        assert "今すぐやる" in final["facts"] and "家にベッドを置く" in final["facts"]
        assert final["draft"] == "朝になってから置くね"  # 下書きは材料。話すのは書き直したほう
        assert mock_text_generator.generate.call_args.kwargs["purpose"] == "reply_final"

    @pytest.mark.asyncio
    async def test_at_night_a_bed_in_the_home_is_not_held_until_morning_but_other_things_are(
        self, use_case, mock_text_generator
    ):
        """夜の待ちは小目標を切り替えない決まり。ベッドを家に置く頼みだけは例外（ブリッジが置く手を出す）。"""
        mock_text_generator.generate_json.return_value = {"reply": "うん", **BED_NOW}
        bed_session = _night()
        await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="ベッド設置して", session=bed_session, kind="request")
        )
        assert bed_session.rethink_reason and not bed_session.defers_rethink()

        mock_text_generator.generate_json.return_value = {"reply": "うん", **CHEST_NOW}
        chest_session = _night()
        await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="チェスト置いて", session=chest_session, kind="request")
        )
        assert chest_session.rethink_reason and chest_session.defers_rethink()

    @pytest.mark.asyncio
    async def test_a_request_held_until_morning_is_told_as_such_and_now_is_not_claimed(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        mock_text_generator.generate_json.return_value = {"reply": "今すぐ置くね", **CHEST_NOW}
        mock_text_generator.generate.side_effect = [
            "今すぐ置くね！",  # 夜の間は動かないのに「今すぐ」: 直させる
            "夜が明けてから、チェストを置くね！",
        ]

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="チェスト置いて", session=_night(), kind="request")
        )

        assert response.text == "夜が明けてから、チェストを置くね！"
        first, second = self._final_prompts(mock_prompt_builder)
        assert "夜が明けてからやる" in first["facts"] and "夜が明けてからやる" in second["facts"]
        assert "(right now)" in second["previous_error"]

    @pytest.mark.asyncio
    async def test_a_reply_that_still_disagrees_is_replaced_by_a_line_built_from_the_outcome(
        self, use_case, mock_text_generator
    ):
        mock_text_generator.generate_json.return_value = {"reply": "朝になってから置くね", **BED_NOW}
        mock_text_generator.generate.side_effect = None
        mock_text_generator.generate.return_value = "朝になってから置くね！"  # 今やるのに、2 回とも「朝になってから」

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="ベッド設置して", session=_night(), kind="request")
        )

        assert "朝になって" not in response.text
        assert response.text == "nekoさん、「家にベッドを置く」、今やるね！"
        assert mock_text_generator.generate.await_count == 2

    @pytest.mark.asyncio
    async def test_a_decline_is_told_with_the_reason_and_without_a_promise(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        mock_text_generator.generate_json.return_value = {
            "reply": "あとでやるね", "request": "decline", "decline_reason": "unsafe", "reason": "溶岩の上だから"
        }
        mock_text_generator.generate.side_effect = [
            "あとで置くね！",  # 断ったのにやると言う
            "ごめんね、溶岩の上は危ないからやらないよ。",
        ]

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="溶岩の上に家建てて", session=_playing(), kind="request")
        )

        assert response.text == "ごめんね、溶岩の上は危ないからやらないよ。"
        first, second = self._final_prompts(mock_prompt_builder)
        assert "断った。理由: 危ない" in first["facts"] and "溶岩の上だから" in first["facts"]
        assert "nothing was registered" in second["previous_error"]

    @pytest.mark.asyncio
    async def test_plain_small_talk_is_not_written_again(
        self, use_case, mock_text_generator, mock_prompt_builder
    ):
        mock_text_generator.generate_json.return_value = {"reply": "元気だよ！", "request": "none"}

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="元気？", session=_playing(), kind="chat")
        )

        assert response.text == "元気だよ！"
        mock_prompt_builder.build_final_reply_prompt.assert_not_called()

    @pytest.mark.asyncio
    async def test_when_the_rewrite_fails_the_draft_is_spoken_unless_it_disagrees(
        self, use_case, mock_text_generator
    ):
        mock_text_generator.generate.side_effect = TextGenerationError("down")
        mock_text_generator.generate_json.return_value = {"reply": "夜が明けてからやるね", **CHEST_NOW}
        said = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="チェスト置いて", session=_night(), kind="request")
        )
        assert said.text == "夜が明けてからやるね"

        mock_text_generator.generate_json.return_value = {"reply": "今すぐ置くね", **CHEST_NOW}
        fallback = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="チェスト置いて", session=_night(), kind="request")
        )
        assert fallback.text.startswith("nekoさん、「家にチェストを置く」は夜の間は")


class TestARequestThatIsAlreadyTrue:
    """
    頼まれた条件がもう満たされているときは、大目標にも考え直しにもしない（2026-10-10 22:11: ベッドは家に
    あるのに「ベッドで寝て」を大目標にして「今やるね」と言い、46 秒後に達成した）。
    """

    @pytest.mark.asyncio
    async def test_it_is_told_as_already_so_and_nothing_is_added_or_cut(
        self, use_case, mock_text_generator, mock_prompt_builder, bridge
    ):
        async def met(specs):
            return [ConditionStatus(s, True) for s in specs]

        bridge.check.side_effect = met
        mock_text_generator.generate_json.return_value = {"reply": "今やるね", **BED_NOW}
        mock_text_generator.generate.side_effect = [
            "今すぐ置くね！",  # もうあるのに「今すぐ」: 直させる
            "ベッドはもう家にあるよ！夜になったら寝るね。",
        ]
        session = _playing()

        response = await use_case.execute(
            GenerateResponseRequest(user_name="neko", message="ベッドで寝て", session=session, kind="advice")
        )

        assert response.text == "ベッドはもう家にあるよ！夜になったら寝るね。"
        assert response.objective is None and len(session.plan.pending) == 2
        assert not session.rethink_reason and not session.promised_now
        first, second = [c.kwargs for c in mock_prompt_builder.build_final_reply_prompt.call_args_list]
        assert "もう満たしている" in first["facts"] and "昼は寝られない" in first["facts"]
        assert "already the case" in second["previous_error"]
