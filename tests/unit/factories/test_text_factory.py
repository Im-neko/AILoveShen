"""テキスト生成器の Composition Root のテスト（リモートは LiteLLM 経由のモデル。docs/design/127）。"""

import pytest

from ailoveshen.domain.value_objects import Screenshot
from ailoveshen.factories.text import create_text_generator
from ailoveshen.infrastructure.adapters.local_llm.openai_compat_text_generator import (
    OpenAICompatTextGenerator,
)
from ailoveshen.infrastructure.adapters.local_llm.routing_text_generator import (
    RoutingTextGenerator,
)
from ailoveshen.infrastructure.config import LiteLlmSettings, LlmSettings, LocalLlmSettings


def _llm(local=False, model="haiku-5.5", **kwargs):
    return LlmSettings(
        local=LocalLlmSettings(enabled=local, model="qwen"),
        litellm=LiteLlmSettings(model=model),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_litellm_is_the_only_model_when_the_local_llm_is_off():
    generator = create_text_generator(_llm())
    assert isinstance(generator, OpenAICompatTextGenerator)
    assert generator.name == "haiku-5.5"
    await generator.close()


@pytest.mark.asyncio
async def test_litellm_is_the_remote_that_gets_the_images_next_to_the_local_llm():
    generator = create_text_generator(_llm(local=True))
    assert isinstance(generator, RoutingTextGenerator)
    shot = Screenshot(b"png", "image/png")
    assert generator.backend_for("screen_review", [shot]) == ("remote", "images")
    assert generator.backend_for("reply") == ("local", "route")
    await generator.close()


def test_litellm_needs_a_model_name():
    with pytest.raises(ValueError, match="LITELLM_MODEL"):
        create_text_generator(_llm(model=""))


@pytest.mark.asyncio
async def test_the_local_llm_works_alone_without_a_remote_model():
    generator = create_text_generator(_llm(local=True, model=""))
    assert isinstance(generator, RoutingTextGenerator)
    await generator.close()


@pytest.mark.asyncio
async def test_a_code_model_does_not_change_the_routes_so_local_routes_stay_local():
    llm = _llm(local=True)
    llm.litellm.models = {"skill_write": "claude-sonnet-5-5"}
    generator = create_text_generator(llm)
    assert generator.backend_for("skill_write") == ("local", "route")
    assert generator.backend_for("reply") == ("local", "route")
    await generator.close()

    llm.routes = {"default": "local", "skill_write": "remote"}
    generator = create_text_generator(llm)
    assert generator.backend_for("skill_write") == ("remote", "route")
    await generator.close()
