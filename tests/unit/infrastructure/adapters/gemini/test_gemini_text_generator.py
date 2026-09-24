"""Tests for GeminiTextGenerator adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip all tests in this module if google-genai is not installed
pytest.importorskip("google.genai", reason="google-genai not installed")

from google.genai import errors, types  # noqa: E402

from ailoveshen.domain.exceptions import TextGenerationError  # noqa: E402
from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import (  # noqa: E402
    GeminiTextGenerator,
)

MODULE = "ailoveshen.infrastructure.adapters.gemini.gemini_text_generator"


def _response(
    text: str | None = "洞窟だ！",
    finish_reason: types.FinishReason = types.FinishReason.STOP,
    block_reason: types.BlockedReason | None = None,
) -> types.GenerateContentResponse:
    """Build a real GenerateContentResponse."""
    candidates = None
    if text is not None:
        candidates = [
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=finish_reason,
            )
        ]
    return types.GenerateContentResponse(
        candidates=candidates,
        prompt_feedback=(
            types.GenerateContentResponsePromptFeedback(block_reason=block_reason)
            if block_reason
            else None
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100,
            thoughts_token_count=50,
            candidates_token_count=10,
            total_token_count=160,
        ),
    )


@pytest.fixture
def mock_client():
    """Patch genai.Client and return the mock instance."""
    with patch(f"{MODULE}.genai.Client") as client_cls:
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(return_value=_response())
        client.aio.aclose = AsyncMock()
        client_cls.return_value = client
        yield client_cls


def _generator(**kwargs) -> GeminiTextGenerator:
    params = {"api_key": "test-key", "min_request_interval_seconds": 0.0}
    params.update(kwargs)
    return GeminiTextGenerator(**params)


class TestGeminiTextGeneratorInit:
    """Tests for constructor validation and client configuration."""

    def test_empty_api_key_raises(self, mock_client):
        """Test that a missing API key raises ValueError."""
        with pytest.raises(ValueError, match="API key is required"):
            GeminiTextGenerator(api_key="")

    @pytest.mark.parametrize("level", ["minimal", "none", "LOW"])
    def test_unsupported_thinking_level_raises(self, mock_client, level):
        """Test that unsupported thinking levels raise ValueError."""
        with pytest.raises(ValueError, match="thinking_level must be one of"):
            _generator(thinking_level=level)

    def test_retry_options_configured(self, mock_client):
        """Test SDK retry options follow the constructor arguments."""
        _generator(
            retry_attempts=3,
            retry_initial_delay_seconds=1.0,
            retry_max_delay_seconds=10.0,
            retry_exponential_base=2.0,
        )

        http_options = mock_client.call_args.kwargs["http_options"]
        retry = http_options.retry_options
        assert mock_client.call_args.kwargs["api_key"] == "test-key"
        assert retry.attempts == 3
        assert retry.initial_delay == 1.0
        assert retry.max_delay == 10.0
        assert retry.exp_base == 2.0


class TestGeminiTextGeneratorGenerate:
    """Tests for generate()."""

    @pytest.mark.asyncio
    async def test_generate_returns_stripped_text(self, mock_client):
        """Test generated text is returned stripped."""
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _response("  洞窟だ！\n")

        text = await _generator().generate("prompt")

        assert text == "洞窟だ！"

    @pytest.mark.asyncio
    async def test_generate_passes_model_and_config(self, mock_client):
        """Test model, prompt, system instruction and thinking config are sent."""
        generator = _generator(
            model="gemini-3.8-flash", thinking_level="low", max_output_tokens=2048
        )

        await generator.generate("prompt", system_instruction="system")

        kwargs = mock_client.return_value.aio.models.generate_content.call_args.kwargs
        config = kwargs["config"]
        assert kwargs["model"] == "gemini-3.8-flash"
        assert kwargs["contents"] == "prompt"
        assert config.system_instruction == "system"
        assert config.max_output_tokens == 2048
        assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
        assert config.automatic_function_calling.disable is True
        assert config.temperature is None

    @pytest.mark.asyncio
    async def test_system_instruction_not_shared_between_calls(self, mock_client):
        """Test each call gets its own config copy."""
        generator = _generator()
        await generator.generate("p1", system_instruction="s1")
        await generator.generate("p2")

        calls = mock_client.return_value.aio.models.generate_content.call_args_list
        assert calls[0].kwargs["config"].system_instruction == "s1"
        assert calls[1].kwargs["config"].system_instruction is None

    @pytest.mark.asyncio
    async def test_api_error_raises_text_generation_error(self, mock_client):
        """Test APIError is converted to TextGenerationError."""
        client = mock_client.return_value
        client.aio.models.generate_content.side_effect = errors.ClientError(
            400, {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}}
        )

        with pytest.raises(TextGenerationError, match="Gemini API error 400"):
            await _generator().generate("prompt")

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_text_generation_error(self, mock_client):
        """Test other exceptions are converted to TextGenerationError."""
        client = mock_client.return_value
        client.aio.models.generate_content.side_effect = RuntimeError("network down")

        with pytest.raises(TextGenerationError, match="network down"):
            await _generator().generate("prompt")

    @pytest.mark.asyncio
    async def test_blocked_prompt_returns_empty(self, mock_client):
        """Test a blocked prompt returns an empty string."""
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _response(
            text=None, block_reason=types.BlockedReason.SAFETY
        )

        assert await _generator().generate("prompt") == ""

    @pytest.mark.asyncio
    async def test_max_tokens_returns_truncated_text(self, mock_client):
        """Test MAX_TOKENS still returns the (truncated) text."""
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _response(
            text="途中まで", finish_reason=types.FinishReason.MAX_TOKENS
        )

        assert await _generator().generate("prompt") == "途中まで"

    @pytest.mark.asyncio
    async def test_rate_limit_sleeps_between_requests(self, mock_client):
        """Test the minimum interval is enforced between requests."""
        generator = _generator(min_request_interval_seconds=1.0)

        with patch(f"{MODULE}.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await generator.generate("p1")
            sleep.assert_not_called()
            await generator.generate("p2")
            sleep.assert_called_once()
            assert 0 < sleep.call_args.args[0] <= 1.0

    @pytest.mark.asyncio
    async def test_close(self, mock_client):
        """Test close() closes the async client."""
        generator = _generator()
        await generator.close()
        mock_client.return_value.aio.aclose.assert_awaited_once()
