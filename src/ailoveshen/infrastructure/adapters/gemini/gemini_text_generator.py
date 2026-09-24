"""Gemini text generator adapter (google-genai SDK)."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from google import genai
from google.genai import errors, types
from loguru import logger

from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.exceptions import TextGenerationError

# Gemini 3.8 Flash accepts only these levels ("minimal" is rejected by the API)
SUPPORTED_THINKING_LEVELS = ("low", "medium", "high")


class GeminiTextGenerator(ITextGenerator):
    """
    Infrastructure adapter for the Gemini API.

    Implements ITextGenerator output port using the google-genai SDK.
    One instance serves one model slot (e.g., main or filter).

    - Retries 408/429/5xx with exponential backoff (SDK retry options)
    - Keeps a minimum interval between requests (simple rate limit)
    - Logs token usage and latency per request
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash",
        thinking_level: str = "medium",
        max_output_tokens: int = 8192,
        retry_attempts: int = 3,
        retry_initial_delay_seconds: float = 1.0,
        retry_max_delay_seconds: float = 10.0,
        retry_exponential_base: float = 2.0,
        min_request_interval_seconds: float = 1.0,
    ) -> None:
        """
        Initialize the Gemini client.

        Args:
            api_key: Gemini API key
            model: Model ID
            thinking_level: "low", "medium" or "high"
            max_output_tokens: Output token cap, including thinking tokens
            retry_attempts: Max attempts including the original request
            retry_initial_delay_seconds: Initial backoff delay
            retry_max_delay_seconds: Maximum backoff delay
            retry_exponential_base: Backoff multiplier
            min_request_interval_seconds: Minimum interval between requests

        Raises:
            ValueError: If api_key is empty or thinking_level is unsupported.
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
            # No tools are used; disable automatic function calling explicitly
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
        Generate text using Gemini.

        Args:
            prompt: User prompt
            system_instruction: System instruction (character context)

        Returns:
            Generated text, or empty string if the model returned no text
            (blocked content, output cap reached while thinking, etc.)

        Raises:
            TextGenerationError: If the API call fails after retries
        """
        await self._wait_for_rate_limit()

        config = self._config.model_copy(update={"system_instruction": system_instruction})
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
                f"Gemini hit max_output_tokens (thinking included); "
                f"output is {'truncated' if text else 'empty'}. "
                f"Consider raising max_output_tokens or lowering thinking_level."
            )

        if not text:
            feedback = response.prompt_feedback
            if feedback and feedback.block_reason:
                logger.warning(f"Gemini blocked the prompt: {feedback.block_reason}")
            else:
                logger.warning(f"Gemini returned no text (finish_reason={finish_reason})")

        return text

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aio.aclose()

    async def _wait_for_rate_limit(self) -> None:
        """Sleep until min_request_interval has passed since the last request."""
        async with self._rate_lock:
            if self._last_request_at is not None:
                wait = self._min_interval - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _finish_reason(response: types.GenerateContentResponse) -> Optional[types.FinishReason]:
        """Return the first candidate's finish reason, if any."""
        if not response.candidates:
            return None
        return response.candidates[0].finish_reason

    def _log_usage(self, response: types.GenerateContentResponse, elapsed_ms: int) -> None:
        """Log token usage and latency (token monitoring is log-only)."""
        usage = response.usage_metadata
        if usage is None:
            logger.info(f"Gemini {self._model}: {elapsed_ms}ms (no usage metadata)")
            return
        logger.info(
            f"Gemini {self._model}: {elapsed_ms}ms, "
            f"prompt={usage.prompt_token_count} "
            f"thoughts={usage.thoughts_token_count} "
            f"output={usage.candidates_token_count} "
            f"total={usage.total_token_count}"
        )
