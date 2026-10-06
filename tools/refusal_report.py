#!/usr/bin/env python3
"""
断られた手（ブリッジの拒否）の数を、理由の種類ごとに集計する（docs/design/90）。`ok` は「実行された」で
あって「よい判断だった」ではない（設計書 82）。断られた手は、1 手分の LLM の時間（約 7〜25 秒）を無駄にした
判断の誤りなので、種類ごとに数えて、直す先（引数の誤りか、状況に合わない道具か）を決める材料にする。

    python tools/refusal_report.py                          # logs/steps/*.jsonl の全部
    python tools/refusal_report.py --since "2026-10-07 00:00" --by tool
    python tools/refusal_report.py logs/steps/watch-20261006T103156Z.jsonl

理由の種類は REASONS の正規表現（最初に当たったもの）。どれにも当たらなければ「その他」: 多いときは
その文面を見て種類を足す。状況（夜・水中）に合わせて道具の一覧を絞る案（設計書 90 §4）は、2026-10-06 の
記録で、夜の籠もり系が全結果の 0.16%、水・閉所系が 0.8% だったので入れなかった。この集計で数字が変われば
考え直す。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "logs" / "steps"
OTHER = "その他"
# (種類, 正規表現)。上から見て最初に当たったものにする。種類は直す先で分ける:
# 引数の誤り（距離・古い対象・材料）、状況（反射・夜・水・閉所）、呼び方（調べものの上限・形）、記録（台帳）
REASONS = [
    ("反射が対処中", re.compile(r"reflex")),
    ("夜の籠もり", re.compile(r"staying inside|for the night|hostile mobs wait")),
    ("水・閉所", re.compile(r"closed space|water|swimming|liquid|holds back")),
    ("距離の間違い・遠すぎ", re.compile(r"m away|minus sign|too far")),
    ("古い対象（もう空気・提案にない）", re.compile(r"nothing to dig|there is air|not a current suggestion")),
    ("材料・食料・道具がない", re.compile(r"cannot craft|needs \d|not food|no food|you have no |drops nothing without|crafting table within reach|no chest is known")),
    ("呼び方（調べものの上限・形）", re.compile(r"no more lookups|invalid call|must be numbers")),
    ("台帳（前に断られた手）", re.compile(r"was tried before")),
]


def read_results(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    """ステップの記録の `result` 行。壊れた行は飛ばす。"""
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("event") == "result":
                    yield row


def reason_of(result: str) -> str:
    for name, pattern in REASONS:
        if pattern.search(result):
            return name
    return OTHER


def summarize(rows: Iterable[dict[str, Any]], by: str = "reason") -> tuple[int, Counter[str]]:
    """(結果の総数, 断られた手の数: 理由の種類ごと、または道具ごと)。"""
    total = 0
    refused: Counter[str] = Counter()
    for row in rows:
        total += 1
        if not row.get("refused"):
            continue
        refused[reason_of(str(row.get("result", ""))) if by == "reason" else str(row.get("tool"))] += 1
    return total, refused


def render(total: int, refused: Counter[str]) -> str:
    if not total:
        return "結果の記録がない"
    count = sum(refused.values())
    lines = [f"結果 {total} 件のうち断られた手 {count} 件（{count / total:.1%}）"]
    for name, n in refused.most_common():
        lines.append(f"  {n:5d}  断られた手の {n / count:5.1%}  全結果の {n / total:5.1%}  {name}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path, help="記録のファイル（既定: logs/steps/*.jsonl）")
    parser.add_argument("--since", help="この時刻（この機械の時刻）より後の結果だけ")
    parser.add_argument("--by", choices=("reason", "tool"), default="reason", help="何ごとに数えるか")
    args = parser.parse_args(argv)

    files = args.files or sorted(DEFAULT_DIR.glob("*.jsonl"))
    if not files:
        print(f"記録がない: {DEFAULT_DIR}", file=sys.stderr)
        return 1
    rows: Iterable[dict[str, Any]] = read_results(files)
    if args.since:
        since = datetime.fromisoformat(args.since).astimezone()
        rows = (r for r in rows if r.get("at") and datetime.fromisoformat(r["at"]).astimezone() >= since)
    print(render(*summarize(rows, args.by)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
