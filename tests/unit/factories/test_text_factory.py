"""テキスト生成器の Composition Root のテスト（リモートの Gemini / LiteLLM。docs/design/127）。"""

import pytest

pytest.importorskip("google.genai", reason="google-genai not installed")

from ailoveshen.domain.value_objects import Screenshot  # noqa: E402
from ailoveshen.factories.text import create_text_generator  # noqa: E402
from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import (  # noqa: E402
    GeminiTextGenerator,
)
from ailoveshen.infrastructure.adapters.local_llm.openai_compat_text_generator import (  # noqa: E402
    OpenAICompatTextGenerator,
)
from ailoveshen.infrastructure.adapters.local_llm.routing_text_generator import (  # noqa: E402
    RoutingTextGenerator,
)
from ailoveshen.infrastructure.config import (  # noqa: E402
    GeminiSettings,
    LiteLlmSettings,
    LlmSettings,
    LocalLlmSettings,
    RemoteLlmSettings,
)


def _llm(provider="litellm", local=False, model="haiku-5.5", **kwargs):
    return LlmSettings(
        local=LocalLlmSettings(enabled=local, model="qwen"),
        remote=RemoteLlmSettings(provider=provider, litellm=LiteLlmSettings(model=model)),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_gemini_is_the_remote_by_default():
    generator = create_text_generator(GeminiSettings(api_key="k"), LlmSettings())
    assert isinstance(generator, GeminiTextGenerator)
    await generator.close()


@pytest.mark.asyncio
async def test_litellm_replaces_gemini_when_the_local_llm_is_off_and_needs_no_gemini_key():
    generator = create_text_generator(GeminiSettings(api_key=""), _llm())
    assert isinstance(generator, OpenAICompatTextGenerator)
    assert generator.name == "haiku-5.5"
    await generator.close()


@pytest.mark.asyncio
async def test_litellm_is_the_remote_that_gets_the_images_next_to_the_local_llm():
    generator = create_text_generator(GeminiSettings(api_key=""), _llm(local=True))
    assert isinstance(generator, RoutingTextGenerator)
    shot = Screenshot(b"png", "image/png")
    assert generator.backend_for("screen_review", [shot]) == ("remote", "images")
    assert generator.backend_for("reply") == ("local", "route")
    await generator.close()


def test_litellm_needs_a_model_name():
    with pytest.raises(ValueError, match="LITELLM_MODEL"):
        create_text_generator(GeminiSettings(api_key="k"), _llm(model=""))


def test_an_unknown_remote_provider_is_refused():
    with pytest.raises(ValueError, match="provider"):
        create_text_generator(GeminiSettings(api_key="k"), _llm(provider="claude"))


@pytest.mark.asyncio
async def test_a_code_model_does_not_change_the_routes_so_local_routes_stay_local():
    llm = _llm(local=True)
    llm.remote.litellm.models = {"skill_write": "claude-sonnet-5-5"}
    generator = create_text_generator(GeminiSettings(api_key=""), llm)
    assert generator.backend_for("skill_write") == ("local", "route")
    assert generator.backend_for("reply") == ("local", "route")
    await generator.close()

    llm.routes = {"default": "local", "skill_write": "remote"}
    generator = create_text_generator(GeminiSettings(api_key=""), llm)
    assert generator.backend_for("skill_write") == ("remote", "route")
    await generator.close()
