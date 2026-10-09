"""ローカルの LLM のアダプターと振り分け（docs/design/36）、LiteLLM のリモート（docs/design/127）のテスト。"""

import base64
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
    drop_empty,
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


def test_empty_optional_fields_count_as_not_written():
    """使わない項目を "" で埋めて enum に合わず、書き直しになっていた（2026-09-27: lesson_when.goal）。"""
    schema = {
        "type": "object",
        "properties": {
            "reply": {"type": "string"},
            "reason": {"type": "string", "enum": ["cannot", "wrong"]},
            "note": {"type": ["string", "null"]},
            "when": {
                "type": "object",
                "properties": {"goal": {"type": "string", "enum": ["have"]}},
            },
        },
        "required": ["reply"],
    }
    data = drop_empty(
        {"reply": "", "reason": "", "note": None, "when": {"goal": ""}, "extra": None}, schema
    )
    assert data == {"reply": "", "note": None, "when": {}}
    assert check(data, schema) == []


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


class _Log:
    def __init__(self):
        self.entries, self.images = [], []

    def record(self, entry):
        self.entries.append(entry)

    def record_image(self, data, mime_type):
        self.images.append((data, mime_type))
        return f"img{len(self.images)}"


@pytest.mark.asyncio
async def test_a_model_that_reads_images_gets_them_as_data_urls_and_records_them():
    """LiteLLM 経由の Claude など: 画像は image_url の部品で送り、記録には中身ではなく id を残す。"""
    server = _Server(_answer('{"color": "red"}'), _answer(tool_calls=[{"function": {"name": "dig", "arguments": "{}"}}]))
    log = _Log()
    remote = OpenAICompatTextGenerator(
        "http://proxy/v1", model="haiku", transport=httpx.MockTransport(server), label="LiteLLM", images=True,
        thinking_param="none", thinking_purposes=[], generation_log=log,
    )
    shot = Screenshot(b"\x89PNGdata", "image/png")
    assert await remote.generate_json("what is it", SCHEMA, purpose="screen_review", images=[shot]) == {"color": "red"}
    content = server.requests[0]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": content[0]["text"]} and "what is it" in content[0]["text"]
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"\x89PNGdata").decode()
    assert "chat_template_kwargs" not in server.requests[0]
    assert log.images == [(b"\x89PNGdata", "image/png")]
    assert log.entries[0]["model"] == "litellm:haiku"
    assert log.entries[0]["images"] == [{"id": "img1", "mime_type": "image/png", "bytes": 8}]
    assert "what is it" in log.entries[0]["prompt"]
    assert await remote.choose_tool("go", TOOLS, purpose="tool", images=[shot]) == ToolChoice("dig", {})
    assert server.requests[1]["messages"][-1]["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_a_text_answer_to_a_tool_call_with_images_is_asked_again_with_the_images():
    server = _Server(_answer("I would dig."), _answer('{"tool": "dig", "args": {"x": 5}}'))
    remote = OpenAICompatTextGenerator(
        "http://proxy/v1", model="haiku", transport=httpx.MockTransport(server), images=True
    )
    shot = Screenshot(b"png", "image/png")
    assert await remote.choose_tool("go", TOOLS, images=[shot]) == ToolChoice("dig", {"x": 5})
    assert server.requests[1]["messages"][-1]["content"][1]["type"] == "image_url"


def _router(**kwargs):
    local, remote = AsyncMock(), AsyncMock()
    local.generate.return_value = "local"
    remote.generate.return_value = "remote"
    local.generate_json.return_value = {"by": "local"}
    remote.generate_json.return_value = {"by": "remote"}
    return RoutingTextGenerator(local, remote, **kwargs), local, remote


@pytest.mark.asyncio
async def test_calls_go_by_purpose_and_images_always_go_to_the_remote():
    router, local, remote = _router(routes={"build_design": "remote"})
    assert await router.generate("x", purpose="commentary") == "local"
    assert await router.generate_json("x", SCHEMA, purpose="build_design") == {"by": "remote"}
    shot = Screenshot(b"png", "image/png")
    assert await router.generate_json("x", SCHEMA, purpose="goal_after_failure", images=[shot]) == {"by": "remote"}
    assert remote.generate_json.await_args.args[4] == [shot]
    assert local.generate_json.await_count == 0


@pytest.mark.asyncio
async def test_a_prompt_too_long_for_the_local_model_goes_to_the_remote():
    router, local, remote = _router(context_tokens=1000, reserve_output_tokens=500)
    assert await router.generate("あ" * 400, purpose="reply") == "local"
    assert await router.generate("あ" * 600, purpose="reply") == "remote"


@pytest.mark.asyncio
async def test_a_local_failure_is_an_error_unless_the_fallback_is_remote():
    router, local, remote = _router()
    local.generate.side_effect = TextGenerationError("down")
    with pytest.raises(TextGenerationError):
        await router.generate("x", purpose="reply")
    router, local, remote = _router(fallback="remote")
    local.generate.side_effect = TextGenerationError("down")
    assert await router.generate("x", purpose="reply") == "remote"


def test_unknown_routes_are_refused():
    with pytest.raises(ValueError):
        RoutingTextGenerator(AsyncMock(), AsyncMock(), routes={"reply": "claude"})


@pytest.mark.asyncio
async def test_only_the_field_the_server_refuses_is_dropped():
    """chat_template_kwargs だけを断るサーバー: response_format は送り続ける。"""
    seen = []

    def server(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        body = json.loads(request.content)
        seen.append(body)
        if "chat_template_kwargs" in body:
            return httpx.Response(400, text="unknown field chat_template_kwargs")
        return httpx.Response(200, json=_answer('{"color": "red"}'))

    local = OpenAICompatTextGenerator("http://local/v1", transport=httpx.MockTransport(server))
    await local.generate_json("pick", SCHEMA, purpose="goal")
    await local.generate_json("pick", SCHEMA, purpose="goal")
    last = seen[-1]
    assert "response_format" in last and "chat_template_kwargs" not in last
    assert len(seen) == 4  # 400、response_format なし（400）、chat_template_kwargs なし（通る）、2 回目


@pytest.mark.asyncio
async def test_an_answer_cut_by_thinking_is_a_clear_error_not_a_retry():
    cut = _answer("")
    cut["choices"][0]["finish_reason"] = "length"
    server = _Server(cut)
    local = _local(server, max_output_tokens=2000)
    with pytest.raises(TextGenerationError, match="max_output_tokens"):
        await local.generate_json("pick", SCHEMA, purpose="goal_after_failure")
    assert len(server.requests) == 1
    assert server.requests[0]["max_tokens"] == 2000


@pytest.mark.asyncio
async def test_a_capped_thinking_purpose_is_asked_again_without_thinking_when_it_hits_the_cap():
    """tool_stuck の長い思考でプレイが数分止まった（2026-09-29）: 上限で切れたら、思考なしで聞き直す。"""
    cut = _answer("")
    cut["choices"][0]["finish_reason"] = "length"
    server = _Server(cut, _answer("ok"))
    local = _local(
        server, thinking_purposes=["tool_stuck"], thinking_max_tokens={"tool_stuck": 3000}
    )
    assert await local.generate("pick", purpose="tool_stuck") == "ok"
    first, second = server.requests
    assert first["max_tokens"] == 3000 and first["chat_template_kwargs"] == {"enable_thinking": True}
    assert second["chat_template_kwargs"] == {"enable_thinking": False}
    assert second["max_tokens"] > 3000


@pytest.mark.asyncio
async def test_a_purpose_that_does_not_think_is_cut_at_its_output_cap():
    """返事が 4,462 トークン（84 秒）書き続けた（2026-09-30）: 思考しない用途は、決めた長さで切る。"""
    server = _Server(_answer("a"), _answer("b"))
    local = _local(server, output_max_tokens={"reply": 1200})
    await local.generate("x", purpose="reply")
    await local.generate("x", purpose="commentary")  # 上限を決めていない用途は、そのまま
    assert server.requests[0]["max_tokens"] == 1200
    assert server.requests[1]["max_tokens"] > 1200


@pytest.mark.asyncio
async def test_the_output_cap_does_not_apply_to_a_thinking_purpose():
    server = _Server(_answer("a"))
    local = _local(
        server, thinking_purposes=["tool_stuck"], thinking_max_tokens={"tool_stuck": 3000}, output_max_tokens={"tool_stuck": 500}
    )
    await local.generate("x", purpose="tool_stuck")
    assert server.requests[0]["max_tokens"] == 3000


@pytest.mark.asyncio
async def test_the_output_budget_shrinks_to_what_the_context_leaves():
    server = _Server(_answer("ok"))
    local = _local(server, max_output_tokens=16000, context_tokens=8000)
    await local.generate("あ" * 5000, purpose="reply")
    assert server.requests[0]["max_tokens"] == 8000 - 5000


@pytest.mark.asyncio
async def test_a_shared_router_is_closed_once():
    router, local, remote = _router()
    await router.close()
    await router.close()
    assert local.close.await_count == 1 and remote.close.await_count == 1


@pytest.mark.asyncio
async def test_only_the_listed_purposes_think():
    """ふだんの操作の判断は、失敗の後でも思考なし（ローカルの思考は遅い）。"""
    server = _Server(_answer("a"), _answer("b"), _answer("c"))
    local = _local(server, thinking_purposes=["skill_write"])
    for purpose in ("tool_after_failure", "goal_after_failure", "skill_write"):
        await local.generate("x", purpose=purpose)
    assert [r["chat_template_kwargs"]["enable_thinking"] for r in server.requests] == [False, False, True]


def _claude(server, **kwargs):
    return OpenAICompatTextGenerator(
        "http://proxy/v1",
        model="claude-haiku-5-5",
        transport=httpx.MockTransport(server),
        thinking_param="anthropic",
        thinking_levels={"goal": "low", "goal_after_failure": "medium", "skill_write": "high"},
        label="LiteLLM",
        images=True,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_claude_thinks_only_for_the_purposes_whose_level_is_medium_or_high():
    """Haiku 5.5 は指定しないと思考が走る: 浅い用途は disabled を送り、深い用途は adaptive と effort を送る。"""
    server = _Server(_answer("a"), _answer("b"), _answer("c"), _answer("d"))
    claude = _claude(server)
    await claude.generate("x", purpose="goal")
    await claude.generate("x", purpose="goal_after_failure")
    await claude.generate("x", purpose="skill_write")
    await claude.generate("x", purpose="unlisted")
    bodies = server.requests
    assert bodies[0]["thinking"] == {"type": "disabled"} and "reasoning_effort" not in bodies[0]
    assert bodies[1]["thinking"] == {"type": "adaptive"} and bodies[1]["reasoning_effort"] == "medium"
    assert bodies[2]["thinking"] == {"type": "adaptive"} and bodies[2]["reasoning_effort"] == "high"
    assert bodies[3]["thinking"] == {"type": "disabled"}
    assert all("chat_template_kwargs" not in b for b in bodies)


@pytest.mark.asyncio
async def test_a_purpose_can_use_another_model_and_the_log_names_the_model_that_answered():
    server = _Server(_answer("a"), _answer("b"))
    log = _Log()
    claude = _claude(server, models={"skill_write": "claude-sonnet-5-5", "empty": ""}, generation_log=log)
    await claude.generate("x", purpose="skill_write")
    await claude.generate("x", purpose="goal")
    assert [r["model"] for r in server.requests] == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    assert [e["model"] for e in log.entries] == ["litellm:claude-sonnet-5-5", "litellm:claude-haiku-5-5"]


@pytest.mark.asyncio
async def test_the_cache_tokens_the_proxy_reports_are_logged():
    from loguru import logger

    usage = {
        "prompt_tokens": 3747,
        "completion_tokens": 20,
        "total_tokens": 3767,
        "prompt_tokens_details": {"cached_tokens": 3000},
        "cache_creation_input_tokens": 700,
    }
    server = _Server(_answer("a", usage=usage))
    log, lines = _Log(), []
    sink = logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        await _claude(server, generation_log=log).generate("x", purpose="goal")
    finally:
        logger.remove(sink)
    assert "cached=3000" in lines[0] and "cache_write=700" in lines[0]
    assert log.entries[0]["tokens"]["cached"] == 3000 and log.entries[0]["tokens"]["cache_write"] == 700


@pytest.mark.asyncio
async def test_cache_control_goes_on_the_system_and_in_front_of_each_cache_break_for_claude():
    from ailoveshen.application.ports.output.text_generator import CACHE_BREAK

    server = _Server(_answer("a"))
    claude = _claude(server, prompt_cache=True)
    await claude.generate(f"rules\n{CACHE_BREAK}goal\n{CACHE_BREAK}state", system_instruction="persona", purpose="goal")
    system, user = server.requests[0]["messages"]
    assert system["content"] == [{"type": "text", "text": "persona", "cache_control": {"type": "ephemeral"}}]
    assert user["content"] == [
        {"type": "text", "text": "rules\n", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "goal\n", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "state"},
    ]


@pytest.mark.asyncio
async def test_a_prompt_without_breaks_is_not_marked_and_there_are_at_most_four_marks():
    from ailoveshen.application.ports.output.text_generator import CACHE_BREAK

    server = _Server(_answer("a"), _answer("b"))
    claude = _claude(server, prompt_cache=True)
    await claude.generate("plain prompt", purpose="goal")
    assert server.requests[0]["messages"][-1]["content"] == "plain prompt"
    await claude.generate(CACHE_BREAK.join("abcdefg"), system_instruction="s", purpose="goal")
    marks = [
        part for m in server.requests[1]["messages"] for part in m["content"] if "cache_control" in part
    ]
    assert len(marks) == 4


@pytest.mark.asyncio
async def test_without_prompt_cache_the_breaks_are_only_removed():
    from ailoveshen.application.ports.output.text_generator import CACHE_BREAK

    server = _Server(_answer("a"))
    await _claude(server).generate(f"rules\n{CACHE_BREAK}state", system_instruction="persona", purpose="goal")
    system, user = server.requests[0]["messages"]
    assert system["content"] == "persona" and user["content"] == "rules\nstate"
    assert CACHE_BREAK not in json.dumps(server.requests[0], ensure_ascii=False)


@pytest.mark.asyncio
async def test_the_output_budget_counts_an_image_by_a_fixed_size_not_by_its_base64():
    server = _Server(_answer('{"color": "red"}'))
    claude = _claude(server, context_tokens=20000, max_output_tokens=16384)
    await claude.generate_json("x", SCHEMA, purpose="goal", images=[Screenshot(b"\x00" * 400000, "image/png")])
    assert server.requests[0]["max_tokens"] > 10000
