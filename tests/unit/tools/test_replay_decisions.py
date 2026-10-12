"""tools/replay_decisions.py（記録した出力を今の検証に通し直す、設計書 90）のテスト。"""

import importlib.util
import json
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "replay_decisions", Path(__file__).resolve().parents[3] / "tools" / "replay_decisions.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def _tool_entry(name="goto", args=None, purpose="tool", **kw):
    return {"purpose": purpose, "tool_calls": [{"name": name, "args": args if args is not None else {}}], **kw}


def test_a_valid_tool_call_is_accepted():
    v = tool.replay(_tool_entry(args={"intent": "go to the tree", "x": 1}))
    assert v.outcome == tool.ACCEPTED


def test_local_server_arguments_come_as_a_json_string():
    v = tool.replay(_tool_entry(args=json.dumps({"intent": "go"})))
    assert v.outcome == tool.ACCEPTED


def test_unknown_tool_and_missing_intent_are_rejected_with_the_reason():
    unknown = tool.replay(_tool_entry(name="reflex_surface", args={"intent": "x"}))
    no_intent = tool.replay(_tool_entry(args={"x": 1}))

    assert (unknown.outcome, "unknown tool" in unknown.reason) == (tool.REJECTED, True)
    assert (no_intent.outcome, "needs an intent" in no_intent.reason) == (tool.REJECTED, True)


def test_broken_json_arguments_are_rejected_not_crashed():
    v = tool.replay(_tool_entry(args="{not json"))
    assert v.outcome == tool.REJECTED


def test_failed_call_and_empty_answer_are_not_counted_as_rejected():
    assert tool.replay({"purpose": "tool", "error": "timeout"}).outcome == tool.NO_ANSWER
    assert tool.replay({"purpose": "goal", "output": None}).outcome == tool.NO_ANSWER


def test_goal_decision_without_serves_is_rejected_with_the_allowed_values():
    v = tool.replay({"purpose": "goal", "output": json.dumps({"jobs": []})})
    assert v.outcome == tool.REJECTED
    assert "serves must be one of" in v.reason


def test_goal_output_that_is_not_json_is_rejected():
    v = tool.replay({"purpose": "goal_after_failure", "output": "have(oak_log, 1)"})
    assert v.outcome == tool.REJECTED


def test_a_fenced_json_answer_is_read_the_way_the_stream_reads_it():
    """実際の記録（2026-10-06）: ローカルの LLM は JSON をコードの囲みに入れて返す。"""
    fenced = "```json\n" + json.dumps({"jobs": []}) + "\n```"
    v = tool.replay({"purpose": "goal", "output": fenced})
    assert v.outcome == tool.REJECTED
    assert "serves must be one of" in v.reason  # JSON としては読めて、中身の検証で断られる


def test_other_purposes_are_not_replayed():
    assert tool.replay({"purpose": "reply", "output": "hi"}) is None


def test_summary_groups_reasons_without_names_and_numbers():
    verdicts = [
        tool.Verdict("tool", tool.REJECTED, "unknown tool 'a'"),
        tool.Verdict("tool", tool.REJECTED, "unknown tool 'b'"),
        tool.Verdict("tool", tool.ACCEPTED),
    ]
    row = tool.summarize(verdicts)["tool"]
    assert (row[tool.ACCEPTED], row[tool.REJECTED]) == (1, 2)
    assert row["reasons"].most_common(1)[0][1] == 2


def test_main_reads_a_file_and_exports_the_rejected(tmp_path, capsys):
    source = tmp_path / "gen-x.jsonl"
    rows = [
        _tool_entry(args={"intent": "ok"}),
        _tool_entry(name="nope", args={"intent": "x"}),
        {"purpose": "reply", "output": "x"},
    ]
    source.write_text("".join(json.dumps(r) + "\n" for r in rows) + "{broken", encoding="utf-8")
    export = tmp_path / "out" / "rejected.jsonl"

    assert tool.main([str(source), "--export", str(export)]) == 0

    assert "tool: 2 件" in capsys.readouterr().out
    exported = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    assert len(exported) == 1 and "unknown tool" in exported[0]["reason"]


async def test_a_real_local_call_is_written_whole_and_replayed(tmp_path):
    """アダプター → JSONL の記録 → 再生の全部をつなぐ（偽のサーバーで）。"""
    import httpx

    from ailoveshen.application.ports.output.text_generator import ToolSpec
    from ailoveshen.infrastructure.adapters.local_llm.openai_compat_text_generator import (
        OpenAICompatTextGenerator,
    )
    from ailoveshen.infrastructure.adapters.storage import JsonlGenerationLog

    answers = [
        {"function": {"name": "goto", "arguments": json.dumps({"intent": "go to the tree"})}},
        {"function": {"name": "reflex_surface", "arguments": json.dumps({"intent": "x"})}},
    ]

    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        message = {"role": "assistant", "content": None, "tool_calls": [answers.pop(0)]}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    log = JsonlGenerationLog(str(tmp_path))
    local = OpenAICompatTextGenerator(
        "http://local/v1", transport=httpx.MockTransport(server), generation_log=log
    )
    tools = [ToolSpec("goto", "go", {"type": "object", "properties": {}})]
    await local.choose_tool("PROMPT-BODY", tools, system_instruction="SYSTEM-TEXT", purpose="tool")
    await local.choose_tool("PROMPT-BODY", tools, purpose="tool_after_failure")

    rows = list(tool.read_entries([log.path]))
    assert rows[0]["system"].endswith("\nSYSTEM-TEXT")  # 共通のシステム指示に続けて、呼び出し自身の指示
    assert rows[0]["prompt"] == "PROMPT-BODY"
    assert rows[0]["model"] == "local:m"
    assert rows[0]["tokens"] == {"prompt": 7, "output": 3, "cached": 0, "cache_write": 0}
    verdicts = [tool.replay(r) for r in rows]
    assert [v.outcome for v in verdicts] == [tool.ACCEPTED, tool.REJECTED]
    assert "unknown tool 'reflex_surface'" in verdicts[1].reason
