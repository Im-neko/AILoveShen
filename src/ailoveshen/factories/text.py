"""テキスト生成器を作る: Gemini だけか、ローカルの LLM と Gemini の振り分け（docs/design/36）。"""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.ports.output.generation_log import IGenerationLog
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import GeminiTextGenerator
from ailoveshen.infrastructure.adapters.local_llm.openai_compat_text_generator import (
    OpenAICompatTextGenerator,
)
from ailoveshen.infrastructure.adapters.local_llm.routing_text_generator import (
    RoutingTextGenerator,
)
from ailoveshen.infrastructure.config import GeminiSettings, LlmSettings


def create_gemini_generator(
    gemini: GeminiSettings, generation_log: IGenerationLog | None = None
) -> GeminiTextGenerator:
    return GeminiTextGenerator(
        api_key=gemini.api_key,
        model=gemini.main_model,
        thinking_level=gemini.main_thinking_level,
        max_output_tokens=gemini.max_output_tokens,
        retry_attempts=gemini.retry.max_attempts,
        retry_initial_delay_seconds=gemini.retry.base_delay_seconds,
        retry_max_delay_seconds=gemini.retry.max_delay_seconds,
        retry_exponential_base=gemini.retry.exponential_base,
        min_request_interval_seconds=gemini.rate_limit.min_interval_seconds,
        thinking_levels=gemini.thinking_levels,
        include_thoughts=gemini.include_thoughts,
        generation_log=generation_log,
        media_resolution=gemini.media_resolution,
        media_resolutions=gemini.media_resolutions,
    )


def create_text_generator(
    gemini: GeminiSettings,
    llm: LlmSettings | None = None,
    generation_log: IGenerationLog | None = None,
) -> ITextGenerator:
    """
    ローカルの LLM が無効なら Gemini。有効なら用途で振り分ける（画像はいつも Gemini）。
    Gemini のキーがなければ、画像を外してローカルだけで動く。

    Raises:
        ValueError: ローカルが無効で Gemini のキーもないとき、設定の値が使えないとき
    """
    if llm is None or not llm.local.enabled:
        return create_gemini_generator(gemini, generation_log)
    local = llm.local
    local_generator = OpenAICompatTextGenerator(
        base_url=local.base_url,
        model=local.model,
        api_key=local.api_key,
        max_output_tokens=local.max_output_tokens,
        response_format=local.response_format,
        thinking_param=local.thinking_param,
        thinking_levels=gemini.thinking_levels,
        default_thinking_level=gemini.main_thinking_level,
        max_concurrent=local.max_concurrent,
        timeout_seconds=local.timeout_seconds,
        generation_log=generation_log,
    )
    remote = create_gemini_generator(gemini, generation_log) if gemini.api_key else None
    if remote is None:
        logger.warning("Gemini のキーがない: 画像は送らず、すべてローカルの LLM で")
    logger.info(
        f"LLM: ローカル {local.base_url}（{local.model or '/v1/models の最初'}）、"
        f"振り分け {llm.routes}、画像は Gemini、失敗したとき {llm.fallback}"
    )
    return RoutingTextGenerator(
        local_generator,
        remote,
        routes=llm.routes,
        context_tokens=local.context_tokens,
        reserve_output_tokens=local.max_output_tokens,
        fallback=llm.fallback,
    )
