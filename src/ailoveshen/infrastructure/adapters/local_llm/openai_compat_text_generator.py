"""
OpenAI 互換の /v1/chat/completions（FreeToken、Ollama、llama.cpp、vLLM、LiteLLM）で
テキストを生成するアダプター（docs/design/36 §3、127）。
"""

from __future__ import annotations

import asyncio
import base64
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
    split_cache_breaks,
    strip_cache_breaks,
)
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.infrastructure.adapters.prompts.common_system import with_common_system
from ailoveshen.domain.value_objects import Screenshot
from ailoveshen.infrastructure.adapters.local_llm.schema_check import (
    check,
    drop_empty,
    estimate_tokens,
    extract_json,
    strip_thinking,
)

THINKING_PARAMS = ("chat_template_kwargs", "litellm", "none")
RESPONSE_FORMATS = ("auto", "off")
THINKING_ON = ("medium", "high")  # thinking_levels のうち、思考させる深さ（ローカルと Claude）
REASONING_EFFORTS = ("low", "medium", "high")  # Claude 以外の LiteLLM のモデル（Gemini 3.8）が受ける reasoning_effort
SCHEMA_ERRORS_SHOWN = 8
IMAGE_TOKENS = 1600  # 画像 1 枚の見積もり（data: URL の長さではなく）
EPHEMERAL = {"type": "ephemeral"}
MAX_CACHE_MARKS = 4  # Claude は 1 リクエストに 4 つまで


