"""GeminiTextGenerator アダプタのテスト。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# google-genai がなければ、このモジュールのテストは全部飛ばす
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
    """本物の GenerateContentResponse を作る。"""
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
    """genai.Client を差し替えて、モックのインスタンスを返す。"""
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
    """コンストラクタの検証とクライアントの設定のテスト。"""

    def test_empty_api_key_raises(self, mock_client):
        """API キーがなければ ValueError。"""
        with pytest.raises(ValueError, match="API key is required"):
            GeminiTextGenerator(api_key="")

    @pytest.mark.parametrize("level", ["minimal", "none", "LOW"])
    def test_unsupported_thinking_level_raises(self, mock_client, level):
        """対応していない thinking level なら ValueError。"""
        with pytest.raises(ValueError, match="thinking_level must be one of"):
            _generator(thinking_level=level)

    def test_retry_options_configured(self, mock_client):
        """SDK のリトライの設定はコンストラクタの引数に従う。"""
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
    """generate() のテスト。"""

    @pytest.mark.asyncio
    async def test_generate_returns_stripped_text(self, mock_client):
        """生成したテキストを前後の空白を除いて返す。"""
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _response("  洞窟だ！\n")

        text = await _generator().generate("prompt")

        assert text == "洞窟だ！"

    @pytest.mark.asyncio
    async def test_generate_passes_model_and_config(self, mock_client):
        """モデル、プロンプト、システム指示、thinking の設定が送られる。"""
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
        """呼び出しごとに設定のコピーを使う。"""
        generator = _generator()
        await generator.generate("p1", system_instruction="s1")
        await generator.generate("p2")

        calls = mock_client.return_value.aio.models.generate_content.call_args_list
        assert calls[0].kwargs["config"].system_instruction == "s1"
        assert calls[1].kwargs["config"].system_instruction is None

    @pytest.mark.asyncio
    async def test_api_error_raises_text_generation_error(self, mock_client):
        """APIError は TextGenerationError に変える。"""
        client = mock_client.return_value
        client.aio.models.generate_content.side_effect = errors.ClientError(
            400, {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}}
        )

        with pytest.raises(TextGenerationError, match="Gemini API error 400"):
            await _generator().generate("prompt")

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_text_generation_error(self, mock_client):
        """ほかの例外も TextGenerationError に変える。"""
        client = mock_client.return_value
        client.aio.models.generate_content.side_effect = RuntimeError("network down")

        with pytest.raises(TextGenerationError, match="network down"):
            await _generator().generate("prompt")

    @pytest.mark.asyncio
    async def test_blocked_prompt_returns_empty(self, mock_client):
        """ブロックされたプロンプトは空文字列を返す。"""
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _response(
            text=None, block_reason=types.BlockedReason.SAFETY
        )

        assert await _generator().generate("prompt") == ""

    @pytest.mark.asyncio
    async def test_max_tokens_returns_truncated_text(self, mock_client):
        """MAX_TOKENS でも（途中で切れた）テキストを返す。"""
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _response(
            text="途中まで", finish_reason=types.FinishReason.MAX_TOKENS
        )

        assert await _generator().generate("prompt") == "途中まで"

    @pytest.mark.asyncio
    async def test_rate_limit_sleeps_between_requests(self, mock_client):
        """リクエストの間は最小の間隔を空ける。"""
        generator = _generator(min_request_interval_seconds=1.0)

        with patch(f"{MODULE}.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await generator.generate("p1")
            sleep.assert_not_called()
            await generator.generate("p2")
            sleep.assert_called_once()
            assert 0 < sleep.call_args.args[0] <= 1.0

    @pytest.mark.asyncio
    async def test_close(self, mock_client):
        """close() は非同期クライアントを閉じる。"""
        generator = _generator()
        await generator.close()
        mock_client.return_value.aio.aclose.assert_awaited_once()


class TestGeminiTextGeneratorGenerateJson:
    """generate_json() のテスト。"""

    SCHEMA = {"type": "object", "properties": {"goal": {"type": "string"}}}

    @pytest.mark.asyncio
    async def test_returns_parsed_object(self, mock_client):
        """JSON のテキストを dict に解析する。"""
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _response('{"goal": "explore"}')

        data = await _generator().generate_json("prompt", self.SCHEMA)

        assert data == {"goal": "explore"}

    @pytest.mark.asyncio
    async def test_sends_schema_as_json_mode(self, mock_client):
        """スキーマと JSON の MIME タイプがリクエストの設定に入る。"""
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _response("{}")

        await _generator().generate_json("prompt", self.SCHEMA, system_instruction="sys")

        config = client.aio.models.generate_content.call_args.kwargs["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_json_schema == self.SCHEMA
        assert config.system_instruction == "sys"

    @pytest.mark.asyncio
    async def test_plain_generate_is_not_json_mode(self, mock_client):
        """JSON の設定はテキストだけの呼び出しに漏れない。"""
        generator = _generator()
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _response("{}")
        await generator.generate_json("p", self.SCHEMA)
        client.aio.models.generate_content.return_value = _response("text")

        await generator.generate("p")

        config = client.aio.models.generate_content.call_args.kwargs["config"]
        assert config.response_mime_type is None
        assert config.response_json_schema is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["not json", "[1, 2]"])
    async def test_non_object_raises(self, mock_client, text):
        """不正な JSON やオブジェクトでないものは TextGenerationError。"""
        mock_client.return_value.aio.models.generate_content.return_value = _response(text)

        with pytest.raises(TextGenerationError, match="JSON"):
            await _generator().generate_json("prompt", self.SCHEMA)


def _tool_response(name: str | None, args: dict | None = None) -> types.GenerateContentResponse:
    """道具を呼んだ（name が None なら呼ばなかった）応答。"""
    part = (
        types.Part(function_call=types.FunctionCall(name=name, args=args or {}))
        if name
        else types.Part(text="考え中")
    )
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[part]),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10),
    )


class TestThinkingPerPurpose:
    """用途ごとの考える深さ（設計書 19 §7）。"""

    def test_each_purpose_gets_its_level_and_unknown_ones_the_default(self, mock_client):
        g = _generator(thinking_level="low", thinking_levels={"town": "high", "tool": "low"})
        assert g.thinking_level_for("town") == "high"
        assert g.thinking_level_for("reply") == "low"
        assert g.thinking_level_for(None) == "low"

    def test_an_invalid_level_in_the_table_is_rejected_at_startup(self, mock_client):
        with pytest.raises(ValueError, match="for 'town'"):
            _generator(thinking_levels={"town": "max"})

    @pytest.mark.asyncio
    async def test_the_request_carries_the_purposes_level(self, mock_client):
        mock_client.return_value.aio.models.generate_content.return_value = _response("{}")
        g = _generator(thinking_levels={"town": "high"})
        await g.generate_json("p", {"type": "object"}, purpose="town")
        await g.generate("p", purpose="reply")
        calls = mock_client.return_value.aio.models.generate_content.call_args_list
        levels = [c.kwargs["config"].thinking_config.thinking_level for c in calls]
        assert [getattr(v, "value", v).lower() for v in levels] == ["high", "low"]


class TestChooseTool:
    """choose_tool(): function calling で道具を必ず 1 つ呼ばせる。"""

    def _tools(self):
        from ailoveshen.application.ports.output.text_generator import ToolSpec

        return [
            ToolSpec(
                "dig",
                "掘る",
                {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}, "intent": {"type": "string"}},
                    "required": ["intent", "x"],
                },
            )
        ]

    @pytest.mark.asyncio
    async def test_the_call_is_forced_and_returned(self, mock_client):
        client = mock_client.return_value
        client.aio.models.generate_content.return_value = _tool_response(
            "dig", {"x": 3, "intent": "掘る"}
        )

        choice = await _generator().choose_tool("prompt", self._tools(), purpose="tool")

        assert (choice.name, choice.args) == ("dig", {"x": 3, "intent": "掘る"})
        config = client.aio.models.generate_content.call_args.kwargs["config"]
        assert config.tool_config.function_calling_config.mode == (
            types.FunctionCallingConfigMode.ANY
        )
        declaration = config.tools[0].function_declarations[0]
        assert declaration.name == "dig"
        assert declaration.parameters_json_schema["required"] == ["intent", "x"]
        assert config.automatic_function_calling.disable is True

    @pytest.mark.asyncio
    async def test_no_call_is_an_error(self, mock_client):
        mock_client.return_value.aio.models.generate_content.return_value = _tool_response(None)
        with pytest.raises(TextGenerationError, match="did not call a tool"):
            await _generator().choose_tool("prompt", self._tools())
