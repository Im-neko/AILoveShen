"""
呼び出しごとにローカルの LLM かリモート（Gemini か LiteLLM 経由のモデル）を選ぶ生成器（docs/design/36 §2、127）。

- 用途（purpose）の表で選ぶ（`default` と用途ごと、"local" か "remote"）
- 画像のある呼び出しは必ずリモート（ローカルは画像なしで動かしている）
- ローカルに送る前にトークン数を見積もり、入らなければその 1 回をリモートに回す（切り詰めない）
- ローカルが失敗したとき、`fallback` が "remote" ならリモートで答える（"none" ならエラーのまま）
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.text_generator import (
    ITextGenerator,
    ToolChoice,
    ToolSpec,
)
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import Screenshot
from ailoveshen.infrastructure.adapters.local_llm.schema_check import estimate_tokens

BACKENDS = ("local", "remote")
FALLBACKS = ("none", "remote")


class RoutingTextGenerator(ITextGenerator):
    """ローカルの LLM とリモートを、用途・画像・大きさで振り分ける。"""

    def __init__(
        self,
        local: ITextGenerator,
        remote: Optional[ITextGenerator],
        routes: Optional[Mapping[str, str]] = None,
        context_tokens: int = 32768,
        reserve_output_tokens: int = 2048,
        fallback: str = "none",
    ) -> None:
        """
        Args:
            local: ローカルの LLM
            remote: リモート（Gemini か LiteLLM。画像、入らない大きさ、fallback のとき。None なら画像は外して送る）
            routes: 用途 → "local" / "remote"（"default" は表にない用途）
            context_tokens: ローカルの 1 回のコンテキストの長さ
            reserve_output_tokens: そのうち答えに最低限残す分（思考を含む出力の上限は、ローカルの
                アダプターが 1 回ごとに残りに合わせる）
            fallback: ローカルが失敗したとき "none"（エラー）か "remote"
        """
        routes = {"default": "local", **dict(routes or {})}
        for purpose, backend in routes.items():
            if backend not in BACKENDS:
                raise ValueError(f"llm route for {purpose!r} must be one of {BACKENDS}, got {backend!r}")
        if fallback not in FALLBACKS:
            raise ValueError(f"llm fallback must be one of {FALLBACKS}, got {fallback!r}")
        self._local = local
        self._remote = remote
        self._routes = routes
        self._budget = max(1, context_tokens - reserve_output_tokens)
        self._fallback = fallback
        self._closed = False

    def backend_for(
        self, purpose: Optional[str], images: Sequence[Screenshot] = (), size: int = 0
    ) -> tuple[str, str]:
        """選んだ先と理由（ログ用）。"""
        if images:
            if self._remote is not None:
                return "remote", "images"
            return "local", "images dropped: no remote"
        backend = self._routes.get(purpose or "default", self._routes["default"])
        if backend == "remote" and self._remote is None:
            return "local", "no remote"
        if backend == "local" and size > self._budget and self._remote is not None:
            return "remote", f"too long for the local model (~{size} > {self._budget} tokens)"
        return backend, "route"

    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> str:
        size = _size(prompt, system_instruction)
        backend, why = self._pick(purpose, (), size)
        if backend == "remote":
            assert self._remote is not None
            return await self._remote.generate(prompt, system_instruction, purpose)
        try:
            return await self._local.generate(prompt, system_instruction, purpose)
        except TextGenerationError as e:
            remote = self._fallback_to(purpose, e)
            return await remote.generate(prompt, system_instruction, purpose)

    async def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
        images: Sequence[Screenshot] = (),
    ) -> dict[str, Any]:
        size = _size(prompt, system_instruction, json.dumps(schema, ensure_ascii=False))
        backend, why = self._pick(purpose, images, size)
        if backend == "remote":
            assert self._remote is not None
            return await self._remote.generate_json(prompt, schema, system_instruction, purpose, images)
        try:
            return await self._local.generate_json(prompt, schema, system_instruction, purpose)
        except TextGenerationError as e:
            remote = self._fallback_to(purpose, e)
            return await remote.generate_json(prompt, schema, system_instruction, purpose, images)

    async def choose_tool(
        self,
        prompt: str,
        tools: Sequence[ToolSpec],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
        images: Sequence[Screenshot] = (),
    ) -> ToolChoice:
        listed = json.dumps(
            [{"n": t.name, "d": t.description, "p": t.parameters} for t in tools], ensure_ascii=False
        )
        size = _size(prompt, system_instruction, listed)
        backend, why = self._pick(purpose, images, size)
        if backend == "remote":
            assert self._remote is not None
            return await self._remote.choose_tool(prompt, tools, system_instruction, purpose, images)
        try:
            return await self._local.choose_tool(prompt, tools, system_instruction, purpose)
        except TextGenerationError as e:
            remote = self._fallback_to(purpose, e)
            return await remote.choose_tool(prompt, tools, system_instruction, purpose, images)

    async def close(self) -> None:
        # ゲームと返事で 1 つを共有する（同時に送る数をまとめて数える）: 閉じるのは 1 回
        if self._closed:
            return
        self._closed = True
        await self._local.close()
        if self._remote is not None:
            await self._remote.close()

    def _pick(self, purpose: Optional[str], images: Sequence[Screenshot], size: int) -> tuple[str, str]:
        backend, why = self.backend_for(purpose, images, size)
        if why not in ("route", "images"):
            logger.info(f"LLM の振り分け（{purpose or 'default'}）: {backend}（{why}）")
        return backend, why

    def _fallback_to(self, purpose: Optional[str], error: TextGenerationError) -> ITextGenerator:
        if self._fallback != "remote" or self._remote is None:
            raise error
        logger.warning(f"ローカルの LLM が失敗したのでリモートで（{purpose or 'default'}）: {error}")
        return self._remote


def _size(*parts: Optional[str]) -> int:
    return sum(estimate_tokens(p) for p in parts if p)