class OpenAICompatTextGenerator(ITextGenerator):
    """
    OpenAI 互換のサーバーのインフラ側アダプター。画像は `images=True`（LiteLLM 経由の Claude など
    画像を読めるモデル）のときだけ受け付ける。ローカルは画像なしで動かすので、振り分けでリモートに回す。

    - JSON: スキーマをいつもプロンプトに書き、`response_format: json_schema` も送る（断られた
      用途では以後送らない。スキーマで通る・通らないが分かれる）。答えをスキーマの主な決まりで確かめ、違えば 1 回だけ書き直させる
    - 道具: `tools` と `tool_choice: required`（断られたら auto）。道具を呼ばなければ、道具の名前と
      引数の JSON で選ばせる
    - 考える深さ: 用途の thinking_level が medium / high なら思考あり。渡し方は thinking_param:
      chat_template_kwargs（ローカルのサーバー）、litellm（LiteLLM 経由。Claude は思考ありなら
      `thinking: adaptive` と `reasoning_effort`、なしなら `thinking: disabled`。それ以外のモデル
      （Gemini 3.8）は思考を切れないので、深さをそのまま `reasoning_effort`（low / medium / high）で
      送る。モデル名が claude で始まるかで分ける）、none
    - モデル: 用途ごとに変えられる（models。コードを書く用途だけ上のモデルにする）
    - キャッシュ: prompt_cache で、モデル名が claude で始まる呼び出しのとき、system と、プロンプトの
      CACHE_BREAK の手前に `cache_control`（Claude のプロンプトキャッシュ。docs/design/127 §5）。
      そうでなければ印を取り除くだけ（Gemini に付けると、暗黙のキャッシュが止まる）
    """

    def __init__(
        self,
        base_url: str,
        model: str = "",
        api_key: str = "",
        max_output_tokens: int = 16384,
        context_tokens: int = 32768,
        response_format: str = "auto",
        thinking_param: str = "chat_template_kwargs",
        thinking_levels: Optional[Mapping[str, str]] = None,
        default_thinking_level: str = "low",
        thinking_purposes: Optional[Sequence[str]] = None,
        thinking_max_tokens: Optional[Mapping[str, int]] = None,
        output_max_tokens: Optional[Mapping[str, int]] = None,
        max_concurrent: int = 2,
        timeout_seconds: float = 180.0,
        generation_log: Optional[IGenerationLog] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        label: str = "Local",
        images: bool = False,
        models: Optional[Mapping[str, str]] = None,
        prompt_cache: bool = False,
    ) -> None:
        """
        Args:
            base_url: 例 http://127.0.0.1:1919/v1
            model: モデル ID（空: /v1/models の最初）
            api_key: 要るサーバーだけ
            max_output_tokens: 出力の上限（思考を含む）。1 回ごとに、コンテキストの残り（見積もり）
                までに減らす
            context_tokens: サーバーの 1 回の最大の長さ
            response_format: "auto"（json_schema を送り、断られた用途ではやめる）か "off"
            thinking_param: "chat_template_kwargs"（{"enable_thinking": …}）、"litellm"（thinking と
                reasoning_effort。LiteLLM 経由のモデル）か "none"
            thinking_levels: 用途ごとの深さ（Gemini の表をそのまま使う）
            default_thinking_level: 表にない用途の深さ
            thinking_purposes: 思考させる用途（与えれば thinking_levels より優先。ローカルは思考が
                遅いので、ふだんの操作の判断は思考なしにする）
            thinking_max_tokens: 思考させる用途ごとの出力の上限（思考を含む）。上限で答えが出なければ
                思考なしで聞き直す（長い思考でプレイが数分止まらないように）
            output_max_tokens: 思考させない用途ごとの出力の上限。書き続けて止まらない呼び出し（返事が
                4,462 トークン、84 秒。ふつうは 200）を、この長さで切る
            max_concurrent: 同時に送る数の上限
            timeout_seconds: 1 回の上限
            generation_log: デバッグの記録の残し先
            transport: テスト用
            label: ログに出す名前（使用量の行の頭: "Local" か "LiteLLM"）
            images: 画像を受け付けるか（画像を読めるモデルだけ）
            models: 用途 → モデル（表にない用途は model）
            prompt_cache: system と CACHE_BREAK の手前に cache_control を付ける（モデル名が claude で
                始まる呼び出しだけ）
        """
        if response_format not in RESPONSE_FORMATS:
            raise ValueError(f"response_format must be one of {RESPONSE_FORMATS}")
        if thinking_param not in THINKING_PARAMS:
            raise ValueError(f"thinking_param must be one of {THINKING_PARAMS}")
        self._model = model
        self._label = label
        self._images = images
        self._prompt_cache = prompt_cache
        self._models = {k: v for k, v in (models or {}).items() if v}
        self._max_output = max_output_tokens
        self._context = context_tokens
        self._use_response_format = response_format == "auto"
        self._no_response_format: set[str] = set()  # response_format を受け付けなかった用途
        self._tool_choice = "required"
        self._thinking_param = thinking_param
        self._levels = {"default": default_thinking_level, **dict(thinking_levels or {})}
        self._think_purposes = set(thinking_purposes) if thinking_purposes is not None else None
        self._think_caps = dict(thinking_max_tokens or {})
        self._output_caps = dict(output_max_tokens or {})
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
        return self._model or self._label.lower()

    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> str:
        message = await self._chat(
            _messages(prompt, with_common_system(system_instruction, purpose), cache=self._cache_on(purpose)), purpose
        )
        return strip_thinking(message.get("content") or "")

    async def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
        images: Sequence[Screenshot] = (),
    ) -> dict[str, Any]:
        self._check_images(images)
        messages = _messages(
            _with_schema(prompt, schema), with_common_system(system_instruction, purpose), images, self._cache_on(purpose)
        )
        message = await self._chat(messages, purpose, schema=schema)
        text = message.get("content") or ""
        try:
            data = drop_empty(extract_json(text), schema)
            errors = check(data, schema)
        except ValueError as e:
            data, errors = None, [str(e)]
        if errors:
            logger.info(f"{self._label} の JSON を書き直させる（{purpose}）: {'; '.join(errors[:3])}")
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
                data = drop_empty(extract_json(message.get("content") or ""), schema)
            except ValueError as e:
                raise TextGenerationError(f"{self._label} gave no JSON object: {e}") from e
            left = check(data, schema)
            if left:
                # 残りは呼び出し側の確かめ（使えない決定の差し戻し）に任せる
                logger.warning(f"{self._label} の JSON がまだスキーマに合わない（{purpose}）: {left[:3]}")
        if not isinstance(data, dict):
            raise TextGenerationError(f"{self._label}'s answer is not a JSON object")
        return data

    async def choose_tool(
        self,
        prompt: str,
        tools: Sequence[ToolSpec],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
        images: Sequence[Screenshot] = (),
    ) -> ToolChoice:
        self._check_images(images)
        messages = _messages(prompt, with_common_system(system_instruction, purpose), images, self._cache_on(purpose))
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
        logger.info(f"{self._label} が道具を呼ばなかったので、JSON で選ばせる（{purpose}）")
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
            images,
        )
        args = data.get("args")
        return ToolChoice(name=str(data.get("tool") or ""), args=args if isinstance(args, dict) else {})

    def _check_images(self, images: Sequence[Screenshot]) -> None:
        if images and not self._images:
            raise TextGenerationError(f"{self._label} takes no images (route them to the remote model)")

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def _model_id(self) -> str:
        async with self._model_lock:
            if not self._model:
                try:
                    response = await self._client.get("/models")
                    response.raise_for_status()
                    self._model = response.json()["data"][0]["id"]
                except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                    raise TextGenerationError(f"{self._label} LLM: could not read /models: {e}") from e
                logger.info(f"{self._label} の LLM: {self._model}")
            return self._model

    def _output_budget(self, messages: list[dict[str, Any]], tools: Sequence[ToolSpec]) -> int:
        """出力の上限: 設定の値か、コンテキストの残り（見積もり）の小さい方（最低 1024）。"""
        used = sum(estimate_tokens(_text(m.get("content"))) + IMAGE_TOKENS * len(_images_in([m])) for m in messages)
        used += sum(estimate_tokens(json.dumps(t.parameters)) + estimate_tokens(t.description) for t in tools)
        return max(1024, min(self._max_output, self._context - used))

    def _thinking(self, purpose: Optional[str]) -> bool:
        if self._think_purposes is not None:
            return (purpose or "default") in self._think_purposes
        return self._levels.get(purpose or "default", self._levels["default"]) in THINKING_ON

    def _cache_on(self, purpose: Optional[str]) -> bool:
        """この用途の呼び出しに cache_control を付けるか（Claude のモデルだけ）。"""
        return self._prompt_cache and _is_claude(self._models.get(purpose or "default") or self._model)

    def _effort(self, purpose: Optional[str]) -> Optional[str]:
        """思考させるときの深さ（Gemini の表の medium / high をそのまま使う）。"""
        level = self._levels.get(purpose or "default", self._levels["default"])
        return level if level in THINKING_ON else None

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        purpose: Optional[str],
        schema: Optional[dict[str, Any]] = None,
        tools: Sequence[ToolSpec] = (),
        allow_thinking: bool = True,
    ) -> dict[str, Any]:
        model = self._models.get(purpose or "default") or await self._model_id()
        think = allow_thinking and self._thinking(purpose)
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": self._max_output,
            "stream": False,
        }
        if self._thinking_param == "chat_template_kwargs":
            body["chat_template_kwargs"] = {"enable_thinking": think}
        elif self._thinking_param == "litellm":
            if _is_claude(model):
                body["thinking"] = {"type": "adaptive" if think else "disabled"}
                if think and (effort := self._effort(purpose)):
                    body["reasoning_effort"] = effort
            elif (level := self._levels.get(purpose or "default", self._levels["default"])) in REASONING_EFFORTS:
                body["reasoning_effort"] = level
        if (
            schema is not None
            and self._use_response_format
            and (purpose or "default") not in self._no_response_format
        ):
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
        body["max_tokens"] = self._output_budget(messages, tools)
        cap = self._think_caps.get(purpose or "default")
        if think and cap:
            body["max_tokens"] = min(body["max_tokens"], cap)
        elif not think and (out_cap := self._output_caps.get(purpose or "default")):
            body["max_tokens"] = min(body["max_tokens"], out_cap)
        data = await self._post(body, purpose, think)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise TextGenerationError(f"{self._label} LLM: unexpected answer {str(data)[:200]}") from e
        if (
            choice.get("finish_reason") == "length"
            and not (message.get("content") or "").strip()
            and not message.get("tool_calls")
        ):
            if think and cap:
                # 上限（thinking_max_tokens）を思考で使い切った: 思考なしで聞き直す。上限を決めていない用途は、
                # 同じことを頼み直しても同じなのでエラーにする
                logger.warning(
                    f"{self._label} の思考が上限（{body['max_tokens']} トークン）で答えに届かなかった: "
                    f"思考なしで聞き直す（{purpose}）"
                )
                return await self._chat(messages, purpose, schema, tools, allow_thinking=False)
            raise TextGenerationError(
                f"{self._label} LLM output was cut at {body['max_tokens']} tokens with no answer "
                f"({purpose}): raise max_output_tokens or think less for this purpose"
            )
        return message

    async def _post(self, body: dict[str, Any], purpose: Optional[str], think: bool) -> dict[str, Any]:
        started = time.monotonic()
        timed_out: Optional[TextGenerationError] = None
        try:
            response = await self._send(body, purpose, think, started)
        except TextGenerationError as e:
            if "response_format" not in body or not isinstance(e.__cause__, httpx.ReadTimeout):
                raise
            # スキーマが重くて返事が来ない（LiteLLM 経由の Claude で、入れ子の深いスキーマが 2 分止まった）
            timed_out, response = e, None
        if response is None or self._may_be_refused(response, body):
            # 断られたかもしれない指定を 1 つずつ外して試す。通った指定だけを、その用途で以後送らない
            fields = ["response_format"] if response is None or response.status_code >= 500 else self._optional_fields(body)
            for field in fields:
                trial = self._without(body, field)
                retry = await self._send(trial, purpose, think, started)
                if retry.status_code in (400, 422):
                    continue
                if retry.status_code < 400:
                    self._drop(field, purpose)
                    why = "返事が来ない" if response is None else response.status_code
                    where = f"（{purpose or 'default'}）" if field == "response_format" else ""
                    logger.warning(f"{self._label} は{where} {field} を受け付けない（{why}）: 以後は送らない")
                response = retry
                break
        if response is None:
            raise timed_out  # type: ignore[misc]
        if response.status_code >= 400:
            error = f"{self._label} LLM error {response.status_code}: {response.text[:300]}"
            self._record(body, purpose, think, started, error=error)
            raise TextGenerationError(error)
        data = response.json()
        self._log_usage(data, body, started, purpose, think)
        self._record(body, purpose, think, started, data=data)
        return data

    @staticmethod
    def _may_be_refused(response: httpx.Response, body: dict[str, Any]) -> bool:
        if response.status_code in (400, 422):
            return True
        return response.status_code >= 500 and "response_format" in body

    async def _send(
        self, body: dict[str, Any], purpose: Optional[str], think: bool, started: float
    ) -> httpx.Response:
        async with self._slots:
            try:
                return await self._client.post("/chat/completions", json=body)
            except httpx.HTTPError as e:
                # 読み取りの時間切れは str(e) が空になる: 種類と待った秒数を書く
                why = f"{type(e).__name__} after {time.monotonic() - started:.0f}s: {e}".rstrip(": ")
                self._record(body, purpose, think, started, error=why)
                raise TextGenerationError(f"{self._label} LLM request failed: {why}") from e

    @staticmethod
    def _optional_fields(body: dict[str, Any]) -> list[str]:
        """サーバーが知らないかもしれない指定（外しても答えは出る）。"""
        fields = [f for f in ("response_format", "chat_template_kwargs") if f in body]
        if body.get("tool_choice") == "required":
            fields.append("tool_choice")
        return fields

    @staticmethod
    def _without(body: dict[str, Any], field: str) -> dict[str, Any]:
        trial = dict(body)
        if field == "tool_choice":
            trial["tool_choice"] = "auto"
        else:
            trial.pop(field)
        return trial

    def _drop(self, field: str, purpose: Optional[str]) -> None:
        if field == "response_format":
            self._no_response_format.add(purpose or "default")
        elif field == "tool_choice":
            self._tool_choice = "auto"
        elif field == "chat_template_kwargs":
            self._thinking_param = "none"

    def _log_usage(
        self, data: dict[str, Any], body: dict[str, Any], started: float, purpose: Optional[str], think: bool
    ) -> None:
        """使用量の行（Gemini と同じ形: tools/gemini_usage.py が読む）。"""
        usage = data.get("usage") or {}
        prompt = usage.get("prompt_tokens")
        cached, written = _cache_tokens(usage)
        thoughts = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        output = usage.get("completion_tokens")
        logger.info(
            f"{self._label} {body.get('model') or self.name}（thinking {'on' if think else 'off'}）: "
            f"{int((time.monotonic() - started) * 1000)}ms, "
            f"purpose={purpose or 'default'} images={len(_images_in(body.get('messages', [])))} prompt={prompt} cached={cached} "
            f"thoughts={thoughts} output={output} total={usage.get('total_tokens')} cache_write={written}"
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
            "model": f"{self._label.lower()}:{body.get('model') or self.name}",
            "purpose": purpose or "default",
            "thinking_level": "on" if think else "off",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "system": _text(messages[0].get("content")) if messages and messages[0].get("role") == "system" else None,
            "prompt": _text(messages[-1].get("content", "")) if messages else "",
            "tools": [(t.get("function") or {}).get("name") for t in body.get("tools") or []],
            # 画像そのものは入れない（記録は 2 秒ごとに読まれる）。id で別に取れる
            "images": [
                {"id": self._log.record_image(data, mime), "mime_type": mime, "bytes": len(data)}
                for mime, data in _images_in(messages)
            ],
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
            cached, written = _cache_tokens(usage)
            entry["tokens"] = {
                "prompt": usage.get("prompt_tokens"),
                "output": usage.get("completion_tokens"),
                "cached": cached,
                "cache_write": written,
            }
        try:
            self._log.record(entry)
        except Exception as e:  # noqa: BLE001 - 記録の失敗で生成を止めない
            logger.debug(f"{self._label} の呼び出しを記録できなかった: {e}")


def _is_claude(model: str) -> bool:
    """LiteLLM のモデル名が Claude か（claude-haiku-5-5、anthropic/claude-…）。"""
    return model.rsplit("/", 1)[-1].lower().startswith("claude")


def _cache_tokens(usage: Mapping[str, Any]) -> tuple[int, int]:
    """キャッシュから読んだトークンと、書き込んだトークン（Claude の cache_control。LiteLLM が返す）。"""
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens") or usage.get("cache_read_input_tokens") or 0
    written = usage.get("cache_creation_input_tokens") or details.get("cache_creation_tokens") or 0
    return int(cached), int(written)


def _messages(
    prompt: str, system: Optional[str], images: Sequence[Screenshot] = (), cache: bool = False
) -> list[dict[str, Any]]:
    """メッセージ。cache のとき、system の終わりと、プロンプトの CACHE_BREAK の手前に cache_control を付ける。"""
    out: list[dict[str, Any]] = []
    marks = MAX_CACHE_MARKS
    if system:
        if cache:
            out.append({"role": "system", "content": [{"type": "text", "text": system, "cache_control": EPHEMERAL}]})
            marks -= 1
        else:
            out.append({"role": "system", "content": system})
    segments = split_cache_breaks(prompt) if cache else [strip_cache_breaks(prompt)]
    parts: list[dict[str, Any]] = []
    for i, segment in enumerate(segments):
        part: dict[str, Any] = {"type": "text", "text": segment}
        if cache and i < len(segments) - 1 and marks > 0:
            part["cache_control"] = EPHEMERAL
            marks -= 1
        parts.append(part)
    parts += [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{i.mime_type};base64,{base64.b64encode(i.data).decode('ascii')}"},
        }
        for i in images
    ]
    plain = len(parts) == 1 and "cache_control" not in parts[0]
    out.append({"role": "user", "content": segments[0] if plain else parts})
    return out


def _text(content: Any) -> str:
    """メッセージの本文（画像つきは部品の並びなので、文の部品だけ）。"""
    if isinstance(content, str):
        return content
    return "".join(str(p.get("text") or "") for p in content or [] if p.get("type") == "text")


def _images_in(messages: Sequence[Mapping[str, Any]]) -> list[tuple[str, bytes]]:
    """メッセージに添えた画像（mime と中身）。"""
    found: list[tuple[str, bytes]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) or not content:
            continue
        for part in content:
            url = (part.get("image_url") or {}).get("url") if part.get("type") == "image_url" else None
            if url and url.startswith("data:") and ";base64," in url:
                head, _, payload = url.partition(";base64,")
                found.append((head[len("data:"):], base64.b64decode(payload)))
    return found


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

