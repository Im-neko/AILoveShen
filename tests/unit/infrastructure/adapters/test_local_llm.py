"""ローカルの LLM のアダプターと振り分け（docs/design/36）のテスト。"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from ailoveshen.application.ports.output.text_generator import ToolChoice, ToolSpec
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import Screenshot
from ailoveshen.infrastructure.adapters.local_llm.openai_compat_text_generator import (
    OpenAICompatTextGenerator,
)
from ailoveshen.infrastructure.adapters.local_llm.routing_text_generator import (
    RoutingTextGenerator,
)
from ailoveshen.infrastructure.adapters.local_llm.schema_check import (
    check,
    estimate_tokens,
    extract_json,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "color": {"type": "string", "enum": ["red", "blue"]},
        "count": {"type": "integer", "minimum": 1},
        "steps": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
    },
    "required": ["color"],
}
TOOLS = [ToolSpec("dig", "dig a block", {"type": "object", "properties": {"x": {"type": "integer"}}})]


def _answer(content=None, tool_calls=None, usage=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    }


class _Server:
    """台本どおりに答える偽のサーバー（受けたリクエストを残す）。"""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.6"}]})
        self.requests.append(json.loads(request.content))
        answer = self.answers.pop(0)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


def _local(server, **kwargs):
    return OpenAICompatTextGenerator(
        "http://local/v1",
        transport=httpx.MockTransport(server),
        thinking_levels={"goal_after_failure": "medium", "tool": "low"},
        **kwargs,
    )


def test_json_is_taken_out_of_the_answer_and_checked():
    assert extract_json('<think>hmm</think>```json\n{"color": "red"}\n```') == {"color": "red"}
    assert extract_json('Sure! {"color": "blue", "count": 2} done') == {"color": "blue", "count": 2}
    with pytest.raises(ValueError):
        extract_json("no json here")
    assert check({"color": "red", "count": 3, "steps": ["a"]}, SCHEMA) == []
    errors = check({"color": "green", "count": 0, "steps": ["a", "b", "c"]}, SCHEMA)
    assert len(errors) == 3
    assert check({}, SCHEMA) == ["$.color is required"]
    assert check({"color": "red", "count": True}, SCHEMA) == ["$.count must be integer, got bool"]


def test_tokens_are_estimated_generously_for_japanese():
    assert estimate_tokens("あいうえお") == 5
    assert estimate_tokens("abcdefgh") == 2


@pytest.mark.asyncio
async def test_generate_json_sends_the_schema_and_asks_again_when_it_is_broken():
    server = _Server(_answer('{"color": "green"}'), _answer('{"color": "red"}'))
    local = _local(server)
    assert await local.generate_json("pick", SCHEMA, "rules", purpose="goal") == {"color": "red"}
    first, second = server.requests
    assert first["model"] == "qwen3.6"
    assert first["response_format"]["json_schema"]["schema"] == SCHEMA
    assert '"enum": ["red", "blue"]' in first["messages"][-1]["content"]  # プロンプトにもスキーマ
    assert first["messages"][0] == {"role": "system", "content": "rules"}
    assert "must be one of" in second["messages"][-1]["content"]
    await local.close()


@pytest.mark.asyncio
async def test_a_refused_response_format_is_dropped_for_good():
    server = _Server(httpx.Response(400, text="unknown field"), _answer('{"color": "red"}'), _answer('{"color": "blue"}'))
    local = _local(server)
    await local.generate_json("pick", SCHEMA, purpose="goal")
    await local.generate_json("pick", SCHEMA, purpose="goal")
    assert "response_format" not in server.requests[1] and "response_format" not in server.requests[2]


@pytest.mark.asyncio
async def test_thinking_follows_the_purposes_depth():
    server = _Server(_answer("a"), _answer("b"))
    local = _local(server)
    await local.generate("x", purpose="tool")
    await local.generate("x", purpose="goal_after_failure")
    assert [r["chat_template_kwargs"]["enable_thinking"] for r in server.requests] == [False, True]


@pytest.mark.asyncio
async def test_a_tool_call_is_read_and_a_text_answer_is_asked_again_as_json():
    call = {"function": {"name": "dig", "arguments": '{"x": 3}'}}
    server = _Server(_answer(tool_calls=[call]))
    local = _local(server)
    assert await local.choose_tool("go", TOOLS, purpose="tool") == ToolChoice("dig", {"x": 3})
    assert server.requests[0]["tool_choice"] == "required"
    assert server.requests[0]["tools"][0]["function"]["name"] == "dig"

    server = _Server(_answer("I would dig."), _answer('{"tool": "dig", "args": {"x": 5}}'))
    local = _local(server)
    assert await local.choose_tool("go", TOOLS, purpose="tool") == ToolChoice("dig", {"x": 5})
    assert "Call exactly one of these tools" in server.requests[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_the_local_model_takes_no_images():
    local = _local(_Server())
    with pytest.raises(TextGenerationError):
        await local.generate_json("x", SCHEMA, images=[Screenshot(b"png", "image/png")])


def _router(**kwargs):
    local, gemini = AsyncMock(), AsyncMock()
    local.generate.return_value = "local"
    gemini.generate.return_value = "gemini"
    local.generate_json.return_value = {"by": "local"}
    gemini.generate_json.return_value = {"by": "gemini"}
    return RoutingTextGenerator(local, gemini, **kwargs), local, gemini


@pytest.mark.asyncio
async def test_calls_go_by_purpose_and_images_always_go_to_gemini():
    router, local, gemini = _router(routes={"build_design": "gemini"})
    assert await router.generate("x", purpose="commentary") == "local"
    assert await router.generate_json("x", SCHEMA, purpose="build_design") == {"by": "gemini"}
    shot = Screenshot(b"png", "image/png")
    assert await router.generate_json("x", SCHEMA, purpose="goal_after_failure", images=[shot]) == {"by": "gemini"}
    assert gemini.generate_json.await_args.args[4] == [shot]
    assert local.generate_json.await_count == 0


@pytest.mark.asyncio
async def test_a_prompt_too_long_for_the_local_model_goes_to_gemini():
    router, local, gemini = _router(context_tokens=1000, reserve_output_tokens=500)
    assert await router.generate("あ" * 400, purpose="reply") == "local"
    assert await router.generate("あ" * 600, purpose="reply") == "gemini"


@pytest.mark.asyncio
async def test_a_local_failure_is_an_error_unless_the_fallback_is_gemini():
    router, local, gemini = _router()
    local.generate.side_effect = TextGenerationError("down")
    with pytest.raises(TextGenerationError):
        await router.generate("x", purpose="reply")
    router, local, gemini = _router(fallback="gemini")
    local.generate.side_effect = TextGenerationError("down")
    assert await router.generate("x", purpose="reply") == "gemini"


def test_unknown_routes_are_refused():
    with pytest.raises(ValueError):
        RoutingTextGenerator(AsyncMock(), AsyncMock(), routes={"reply": "claude"})
