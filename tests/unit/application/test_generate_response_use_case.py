"""GenerateResponseUseCase のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
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
    generator.generate.return_value = "nekoさん、ありがとう！"
    return generator


@pytest.fixture
def mock_prompt_builder():
    """プロンプトビルダーのモック（同期メソッド）。"""
    builder = Mock()
    builder.build_system_prompt.return_value = "System prompt"
    builder.build_chat_response_prompt.return_value = "Response prompt"
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
            [True, False],
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
            {"decision": "later", "title": "明るく", "conditions": [], "reason": "r"},
            {
                "decision": "later",
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
            {"decision": "now", "title": "牛", "conditions": beef, "reason": "r"},
            {"reply": "羊さんも倒してくるね！", "request": "none"},
            {
                "decision": "now",
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
