#!/usr/bin/env python3
"""
記録した LLM の呼び出しから、プロンプトの大きさとその内訳を見る（docs/design/90）。1 ステップの時間は
入力のトークン数でほぼ決まる（設計書 51 §2）ので、どの節が大きいかを実際の呼び出しで見る。

    python tools/prompt_budget.py                              # logs/generations/*.jsonl の全部、用途ごと
    python tools/prompt_budget.py --purpose tool --since "2026-10-07 00:00"
    python tools/prompt_budget.py logs/generations/gen-20261007T010000Z.jsonl

見るもの（用途ごと）:
- サーバーが報告した入力のトークン数（実測）と、呼び出しにかかった時間の中央値・90 パーセンタイル・最大
- プロンプトを `## ` の見出しで割った節ごとの推定トークンの平均（`estimate_tokens`。日本語は 1 字 1 トークンで、
  実測より約 2 割多い）。「（見出しの前）」は見出しより前の文
- 実測から節の推定の合計を引いた残り（おもに道具の定義と、推定の過大分）と、渡した道具の数

固定部分（道具の定義、ルールの文）の上限は `tests/unit/infrastructure/adapters/prompts/test_context_budget.py`
が守る。ここは、ステップごとに変わる部分（今していること、まわり、直近の道具）を見るためのもの。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ailoveshen.infrastructure.adapters.local_llm.schema_check import estimate_tokens

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "logs" / "generations"
PREAMBLE = "（見出しの前）"


def read_entries(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    """記録のファイルから 1 件ずつ。壊れた行は飛ばす。"""
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def sections(prompt: str) -> dict[str, int]:
    """プロンプトを `## ` の見出しで割り、節ごとの推定トークン。同じ見出しは足す。"""
    out: dict[str, int] = defaultdict(int)
    title = PREAMBLE
    buffer: list[str] = []
    for line in prompt.splitlines():
        if line.startswith("## "):
            out[title] += estimate_tokens("\n".join(buffer))
            title, buffer = line[3:].split("（")[0].strip(), [line]
        else:
            buffer.append(line)
    out[title] += estimate_tokens("\n".join(buffer))
    return {k: v for k, v in out.items() if v}


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def summarize(entries: Iterable[dict[str, Any]], purposes: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """用途ごとに、実測のトークン・時間・節ごとの推定・渡した道具の数をまとめる。"""
    rows: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"real": [], "ms": [], "est": [], "tools": [], "sections": defaultdict(list)}
    )
    for e in entries:
        purpose = e.get("purpose") or "default"
        if e.get("error") or not e.get("prompt") or (purposes and purpose not in purposes):
            continue
        row = rows[purpose]
        by_section = sections(e["prompt"])
        for title, tokens in by_section.items():
            row["sections"][title].append(tokens)
        # 節のない呼び出しの分も 0 として平均に入れる（見出しがない呼び出しもある）
        row["est"].append(sum(by_section.values()) + estimate_tokens(e.get("system") or ""))
        if (e.get("tokens") or {}).get("prompt"):
            row["real"].append(e["tokens"]["prompt"])
        if e.get("elapsed_ms"):
            row["ms"].append(e["elapsed_ms"])
        if e.get("tools"):
            row["tools"].append(len(e["tools"]))
    return dict(rows)


def format_summary(rows: dict[str, dict[str, Any]]) -> str:
    if not rows:
        return "対象の記録がない"
    lines = []
    for purpose in sorted(rows):
        r = rows[purpose]
        n = len(r["est"])
        lines.append(f"{purpose}: {n} 件")
        if r["real"]:
            lines.append(
                "  入力（実測）: 中央値 {:.0f} / p90 {:.0f} / 最大 {:.0f} トークン".format(
                    statistics.median(r["real"]), _percentile(r["real"], 0.9), max(r["real"])
                )
            )
        if r["ms"]:
            lines.append(
                "  時間: 中央値 {:.1f} 秒 / p90 {:.1f} 秒 / 最大 {:.1f} 秒".format(
                    statistics.median(r["ms"]) / 1000, _percentile(r["ms"], 0.9) / 1000, max(r["ms"]) / 1000
                )
            )
        total = sum(sum(v) for v in r["sections"].values())
        for title, values in sorted(r["sections"].items(), key=lambda kv: -sum(kv[1])):
            lines.append(
                f"    {sum(values) / n:7.0f} トークン（平均） {sum(values) / total:5.1%}  {title}"
                + ("" if len(values) == n else f"（{len(values)}/{n} 件にある）")
            )
        lines.append(f"    節の推定の平均 {total / n:.0f}")
        if r["real"] and r["tools"]:
            rest = statistics.mean(r["real"]) - total / n
            lines.append(
                f"    実測の平均 − 節の推定 = {rest:.0f}（おもに道具 {statistics.mean(r['tools']):.0f} 個の定義。"
                "推定は多めなので、実際の定義はこれより少し多い）"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path, help="記録のファイル（既定: logs/generations/gen-*.jsonl）")
    parser.add_argument("--purpose", help="見る用途（カンマ区切り。既定は全部）")
    parser.add_argument("--since", help="この時刻（この機械の時刻）より後の呼び出しだけ")
    args = parser.parse_args(argv)

    files = args.files or sorted(DEFAULT_DIR.glob("gen-*.jsonl"))
    if not files:
        print(f"記録がない: {DEFAULT_DIR}（gemini.generation_log_dir。配信を動かすと書かれる）", file=sys.stderr)
        return 1
    entries: Iterable[dict[str, Any]] = read_entries(files)
    if args.since:
        since = datetime.fromisoformat(args.since).astimezone()
        entries = (e for e in entries if e.get("at") and datetime.fromisoformat(e["at"]).astimezone() >= since)
    print(format_summary(summarize(entries, set(args.purpose.split(",")) if args.purpose else None)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
