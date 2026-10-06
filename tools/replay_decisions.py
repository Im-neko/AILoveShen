#!/usr/bin/env python3
"""
記録した LLM の出力を、今のコードの検証に通し直す（docs/design/90）。モデルが過去に出した答えが、
今のパーサに受け入れられるか、断られるならなぜかを数える。検証を変えたあと、過去の出力で壊れていないかの
確認に使う。

    python tools/replay_decisions.py                          # logs/generations/*.jsonl の全部
    python tools/replay_decisions.py logs/generations/gen-20261007T010000Z.jsonl
    python tools/replay_decisions.py --purpose tool,goal --show 5
    python tools/replay_decisions.py --export tests/fixtures/decisions/rejected.jsonl

通すのは状態を要らない検証だけ（道具の呼び出し: `parse_tool_call`、目標の決定: `parse_decision`）。
ブリッジの状態を見る検証（述語の語彙、`plan_lint`、ブリッジの 400）は、世界がないので通せない。
世界を再生するものではない。記録の元は `JsonlGenerationLog`（`gemini.generation_log_dir`）。

用途の対応: tool / tool_after_failure / tool_stuck → 道具の呼び出し、goal / goal_after_failure → 目標の決定。
ほかの用途は数えない。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ailoveshen.application.ports.output.text_generator import ToolChoice
from ailoveshen.application.use_cases.goal_vocabulary import parse_decision
from ailoveshen.application.use_cases.tool_catalog import ToolCallError, parse_tool_call
from ailoveshen.infrastructure.adapters.local_llm.schema_check import extract_json

TOOL_PURPOSES = ("tool", "tool_after_failure", "tool_stuck")
GOAL_PURPOSES = ("goal", "goal_after_failure")
ACCEPTED = "accepted"
REJECTED = "rejected"
NO_ANSWER = "no_answer"  # 呼び出しが失敗したか、答えが空・道具を呼ばなかった（検証の前の問題）
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "logs" / "generations"


@dataclass(frozen=True)
class Verdict:
    """記録 1 件を今の検証に通した結果。"""

    purpose: str
    outcome: str
    reason: str = ""


def read_entries(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    """記録のファイルから 1 件ずつ。壊れた行は飛ばす（書いている途中の最後の行など）。"""
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def replay(entry: dict[str, Any]) -> Verdict | None:
    """記録 1 件を検証に通す。対象の用途でなければ None。"""
    purpose = entry.get("purpose") or "default"
    if purpose in TOOL_PURPOSES:
        return _replay_tool(purpose, entry)
    if purpose in GOAL_PURPOSES:
        return _replay_goal(purpose, entry)
    return None


def _replay_tool(purpose: str, entry: dict[str, Any]) -> Verdict:
    calls = entry.get("tool_calls") or []
    if entry.get("error") or not calls:
        return Verdict(purpose, NO_ANSWER, entry.get("error") or "no tool call")
    call = calls[0]
    args = call.get("args")
    if isinstance(args, str):
        # ローカルのサーバーは引数を JSON の文字列で返す
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Verdict(purpose, REJECTED, f"args are not JSON: {e.msg}")
    if not isinstance(args, dict):
        return Verdict(purpose, REJECTED, "args are not an object")
    try:
        parse_tool_call(ToolChoice(name=str(call.get("name")), args=args))
    except ToolCallError as e:
        return Verdict(purpose, REJECTED, str(e))
    return Verdict(purpose, ACCEPTED)


def _replay_goal(purpose: str, entry: dict[str, Any]) -> Verdict:
    output = entry.get("output")
    if entry.get("error") or not output:
        return Verdict(purpose, NO_ANSWER, entry.get("error") or "empty output")
    try:
        data = extract_json(output)  # 配信と同じ取り出し（コードの囲み、前後の文、思考を除く）
    except ValueError as e:
        return Verdict(purpose, REJECTED, f"output is not JSON: {e}")
    if not isinstance(data, dict):
        return Verdict(purpose, REJECTED, "output is not an object")
    try:
        parse_decision(data)
    except (ValueError, KeyError, TypeError) as e:
        return Verdict(purpose, REJECTED, f"{type(e).__name__}: {e}")
    return Verdict(purpose, ACCEPTED)


def _shape(reason: str) -> str:
    """理由から数や名前を落として、同じ種類をまとめる鍵にする。"""
    return re.sub(r"'[^']*'|\d+", "…", reason)[:100]


def summarize(verdicts: Iterable[Verdict]) -> dict[str, dict[str, Any]]:
    """用途ごとの件数と、断られた理由の種類ごとの数。"""
    out: dict[str, dict[str, Any]] = defaultdict(
        lambda: {ACCEPTED: 0, REJECTED: 0, NO_ANSWER: 0, "reasons": Counter()}
    )
    for v in verdicts:
        row = out[v.purpose]
        row[v.outcome] += 1
        if v.outcome == REJECTED:
            row["reasons"][_shape(v.reason)] += 1
    return dict(out)


def _format(summary: dict[str, dict[str, Any]], show: int) -> str:
    if not summary:
        return "対象の記録がない（purpose が tool* / goal* の呼び出し）"
    lines = []
    for purpose in sorted(summary):
        row = summary[purpose]
        total = row[ACCEPTED] + row[REJECTED] + row[NO_ANSWER]
        answered = row[ACCEPTED] + row[REJECTED]
        rate = f"{row[REJECTED] / answered:.1%}" if answered else "-"
        lines.append(
            f"{purpose}: {total} 件（受け入れ {row[ACCEPTED]}、断られた {row[REJECTED]}（{rate}）、"
            f"答えなし {row[NO_ANSWER]}）"
        )
        for reason, n in row["reasons"].most_common(show):
            lines.append(f"    {n:4d} × {reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path, help="記録のファイル（既定: logs/generations/gen-*.jsonl）")
    parser.add_argument("--purpose", help="数える用途（カンマ区切り。既定は tool* と goal*）")
    parser.add_argument("--show", type=int, default=5, help="用途ごとに出す断られた理由の種類の数")
    parser.add_argument("--export", type=Path, help="断られた記録をこのファイルに JSON Lines で書く（回帰テストの元）")
    args = parser.parse_args(argv)

    files = args.files or sorted(DEFAULT_DIR.glob("gen-*.jsonl"))
    if not files:
        print(f"記録がない: {DEFAULT_DIR}（gemini.generation_log_dir。配信を動かすと書かれる）", file=sys.stderr)
        return 1
    wanted = set(args.purpose.split(",")) if args.purpose else None
    verdicts: list[Verdict] = []
    rejected: list[dict[str, Any]] = []
    for entry in read_entries(files):
        verdict = replay(entry)
        if verdict is None or (wanted and verdict.purpose not in wanted):
            continue
        verdicts.append(verdict)
        if verdict.outcome == REJECTED:
            rejected.append({**{k: entry.get(k) for k in ("purpose", "output", "tool_calls")}, "reason": verdict.reason})
    print(_format(summarize(verdicts), args.show))
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejected), encoding="utf-8"
        )
        print(f"断られた {len(rejected)} 件を {args.export} に書いた")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
