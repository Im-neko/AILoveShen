"""
ローカルの LLM の JSON の答えを、スキーマの主な決まりで確かめる（docs/design/36 §3）。

サーバーが JSON スキーマで出力を縛らないとき（FreeToken）でも、形の間違いを理由つきで
書き直させるため。使うのはこのプロジェクトのスキーマが使う決まりだけ: type、enum、required、
properties、items、minimum / maximum、minItems / maxItems。
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def strip_thinking(text: str) -> str:
    """答えに混ざった思考（<think>…</think>）を取り除く。"""
    return _THINK.sub("", text).strip()


def extract_json(text: str) -> Any:
    """
    答えから JSON を取り出す（前後の文、コードの囲み、思考を除く）。

    Raises:
        ValueError: JSON が見つからないとき。
    """
    text = strip_thinking(text)
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("the answer has no JSON object")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"the answer is not valid JSON: {e}") from e
    return value


def check(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """スキーマに合わないところ（なければ空）。"""
    errors: list[str] = []
    kind = schema.get("type")
    if kind and not _is_type(value, kind):
        return [f"{path} must be {kind}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}, got {value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} must be <= {schema['maximum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key} is required")
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                errors += check(item, props[key], f"{path}.{key}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path} needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path} takes at most {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errors += check(item, schema["items"], f"{path}[{i}]")
    return errors


def _is_type(value: Any, kind: str | list[str]) -> bool:
    if isinstance(kind, list):
        return any(_is_type(value, k) for k in kind)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    return True


def estimate_tokens(text: str) -> int:
    """
    トークン数の見積もり（多めに）: ASCII でない字（日本語）は 1 字 1 トークン、
    ASCII は 4 字 1 トークン。
    """
    wide = sum(1 for c in text if ord(c) > 127)
    return wide + (len(text) - wide + 3) // 4
