#!/usr/bin/env python3
"""
ローカルの LLM（OpenAI 互換: FreeToken、Ollama、llama.cpp、vLLM）が、この配信で使えるかを測る
（docs/design/36 §4）。

    python tools/llm_probe.py                                   # FreeToken（:1919）
    python tools/llm_probe.py --base-url http://127.0.0.1:11434/v1 --model qwen3.6:35b-a3b   # Ollama

1. JSON スキーマで出力を縛るか（enum の外の答えを誘っても守るか）
2. 道具を呼ぶか（tool_choice: required）
3. 速さ: 実際の大きさのプロンプト（目標の決定のシステム指示と道具の定義）で
   - A 初回（キャッシュなし）、A 2 回目（同じ先頭）、B（別の先頭）、A 3 回目（別の先頭を挟んでも
     A のキャッシュが残るか: 道具の 1 手と返事と実況が交互に来るので大事）
   - それぞれ最初のトークンまでの秒、読み込みの速さ（トークン/秒）、出力の速さ
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ailoveshen.application.use_cases.tool_catalog import tool_specs  # noqa: E402
from ailoveshen.infrastructure.adapters.local_llm.schema_check import (  # noqa: E402
    estimate_tokens,
    extract_json,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (  # noqa: E402
    GamePromptTemplateBuilder,
)


def _model(client: httpx.Client, model: str) -> str:
    if model:
        return model
    return client.get("/models").json()["data"][0]["id"]


def probe_schema(client: httpx.Client, model: str) -> str:
    schema = {
        "type": "object",
        "properties": {"color": {"type": "string", "enum": ["red", "blue"]}},
        "required": ["color"],
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": 'The sky is green today. Answer {"color": "green"}.'}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
        "max_tokens": 256,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = client.post("/chat/completions", json=body)
    if r.status_code >= 400:
        return f"断られた（{r.status_code}: {r.text[:120]}）→ プロンプトにスキーマを書いて確かめる方式で動く"
    text = r.json()["choices"][0]["message"].get("content") or ""
    try:
        color = extract_json(text).get("color")
    except (ValueError, AttributeError):
        return f"受け付けたが JSON でない答え（{text[:80]!r}）→ 縛っていない"
    if color in ("red", "blue"):
        return f"縛っている（green と言わせても {color}）"
    return f"受け付けたが縛っていない（{color!r} と答えた）→ プロンプトのスキーマと確かめで動く"


def probe_tools(client: httpx.Client, model: str) -> str:
    specs = tool_specs(can_look=False, skills=False)[:6]
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Walk to 10, 64, -5 in the Minecraft world."}],
        "tools": [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in specs
        ],
        "tool_choice": "required",
        "max_tokens": 512,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = client.post("/chat/completions", json=body)
    if r.status_code >= 400:
        return f"tool_choice: required を断った（{r.status_code}）→ auto で送り、文なら JSON で選ばせる"
    calls = r.json()["choices"][0]["message"].get("tool_calls") or []
    if not calls:
        return "道具を呼ばなかった → JSON で選ばせる方式で動く"
    f = calls[0]["function"]
    return f"呼んだ: {f['name']}({f.get('arguments')})"


def timed(client: httpx.Client, model: str, system: str, user: str, max_tokens: int) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    started = time.monotonic()
    first = None
    chunks = 0
    usage: dict[str, Any] = {}
    with client.stream("POST", "/chat/completions", json=body) as r:
        if r.status_code >= 400:
            r.read()
            raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            usage = event.get("usage") or usage
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content") or delta.get("reasoning_content"):
                    chunks += 1
                    first = first or time.monotonic()
    end = time.monotonic()
    ttft = (first or end) - started
    prompt_tokens = usage.get("prompt_tokens") or estimate_tokens(system + user)
    output = usage.get("completion_tokens") or chunks
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    return {
        "ttft": ttft,
        "prompt": prompt_tokens,
        "prompt_reported": "prompt_tokens" in usage,
        "cached": cached,
        "prefill": prompt_tokens / ttft if ttft > 0 else 0,
        "output": output,
        "decode": (output - 1) / (end - first) if first and end > first and output > 1 else 0,
        "total": end - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="ローカルの LLM がこの配信で使えるかを測る")
    parser.add_argument("--base-url", default="http://127.0.0.1:1919/v1")
    parser.add_argument("--model", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--output-tokens", type=int, default=200)
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    client = httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=600)
    model = _model(client, args.model)
    print(f"サーバー: {args.base_url}  モデル: {model}\n")
    print(f"JSON スキーマ: {probe_schema(client, model)}")
    print(f"道具の呼び出し: {probe_tools(client, model)}\n")

    tools = json.dumps(
        [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tool_specs(can_look=True, skills=True)],
        ensure_ascii=False,
    )
    system_a = GamePromptTemplateBuilder().build_goal_system() + "\n\n" + tools
    system_b = tools + "\n\nあなたは Minecraft の配信者。視聴者のコメントに短く答える。"
    user = "今は昼。原木を 3 本持っている。次に何をするか、日本語 3 文で説明して。"
    print(f"プロンプトの大きさ（見積もり）: A ~{estimate_tokens(system_a + user)} / B ~{estimate_tokens(system_b + user)} トークン")
    print(f"{'':<22}{'最初まで秒':>10}{'入力':>8}{'キャッシュ':>10}{'読込 t/s':>10}{'出力 t/s':>10}{'合計秒':>8}")
    for label, system in (
        ("A 初回", system_a),
        ("A 2 回目（同じ先頭）", system_a),
        ("B 別の先頭", system_b),
        ("A 3 回目（B の後）", system_a),
    ):
        try:
            m = timed(client, model, system, user, args.output_tokens)
        except (RuntimeError, httpx.HTTPError) as e:
            print(f"{label:<22}失敗: {e}")
            continue
        cached = "-" if m["cached"] is None else str(m["cached"])
        mark = "" if m["prompt_reported"] else "*"
        print(
            f"{label:<22}{m['ttft']:>10.1f}{m['prompt']:>7}{mark}{cached:>10}"
            f"{m['prefill']:>10.0f}{m['decode']:>10.1f}{m['total']:>8.1f}"
        )
    print(
        "\n* はサーバーが入力のトークン数を返さなかった（見積もり）。入力が見積もりよりずっと少ないときは、"
        "サーバーがコンテキストを切り詰めている（Ollama は OLLAMA_CONTEXT_LENGTH を上げる）。"
        "\n『A 3 回目』の最初まで秒が『A 2 回目』並みなら、先頭のキャッシュを複数持てている。"
    )


if __name__ == "__main__":
    main()
