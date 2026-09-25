"""LLMService のテスト。"""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryResponse,
    GenerateResponseResponse,
)
from ailoveshen.domain.value_objects import (
    Activity,
    EmotionState,
    EmotionType,
    Goal,
    GoalPredicate,
    GoalSpec,
)
from ailoveshen.presentation.services.llm_service import LLMService

ACTIVITY = Activity(goal=Goal(GoalSpec(GoalPredicate.BUILT), reason="家を建てる"))


@pytest.fixture
def commentary_use_case():
    """実況のユースケースのモック。"""
    use_case = AsyncMock()
    use_case.execute.return_value = GenerateCommentaryResponse.ok("洞窟だ！")
    return use_case


@pytest.fixture
def response_use_case():
    """コメントへの返答のユースケースのモック。"""
    use_case = AsyncMock()
    use_case.execute.return_value = GenerateResponseResponse.ok("やっほー", "hi", "neko")
    return use_case


@pytest.fixture
def text_generator():
    """テキスト生成のモック。"""
    return AsyncMock()


@pytest.fixture
def service(commentary_use_case, response_use_case, text_generator):
    """ユースケースをモックにした LLMService。"""
    return LLMService(
        generate_commentary_use_case=commentary_use_case,
        generate_response_use_case=response_use_case,
        text_generator=text_generator,
    )


class TestLLMService:
    """LLMService のテスト。"""

    @pytest.mark.asyncio
    async def test_generate_commentary(self, service, commentary_use_case):
        """実況のリクエストは、引数と今の感情から作る。"""
        happy = EmotionState(EmotionType.HAPPY, 0.8)
        service.update_emotion(happy)

        text = await service.generate_commentary(
            recent_events=["ゾンビを倒した"],
            activity=ACTIVITY,
        )

        assert text == "洞窟だ！"
        request = commentary_use_case.execute.call_args.args[0]
        assert request.emotion_state == happy
        assert request.recent_events == ["ゾンビを倒した"]
        assert request.activity is ACTIVITY

    @pytest.mark.asyncio
    async def test_generate_commentary_failure_returns_empty(self, service, commentary_use_case):
        """実況に失敗したら空文字列を返す。"""
        commentary_use_case.execute.return_value = GenerateCommentaryResponse.error_response("x")
        assert await service.generate_commentary() == ""

    @pytest.mark.asyncio
    async def test_generate_response(self, service, response_use_case):
        """返答のリクエストは引数から作る。"""
        text = await service.generate_response("neko", "hi", user_id="42")

        assert text == "やっほー"
        request = response_use_case.execute.call_args.args[0]
        assert request.user_name == "neko"
        assert request.message == "hi"
        assert request.user_id == "42"
        assert request.emotion_state == EmotionState()

    @pytest.mark.asyncio
    async def test_generate_response_failure_returns_empty(self, service, response_use_case):
        """返答に失敗したら空文字列を返す。"""
        response_use_case.execute.return_value = GenerateResponseResponse.error_response(
            "x", "hi", "neko"
        )
        assert await service.generate_response("neko", "hi") == ""

    def test_update_emotion(self, service):
        """感情を変えて読み出せる。"""
        sad = EmotionState(EmotionType.SAD, 0.6)
        service.update_emotion(sad)
        assert service.get_current_emotion() == sad

    @pytest.mark.asyncio
    async def test_close(self, service, text_generator):
        """close() はテキスト生成を閉じる。"""
        await service.close()
        text_generator.close.assert_awaited_once()
