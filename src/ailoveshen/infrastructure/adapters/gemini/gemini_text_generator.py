"""Gemini でテキストを生成するアダプター（google-genai SDK）。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from google import genai
from google.genai import errors, types
from loguru import logger

from ailoveshen.application.ports.output.generation_log import IGenerationLog
from ailoveshen.application.ports.output.text_generator import (
    ITextGenerator,
    ToolChoice,
    ToolSpec,
)
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import Screenshot

# Gemini 3.8 Flash が受け付けるのはこのレベルだけ（"minimal" は API が拒否する）
SUPPORTED_THINKING_LEVELS = ("low", "medium", "high")


class GeminiTextGenerator(ITextGenerator):
    """
    Gemini API のインフラ側アダプター。

    出力ポート ITextGenerator を google-genai SDK で実装する。
    1つのインスタンスが 1つのモデルの枠（例: main、filter）を受け持つ。

    - 408/429/5xx は指数バックオフで再試行する（SDK の再試行の設定）
    - リクエストの間に最小の間隔を空ける（簡単なレート制限）
    - リクエストごとにトークンの使用量と所要時間をログに出す
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash",
        thinking_level: str = "low",
        max_output_tokens: int = 8192,
        retry_attempts: int = 3,
        retry_initial_delay_seconds: float = 1.0,
        retry_max_delay_seconds: float = 10.0,
        retry_exponential_base: float = 2.0,
        min_request_interval_seconds: float = 1.0,
        thinking_levels: Optional[Mapping[str, str]] = None,
        include_thoughts: bool = False,
        generation_log: Optional[IGenerationLog] = None,
        media_resolution: str = "low",
        media_resolutions: Optional[Mapping[str, str]] = None,
    ) -> None:
        """
        Gemini のクライアントを初期化する。

        Args:
            api_key: Gemini の API キー
            model: モデル ID
            thinking_level: "low"、"medium"、"high" のどれか。用途の表にない呼び出しの既定
            max_output_tokens: 出力トークンの上限（思考のトークンを含む）
            retry_attempts: 最初のリクエストを含めた最大の試行回数
            retry_initial_delay_seconds: バックオフの最初の待ち時間
            retry_max_delay_seconds: バックオフの最大の待ち時間
            retry_exponential_base: バックオフの倍率
            min_request_interval_seconds: リクエストの間の最小の間隔
            thinking_levels: 用途（呼び出しの purpose）ごとの thinking_level（設計書 19 §7）。
                "default" があれば thinking_level より優先する
            include_thoughts: 思考の要約も返させる（デバッグ用。generation_log に残す）
            generation_log: 呼び出しごとの記録（用途、深さ、思考の要約、出力、トークン）の残し先
            media_resolution: 画像を添えるときの解像度（"low"、"medium"、"high"）。低いほど
                画像のトークンが少ない（docs/design/23）
            media_resolutions: 用途ごとの解像度（例: 建物の設計の地図は medium。25 §3）

        Raises:
            ValueError: api_key が空か、thinking_level に対応していないとき。
        """
        if not api_key:
            raise ValueError("Gemini API key is required (set GEMINI_API_KEY)")
        levels = {"default": thinking_level, **dict(thinking_levels or {})}
        for purpose, level in levels.items():
            if level not in SUPPORTED_THINKING_LEVELS:
                raise ValueError(
                    f"thinking_level must be one of {SUPPORTED_THINKING_LEVELS}, "
                    f"got {level!r} for {purpose!r}"
                )
        self._levels = levels
        self._include_thoughts = include_thoughts
        resolutions = {
            "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
            "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
            "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        }
        by_purpose = {"default": media_resolution, **dict(media_resolutions or {})}
        for purpose, resolution in by_purpose.items():
            if resolution not in resolutions:
                raise ValueError(
                    f"media_resolution must be one of {list(resolutions)}, "
                    f"got {resolution!r} for {purpose!r}"
                )
        self._media_resolutions = {p: resolutions[r] for p, r in by_purpose.items()}
        self._generation_log = generation_log

        self._model = model
        self._min_interval = min_request_interval_seconds
        self._config = types.GenerateContentConfig(
            max_output_tokens=max_output_tokens,
            thinking_config=self._thinking(levels["default"]),
            # 道具は choose_tool だけが渡し、呼び出しは自分で扱う。自動の関数呼び出しは無効にする
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    attempts=retry_attempts,
                    initial_delay=retry_initial_delay_seconds,
                    max_delay=retry_max_delay_seconds,
                    exp_base=retry_exponential_base,
                ),
            ),
        )
        self._rate_lock = asyncio.Lock()
        self._last_request_at: Optional[float] = None

    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> str:
        """
        Gemini でテキストを生成する。

        Args:
            prompt: ユーザーのプロンプト
            system_instruction: システム指示（キャラクターの設定）
            purpose: 用途の名前（考える深さを決める）

        Returns:
            生成したテキスト。モデルがテキストを返さなかったとき（ブロックされた、
            思考中に出力の上限に達した、など）は空文字列

        Raises:
            TextGenerationError: 再試行しても API の呼び出しが失敗したとき
        """
        config = self._config_for(purpose).model_copy(
            update={"system_instruction": system_instruction}
        )
        return await self._generate_text(prompt, config, purpose)

    async def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
        images: Sequence[Screenshot] = (),
    ) -> dict[str, Any]:
        """
        JSON Schema に沿った JSON オブジェクトを生成する（Gemini の構造化出力）。

        Raises:
            TextGenerationError: API の呼び出しが失敗したか、出力が JSON オブジェクトでないとき
        """
        config = self._config_for(purpose).model_copy(
            update={
                "system_instruction": system_instruction,
                "response_mime_type": "application/json",
                "response_json_schema": schema,
            }
        )
        text = await self._generate_text(prompt, config, purpose, images)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise TextGenerationError(f"Gemini returned invalid JSON: {text[:200]!r}") from e
        if not isinstance(data, dict):
            raise TextGenerationError(f"Gemini returned JSON that is not an object: {text[:200]!r}")
        return data

    async def choose_tool(
        self,
        prompt: str,
        tools: Sequence[ToolSpec],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
        images: Sequence[Screenshot] = (),
    ) -> ToolChoice:
        """
        function calling で道具を必ず 1 つ呼ばせる（mode ANY）。呼び出しごとに独立。

        Raises:
            TextGenerationError: API の呼び出しが失敗したか、道具を呼ばなかったとき
        """
        declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
        config = self._config_for(purpose).model_copy(
            update={
                "system_instruction": system_instruction,
                "tools": [types.Tool(function_declarations=declarations)],
                "tool_config": types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.ANY
                    )
                ),
            }
        )
        response = await self._request(prompt, config, purpose, images)
        calls = response.function_calls
        if not calls:
            raise TextGenerationError(
                f"Gemini did not call a tool (finish_reason={self._finish_reason(response)})"
            )
        call = calls[0]
        if not call.name:
            raise TextGenerationError("Gemini called a tool without a name")
        return ToolChoice(name=call.name, args=dict(call.args or {}))

    def thinking_level_for(self, purpose: Optional[str]) -> str:
        """用途の考える深さ（表になければ既定）。"""
        return self._levels.get(purpose or "default", self._levels["default"])

    def _config_for(self, purpose: Optional[str]) -> types.GenerateContentConfig:
        level = self.thinking_level_for(purpose)
        if level == self._levels["default"]:
            return self._config
        return self._config.model_copy(update={"thinking_config": self._thinking(level)})

    def _thinking(self, level: str) -> types.ThinkingConfig:
        if self._include_thoughts:
            return types.ThinkingConfig(thinking_level=level, include_thoughts=True)
        return types.ThinkingConfig(thinking_level=level)

    async def close(self) -> None:
        """内部の HTTP クライアントを閉じる。"""
        await self._client.aio.aclose()

    async def _generate_text(
        self,
        prompt: str,
        config: types.GenerateContentConfig,
        purpose: Optional[str],
        images: Sequence[Screenshot] = (),
    ) -> str:
        """API を呼び、空の出力を診断してテキストを返す。"""
        response = await self._request(prompt, config, purpose, images)
        text = (response.text or "").strip()
        finish_reason = self._finish_reason(response)

        if finish_reason == types.FinishReason.MAX_TOKENS:
            logger.warning(
                f"Gemini が max_output_tokens（思考を含む）に達した。"
                f"出力は{'途中で切れている' if text else '空'}。"
                f"max_output_tokens を上げるか、thinking_level を下げることを検討する。"
            )

        if not text:
            feedback = response.prompt_feedback
            if feedback and feedback.block_reason:
                logger.warning(f"Gemini がプロンプトをブロックした: {feedback.block_reason}")
            else:
                logger.warning(f"Gemini がテキストを返さなかった（finish_reason={finish_reason}）")

        return text

    async def _request(
        self,
        prompt: str,
        config: types.GenerateContentConfig,
        purpose: Optional[str],
        images: Sequence[Screenshot] = (),
    ) -> types.GenerateContentResponse:
        """レート制限、使用量のログ、呼び出しの記録つきで API を呼ぶ。画像はテキストの後。"""
        await self._wait_for_rate_limit()

        contents: Any = prompt
        if images:
            contents = [
                types.Part.from_text(text=prompt),
                *[types.Part.from_bytes(data=i.data, mime_type=i.mime_type) for i in images],
            ]
            resolution = self._media_resolutions.get(
                purpose or "default", self._media_resolutions["default"]
            )
            config = config.model_copy(update={"media_resolution": resolution})
        started = time.monotonic()
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except errors.APIError as e:
            error = TextGenerationError(f"Gemini API error {e.code}: {e.message}")
            self._record(prompt, config, purpose, started, images, error=str(error))
            raise error from e
        except Exception as e:
            error = TextGenerationError(f"Gemini request failed: {e}")
            self._record(prompt, config, purpose, started, images, error=str(error))
            raise error from e

        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._log_usage(response, elapsed_ms, config)
        self._record(prompt, config, purpose, started, images, response=response)
        return response

    def _record(
        self,
        prompt: str,
        config: types.GenerateContentConfig,
        purpose: Optional[str],
        started: float,
        images: Sequence[Screenshot] = (),
        response: Optional[types.GenerateContentResponse] = None,
        error: Optional[str] = None,
    ) -> None:
        """呼び出しを 1 件、デバッグの記録に残す（思考の要約、出力、トークン）。"""
        if self._generation_log is None:
            return
        level = config.thinking_config.thinking_level if config.thinking_config else None
        entry: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "purpose": purpose or "default",
            "thinking_level": str(getattr(level, "value", level) or "").lower() or None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "prompt": prompt,
            # 画像そのものは入れない（記録は 2 秒ごとに読まれる）。id で別に取れる
            "images": [
                {
                    "id": self._generation_log.record_image(i.data, i.mime_type),
                    "mime_type": i.mime_type,
                    "bytes": len(i.data),
                }
                for i in images
            ],
        }
        if error is not None:
            entry["error"] = error
        if response is not None:
            parts = []
            if response.candidates and response.candidates[0].content:
                parts = response.candidates[0].content.parts or []
            entry["thoughts"] = "\n".join(p.text for p in parts if p.thought and p.text) or None
            entry["output"] = (response.text or "").strip() or None
            calls = response.function_calls or []
            entry["tool_calls"] = [{"name": c.name, "args": dict(c.args or {})} for c in calls]
            finish = self._finish_reason(response)
            entry["finish_reason"] = getattr(finish, "value", finish)
            usage = response.usage_metadata
            entry["tokens"] = (
                {
                    "prompt": usage.prompt_token_count,
                    "thoughts": usage.thoughts_token_count,
                    "output": usage.candidates_token_count,
                }
                if usage
                else None
            )
        self._generation_log.record(entry)

    async def _wait_for_rate_limit(self) -> None:
        """前のリクエストから min_request_interval が経つまで待つ。"""
        async with self._rate_lock:
            if self._last_request_at is not None:
                wait = self._min_interval - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _finish_reason(response: types.GenerateContentResponse) -> Optional[types.FinishReason]:
        """最初の候補の finish reason を返す（なければ None）。"""
        if not response.candidates:
            return None
        return response.candidates[0].finish_reason

    def _log_usage(
        self,
        response: types.GenerateContentResponse,
        elapsed_ms: int,
        config: types.GenerateContentConfig,
    ) -> None:
        """トークンの使用量と所要時間をログに出す（トークンの監視はログだけ）。"""
        usage = response.usage_metadata
        if usage is None:
            logger.info(f"Gemini {self._model}: {elapsed_ms}ms（使用量の情報なし）")
            return
        logger.info(
            f"Gemini {self._model}（thinking {config.thinking_config.thinking_level}）: "
            f"{elapsed_ms}ms, "
            f"prompt={usage.prompt_token_count} "
            f"thoughts={usage.thoughts_token_count} "
            f"output={usage.candidates_token_count} "
            f"total={usage.total_token_count}"
        )
