"""テキスト生成器を作る: リモート（Gemini か LiteLLM）だけか、ローカルの LLM とリモートの振り分け（docs/design/36、127）。"""

from __future__ import annotations

from collections.abc import Mapping

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
from ailoveshen.infrastructure.config import GeminiSettings, LiteLlmSettings, LlmSettings

REMOTE_PROVIDERS = ("gemini", "litellm")


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


def create_litellm_generator(
    litellm: LiteLlmSettings,
    thinking_levels: Mapping[str, str],
    default_thinking_level: str,
    generation_log: IGenerationLog | None = None,
) -> OpenAICompatTextGenerator:
    """LiteLLM（OpenAI 互換のプロキシ）経由のモデル。画像も送る。

    思考は用途ごとに決まる（Gemini の thinking_levels が medium / high の用途は思考あり）。

    Raises:
        ValueError: モデル名が空のとき（プロキシの最初のモデルが目当てのモデルとは限らない）
    """
    if not litellm.model:
        raise ValueError("llm.remote.litellm.model (LITELLM_MODEL) is empty: name the model LiteLLM serves")
    return OpenAICompatTextGenerator(
        base_url=litellm.base_url,
        model=litellm.model,
        api_key=litellm.api_key,
        max_output_tokens=litellm.max_output_tokens,
        context_tokens=litellm.context_tokens,
        response_format=litellm.response_format,
        thinking_param=litellm.thinking_param,
        thinking_levels=thinking_levels,
        default_thinking_level=default_thinking_level,
        max_concurrent=litellm.max_concurrent,
        timeout_seconds=litellm.timeout_seconds,
        generation_log=generation_log,
        label="LiteLLM",
        images=True,
        models=litellm.models,
        prompt_cache=litellm.prompt_cache,
    )


def create_text_generator(
    gemini: GeminiSettings,
    llm: LlmSettings | None = None,
    generation_log: IGenerationLog | None = None,
) -> ITextGenerator:
    """
    ローカルの LLM が無効ならリモート（Gemini か LiteLLM）。有効なら用途で振り分ける（画像はいつもリモート）。
    リモートが Gemini でキーがなければ、画像を外してローカルだけで動く。

    Raises:
        ValueError: ローカルが無効でリモートが使えないとき（Gemini のキーがない、LiteLLM のモデル名がない）、
            設定の値が使えないとき
    """
    provider = llm.remote.provider if llm is not None else "gemini"
    if provider not in REMOTE_PROVIDERS:
        raise ValueError(f"llm.remote.provider must be one of {REMOTE_PROVIDERS}, got {provider!r}")
    if llm is None or not llm.local.enabled:
        if provider == "litellm":
            logger.info(f"LLM: LiteLLM {llm.remote.litellm.base_url}（{llm.remote.litellm.model}）")
            return create_litellm_generator(
                llm.remote.litellm, gemini.thinking_levels, gemini.main_thinking_level, generation_log
            )
        return create_gemini_generator(gemini, generation_log)
    local = llm.local
    local_generator = OpenAICompatTextGenerator(
        base_url=local.base_url,
        model=local.model,
        api_key=local.api_key,
        max_output_tokens=local.max_output_tokens,
        context_tokens=local.context_tokens,
        response_format=local.response_format,
        thinking_param=local.thinking_param,
        thinking_levels=gemini.thinking_levels,
        default_thinking_level=gemini.main_thinking_level,
        thinking_purposes=local.thinking_purposes,
        thinking_max_tokens=local.thinking_max_tokens,
        output_max_tokens=local.output_max_tokens,
        max_concurrent=local.max_concurrent,
        timeout_seconds=local.timeout_seconds,
        generation_log=generation_log,
    )
    if provider == "litellm":
        remote = create_litellm_generator(
            llm.remote.litellm, gemini.thinking_levels, gemini.main_thinking_level, generation_log
        )
        remote_name = f"LiteLLM {llm.remote.litellm.base_url}（{llm.remote.litellm.model}）"
    else:
        remote = create_gemini_generator(gemini, generation_log) if gemini.api_key else None
        remote_name = f"Gemini {gemini.main_model}"
        if remote is None:
            logger.warning("Gemini のキーがない: 画像は送らず、すべてローカルの LLM で")
    logger.info(
        f"LLM: ローカル {local.base_url}（{local.model or '/v1/models の最初'}）、"
        f"振り分け {llm.routes}、リモート {remote_name}（画像・長すぎる呼び出し）、失敗したとき {llm.fallback}"
    )
    return RoutingTextGenerator(
        local_generator,
        remote,
        routes=llm.routes,
        context_tokens=local.context_tokens,
        fallback=llm.fallback,
    )
