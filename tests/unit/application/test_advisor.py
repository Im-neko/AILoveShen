"""advisor に意見を聞く（docs/design/143）のテスト。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.advisor import PURPOSE, Advisor
from ailoveshen.application.use_cases.tool_catalog import ASK_ADVISOR, is_query, tool_specs
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.factories.text import has_advisor
from ailoveshen.infrastructure.config import LiteLlmSettings, LlmSettings


def _advisor(answer="木を先に切る"):
    generator = AsyncMock()
    generator.generate.return_value = answer
    builder = Mock()
    builder.build_advice_prompt.return_value = "advice prompt"
    return Advisor(generator, builder), generator, builder


class TestAdvisor:
    @pytest.mark.asyncio
    async def test_the_question_and_what_she_saw_go_to_the_advice_purpose(self):
        advisor, generator, builder = _advisor("  木を先に切る \n")

        ok, text = await advisor.ask(" どれが先？ ", "SITUATION")

        assert (ok, text) == (True, "木を先に切る")
        builder.build_advice_prompt.assert_called_once_with("どれが先？", "SITUATION")
        generator.generate.assert_awaited_once_with("advice prompt", purpose=PURPOSE)
        assert PURPOSE == "advice"

    @pytest.mark.asyncio
    async def test_an_empty_question_is_refused_without_asking(self):
        advisor, generator, _ = _advisor()

        ok, text = await advisor.ask("  ", "SITUATION")

        assert not ok and "question is empty" in text
        generator.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failure_or_an_empty_answer_is_a_failed_lookup_not_an_error(self):
        advisor, generator, _ = _advisor()
        generator.generate.side_effect = TextGenerationError("timeout")
        ok, text = await advisor.ask("q", "s")
        assert not ok and "could not answer" in text and "timeout" in text

        generator.generate.side_effect = None
        generator.generate.return_value = "  "
        ok, text = await advisor.ask("q", "s")
        assert not ok and "no answer" in text


class TestAskAdvisorTool:
    def test_it_is_a_lookup_offered_only_with_an_advisor(self):
        assert ASK_ADVISOR not in {t.name for t in tool_specs()}
        specs = {t.name: t for t in tool_specs(advisor=True)}
        assert specs[ASK_ADVISOR].parameters["required"] == ["question"]
        assert is_query(ASK_ADVISOR)  # 調べものと同じく、1 ステップの回数と同じ問いの繰り返しが数えられる

    def test_the_advisor_needs_a_model_and_a_litellm_to_ask(self):
        def llm(model, advice):
            return LlmSettings(litellm=LiteLlmSettings(model=model, models={"advice": advice} if advice else {}))

        assert has_advisor(llm("claude-haiku-5-5", "claude-sonnet-5-5"))
        assert not has_advisor(llm("claude-haiku-5-5", ""))
        assert not has_advisor(llm("", "claude-sonnet-5-5"))
