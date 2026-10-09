"""テキスト生成器を作る: リモート（LiteLLM 経由のモデル）だけか、ローカルの LLM とリモートの振り分け（docs/design/36、127）。"""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.ports.output.generation_log import IGenerationLog
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.infrastructure.adapters.local_llm.openai_compat_text_generator import (
    OpenAICompatTextGenerator,
)
from ailoveshen.infrastructure.adapters.local_llm.routing_text_generator import (
    RoutingTextGenerator,
)
from ailoveshen.infrastructure.config import LlmSettings


def create_litellm_generator(
    llm: LlmSettings, generation_log: IGenerationLog | None = None
) -> OpenAICompatTextGenerator:
    """LiteLLM（OpenAI 互換のプロキシ）経由のモデル。画像も送る。

    思考は用途ごとに決まる（thinking_levels が medium / high の用途は思考あり）。思考の指定と
    プロンプトキャッシュは、モデル名が claude で始まる呼び出しにだけ付く（アダプターが決める）。

    Raises:
        ValueError: モデル名が空のとき（プロキシの最初のモデルが目当てのモデルとは限らない）
    """
    litellm = llm.litellm
    if not litellm.model:
        raise ValueError("llm.litellm.model (LITELLM_MODEL) is empty: name the model LiteLLM serves")
    return OpenAICompatTextGenerator(
        base_url=litellm.base_url,
        model=litellm.model,
        api_key=litellm.api_key,
        max_output_tokens=litellm.max_output_tokens,
        context_tokens=litellm.context_tokens,
        response_format=litellm.response_format,
        thinking_param="litellm",
        thinking_levels=llm.thinking_levels,
        default_thinking_level=llm.default_thinking_level,
        max_concurrent=litellm.max_concurrent,
        timeout_seconds=litellm.timeout_seconds,
        generation_log=generation_log,
        label="LiteLLM",
        images=True,
        models=litellm.models,
        prompt_cache=True,
    )


def create_text_generator(
    llm: LlmSettings | None = None,
    generation_log: IGenerationLog | None = None,
) -> ITextGenerator:
    """
    ローカルの LLM が無効なら LiteLLM のモデルだけ。有効なら用途で振り分ける（画像はいつもリモート）。
    リモートのモデル名がなければ、ローカルだけで動く（画像は外して送る）。

    Raises:
        ValueError: ローカルが無効でリモートのモデル名もないとき、設定の値が使えないとき
    """
    llm = llm or LlmSettings()
    if not llm.local.enabled:
        logger.info(f"LLM: LiteLLM {llm.litellm.base_url}（{llm.litellm.model}）")
        return create_litellm_generator(llm, generation_log)
    local = llm.local
    local_generator = OpenAICompatTextGenerator(
        base_url=local.base_url,
        model=local.model,
        api_key=local.api_key,
        max_output_tokens=local.max_output_tokens,
        context_tokens=local.context_tokens,
        response_format=local.response_format,
        thinking_param=local.thinking_param,
        thinking_levels=llm.thinking_levels,
        default_thinking_level=llm.default_thinking_level,
        thinking_purposes=local.thinking_purposes,
        thinking_max_tokens=local.thinking_max_tokens,
        output_max_tokens=local.output_max_tokens,
        max_concurrent=local.max_concurrent,
        timeout_seconds=local.timeout_seconds,
        generation_log=generation_log,
    )
    remote = create_litellm_generator(llm, generation_log) if llm.litellm.model else None
    if remote is None:
        logger.warning("LITELLM_MODEL がない: 画像は送らず、すべてローカルの LLM で")
    logger.info(
        f"LLM: ローカル {local.base_url}（{local.model or '/v1/models の最初'}）、振り分け {llm.routes}、"
        f"リモート LiteLLM {llm.litellm.base_url}（{llm.litellm.model or 'なし'}。画像・長すぎる呼び出し）、"
        f"失敗したとき {llm.fallback}"
    )
    return RoutingTextGenerator(
        local_generator,
        remote,
        routes=llm.routes,
        context_tokens=local.context_tokens,
        fallback=llm.fallback,
    )
