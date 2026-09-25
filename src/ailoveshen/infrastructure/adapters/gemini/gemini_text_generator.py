"""Gemini でテキストを生成するアダプター（google-genai SDK）。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from google import genai
from google.genai import errors, types
from loguru import logger

from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.exceptions import TextGenerationError

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
    ) -> None:
        """
        Gemini のクライアントを初期化する。

        Args:
            api_key: Gemini の API キー
            model: モデル ID
            thinking_level: "low"、"medium"、"high" のどれか
            max_output_tokens: 出力トークンの上限（思考のトークンを含む）
            retry_attempts: 最初のリクエストを含めた最大の試行回数
            retry_initial_delay_seconds: バックオフの最初の待ち時間
            retry_max_delay_seconds: バックオフの最大の待ち時間
            retry_exponential_base: バックオフの倍率
            min_request_interval_seconds: リクエストの間の最小の間隔

        Raises:
            ValueError: api_key が空か、thinking_level に対応していないとき。
        """
        if not api_key:
            raise ValueError("Gemini API key is required (set GEMINI_API_KEY)")
        if thinking_level not in SUPPORTED_THINKING_LEVELS:
            raise ValueError(
                f"thinking_level must be one of {SUPPORTED_THINKING_LEVELS}, got {thinking_level!r}"
            )

        self._model = model
        self._min_interval = min_request_interval_seconds
        self._config = types.GenerateContentConfig(
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
            # ツールは使わないので、自動の関数呼び出しを明示的に無効にする
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
    ) -> str:
        """
        Gemini でテキストを生成する。

        Args:
            prompt: ユーザーのプロンプト
            system_instruction: システム指示（キャラクターの設定）

        Returns:
            生成したテキスト。モデルがテキストを返さなかったとき（ブロックされた、
            思考中に出力の上限に達した、など）は空文字列

        Raises:
            TextGenerationError: 再試行しても API の呼び出しが失敗したとき
        """
        config = self._config.model_copy(update={"system_instruction": system_instruction})
        return await self._generate_text(prompt, config)

    async def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_instruction: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        JSON Schema に沿った JSON オブジェクトを生成する（Gemini の構造化出力）。

        Raises:
            TextGenerationError: API の呼び出しが失敗したか、出力が JSON オブジェクトでないとき
        """
        config = self._config.model_copy(
            update={
                "system_instruction": system_instruction,
                "response_mime_type": "application/json",
                "response_json_schema": schema,
            }
        )
        text = await self._generate_text(prompt, config)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise TextGenerationError(f"Gemini returned invalid JSON: {text[:200]!r}") from e
        if not isinstance(data, dict):
            raise TextGenerationError(f"Gemini returned JSON that is not an object: {text[:200]!r}")
        return data

    async def close(self) -> None:
        """内部の HTTP クライアントを閉じる。"""
        await self._client.aio.aclose()

    async def _generate_text(self, prompt: str, config: types.GenerateContentConfig) -> str:
        """レート制限、使用量のログ、空の出力の診断を付けて API を呼ぶ。"""
        await self._wait_for_rate_limit()

        started = time.monotonic()
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=config,
            )
        except errors.APIError as e:
            raise TextGenerationError(f"Gemini API error {e.code}: {e.message}") from e
        except Exception as e:
            raise TextGenerationError(f"Gemini request failed: {e}") from e

        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._log_usage(response, elapsed_ms)

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

    def _log_usage(self, response: types.GenerateContentResponse, elapsed_ms: int) -> None:
        """トークンの使用量と所要時間をログに出す（トークンの監視はログだけ）。"""
        usage = response.usage_metadata
        if usage is None:
            logger.info(f"Gemini {self._model}: {elapsed_ms}ms（使用量の情報なし）")
            return
        logger.info(
            f"Gemini {self._model}: {elapsed_ms}ms, "
            f"prompt={usage.prompt_token_count} "
            f"thoughts={usage.thoughts_token_count} "
            f"output={usage.candidates_token_count} "
            f"total={usage.total_token_count}"
        )
