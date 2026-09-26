"""
ローカルの LLM（OpenAI 互換の /v1/chat/completions: FreeToken、Ollama、llama.cpp、vLLM）で
テキストを生成するアダプター（docs/design/36 §3）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from loguru import logger

from ailoveshen.application.ports.output.generation_log import IGenerationLog
from ailoveshen.application.ports.output.text_generator import (
    ITextGenerator,
    ToolChoice,
    ToolSpec,
)
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import Screenshot
from ailoveshen.infrastructure.adapters.local_llm.schema_check import (
    check,
    extract_json,
    strip_thinking,
)

THINKING_PARAMS = ("chat_template_kwargs", "none")
RESPONSE_FORMATS = ("auto", "off")
THINKING_ON = ("medium", "high")  # Gemini の thinking_levels のうち、ローカルで思考させる深さ
SCHEMA_ERRORS_SHOWN = 8


class OpenAICompatTextGenerator(ITextGenerator):
    """
    OpenAI 互換のサーバーのインフラ側アダプター。画像は受け付けない（振り分けで Gemini に回す）。

    - JSON: スキーマをいつもプロンプトに書き、`response_format: json_schema` も送る（断られたら
      以後送らない）。答えをスキーマの主な決まりで確かめ、違えば 1 回だけ書き直させる
    - 道具: `tools` と `tool_choice: required`（断られたら auto）。道具を呼ばなければ、道具の名前と
      引数の JSON で選ばせる
    - 考える深さ: 用途の thinking_level が medium / high なら思考あり
    """

    def __init__(
        self,
        base_url: str,
        model: str = "",
        api_key: str = "",
        max_output_tokens: int = 4096,
        response_format: str = "auto",
        thinking_param: str = "chat_template_kwargs",
        thinking_levels: Optional[Mapping[str, str]] = None,
        default_thinking_level: str = "low",
        max_concurrent: int = 2,
        timeout_seconds: float = 180.0,
        generation_log: Optional[IGenerationLog] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        """
        Args:
            base_url: 例 http://127.0.0.1:1919/v1
            model: モデル ID（空: /v1/models の最初）
            api_key: 要るサーバーだけ
            max_output_tokens: 出力の上限（思考を含む）
            response_format: "auto"（json_schema を送り、断られたらやめる）か "off"
            thinking_param: "chat_template_kwargs"（{"enable_thinking": …}）か "none"
            thinking_levels: 用途ごとの深さ（Gemini の表をそのまま使う）
            default_thinking_level: 表にない用途の深さ
            max_concurrent: 同時に送る数の上限
            timeout_seconds: 1 回の上限
            generation_log: デバッグの記録の残し先
            transport: テスト用
        """
        if response_format not in RESPONSE_FORMATS:
            raise ValueError(f"response_format must be one of {RESPONSE_FORMATS}")
        if thinking_param not in THINKING_PARAMS:
            raise ValueError(f"thinking_param must be one of {THINKING_PARAMS}")
        self._model = model
        self._max_output = max_output_tokens
        self._use_response_format = response_format == "auto"
        self._tool_choice = "required"
        self._thinking_param = thinking_param
        self._levels = {"default": default_thinking_level, **dict(thinking_levels or {})}
        self._slots = asyncio.Semaphore(max(1, max_concurrent))
        self._log = generation_log
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            transport=transport,
        )
        self._model_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._model or "local"

    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> str:
        message = await self._chat(_messages(prompt, system_instruction), purpose)
        return strip_thinking(message.get("content") or "")

    async def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
        images: Sequence[Screenshot] = (),
    ) -> dict[str, Any]:
        _no_images(images)
        messages = _messages(_with_schema(prompt, schema), system_instruction)
        message = await self._chat(messages, purpose, schema=schema)
        text = message.get("content") or ""
        try:
            data = extract_json(text)
            errors = check(data, schema)
        except ValueError as e:
            data, errors = None, [str(e)]
        if errors:
            logger.info(f"ローカルの JSON を書き直させる（{purpose}）: {'; '.join(errors[:3])}")
            messages += [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": "That answer does not follow the JSON Schema: "
                    + "; ".join(errors[:SCHEMA_ERRORS_SHOWN])
                    + ". Answer again with one JSON object only.",
                },
            ]
            message = await self._chat(messages, purpose, schema=schema)
            try:
                data = extract_json(message.get("content") or "")
            except ValueError as e:
                raise TextGenerationError(f"local model gave no JSON object: {e}") from e
            left = check(data, schema)
            if left:
                # 残りは呼び出し側の確かめ（使えない決定の差し戻し）に任せる
                logger.warning(f"ローカルの JSON がまだスキーマに合わない（{purpose}）: {left[:3]}")
        if not isinstance(data, dict):
            raise TextGenerationError("local model's answer is not a JSON object")
        return data

    async def choose_tool(
        self,
        prompt: str,
        tools: Sequence[ToolSpec],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
        images: Sequence[Screenshot] = (),
    ) -> ToolChoice:
        _no_images(images)
        messages = _messages(prompt, system_instruction)
        message = await self._chat(messages, purpose, tools=tools)
        calls = message.get("tool_calls") or []
        if calls:
            function = calls[0].get("function") or {}
            args = function.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {}
            return ToolChoice(name=str(function.get("name") or ""), args=dict(args))
        # 道具を呼ばずに文で答えた: 道具の名前と引数の JSON で選ばせる
        logger.info(f"ローカルが道具を呼ばなかったので、JSON で選ばせる（{purpose}）")
        data = await self.generate_json(
            _with_tools(prompt, tools),
            {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": [t.name for t in tools]},
                    "args": {"type": "object", "description": "the tool's arguments"},
                },
                "required": ["tool", "args"],
            },
            system_instruction,
            purpose,
        )
        args = data.get("args")
        return ToolChoice(name=str(data.get("tool") or ""), args=args if isinstance(args, dict) else {})

    async def close(self) -> None:
        await self._client.aclose()

    async def _model_id(self) -> str:
        async with self._model_lock:
            if not self._model:
                try:
                    response = await self._client.get("/models")
                    response.raise_for_status()
                    self._model = response.json()["data"][0]["id"]
                except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                    raise TextGenerationError(f"local LLM: could not read /models: {e}") from e
                logger.info(f"ローカルの LLM: {self._model}")
            return self._model

    def _thinking(self, purpose: Optional[str]) -> bool:
        return self._levels.get(purpose or "default", self._levels["default"]) in THINKING_ON

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        purpose: Optional[str],
        schema: Optional[dict[str, Any]] = None,
        tools: Sequence[ToolSpec] = (),
    ) -> dict[str, Any]:
        model = await self._model_id()
        think = self._thinking(purpose)
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": self._max_output,
            "stream": False,
        }
        if self._thinking_param == "chat_template_kwargs":
            body["chat_template_kwargs"] = {"enable_thinking": think}
        if schema is not None and self._use_response_format:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema},
            }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
            body["tool_choice"] = self._tool_choice
        data = await self._post(body, purpose, think)
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise TextGenerationError(f"local LLM: unexpected answer {str(data)[:200]}") from e

    async def _post(self, body: dict[str, Any], purpose: Optional[str], think: bool) -> dict[str, Any]:
        started = time.monotonic()
        async with self._slots:
            try:
                response = await self._client.post("/chat/completions", json=body)
            except httpx.HTTPError as e:
                self._record(body, purpose, think, started, error=str(e))
                raise TextGenerationError(f"local LLM request failed: {e}") from e
        if response.status_code in (400, 422):
            dropped = self._drop_unsupported(body)
            if dropped:
                # 断られた指定を外して送り直す。通れば以後も送らない。通らなければ別の理由だった
                try:
                    data = await self._post(body, purpose, think)
                except TextGenerationError:
                    self._restore(dropped)
                    raise
                logger.warning(
                    f"ローカルの LLM は {dropped} を受け付けない（{response.status_code}）: 以後は送らない"
                )
                return data
        if response.status_code >= 400:
            error = f"local LLM error {response.status_code}: {response.text[:300]}"
            self._record(body, purpose, think, started, error=error)
            raise TextGenerationError(error)
        data = response.json()
        self._log_usage(data, started, purpose, think)
        self._record(body, purpose, think, started, data=data)
        return data

    def _drop_unsupported(self, body: dict[str, Any]) -> str:
        """断られたかもしれない指定を 1 つ外す（外したものの名前。なければ ""）。"""
        if "response_format" in body:
            self._use_response_format = False
            body.pop("response_format")
            return "response_format"
        if body.get("tool_choice") == "required":
            self._tool_choice = "auto"
            body["tool_choice"] = "auto"
            return "tool_choice"
        if "chat_template_kwargs" in body:
            self._thinking_param = "none"
            body.pop("chat_template_kwargs")
            return "chat_template_kwargs"
        return ""

    def _restore(self, dropped: str) -> None:
        if dropped == "response_format":
            self._use_response_format = True
        elif dropped == "tool_choice":
            self._tool_choice = "required"
        elif dropped == "chat_template_kwargs":
            self._thinking_param = "chat_template_kwargs"

    def _log_usage(self, data: dict[str, Any], started: float, purpose: Optional[str], think: bool) -> None:
        """使用量の行（Gemini と同じ形: tools/gemini_usage.py が読む）。"""
        usage = data.get("usage") or {}
        prompt = usage.get("prompt_tokens")
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        thoughts = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        output = usage.get("completion_tokens")
        logger.info(
            f"Local {self.name}（thinking {'on' if think else 'off'}）: "
            f"{int((time.monotonic() - started) * 1000)}ms, "
            f"purpose={purpose or 'default'} images=0 prompt={prompt} cached={cached} "
            f"thoughts={thoughts} output={output} total={usage.get('total_tokens')}"
        )

    def _record(
        self,
        body: dict[str, Any],
        purpose: Optional[str],
        think: bool,
        started: float,
        data: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        if self._log is None:
            return
        messages = body.get("messages", [])
        entry: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "model": f"local:{self.name}",
            "purpose": purpose or "default",
            "thinking_level": "on" if think else "off",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "prompt": messages[-1].get("content", "") if messages else "",
            "images": [],
        }
        if error is not None:
            entry["error"] = error
        if data is not None:
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            entry["thoughts"] = message.get("reasoning_content") or None
            entry["output"] = (message.get("content") or "").strip() or None
            entry["tool_calls"] = [
                {"name": (c.get("function") or {}).get("name"), "args": (c.get("function") or {}).get("arguments")}
                for c in message.get("tool_calls") or []
            ]
            entry["finish_reason"] = choice.get("finish_reason")
            usage = data.get("usage") or {}
            entry["tokens"] = {
                "prompt": usage.get("prompt_tokens"),
                "output": usage.get("completion_tokens"),
            }
        try:
            self._log.record(entry)
        except Exception as e:  # noqa: BLE001 - 記録の失敗で生成を止めない
            logger.debug(f"ローカルの呼び出しを記録できなかった: {e}")


def _messages(prompt: str, system: Optional[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    out.append({"role": "user", "content": prompt})
    return out


def _with_schema(prompt: str, schema: dict[str, Any]) -> str:
    return (
        f"{prompt}\n\nAnswer with one JSON object that follows this JSON Schema exactly (the "
        "descriptions say what to write; no other text):\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


def _with_tools(prompt: str, tools: Sequence[ToolSpec]) -> str:
    listed = [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools]
    return (
        f"{prompt}\n\nCall exactly one of these tools: answer with its name as tool and its "
        f"arguments as args.\n{json.dumps(listed, ensure_ascii=False)}"
    )


def _no_images(images: Sequence[Screenshot]) -> None:
    if images:
        raise TextGenerationError("the local model takes no images (route them to Gemini)")
