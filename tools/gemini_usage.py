#!/usr/bin/env python3
"""
配信のログから、Gemini の呼び出しを用途ごとに集計する（回数、トークン、平均の時間、費用の目安）。

    python tools/gemini_usage.py data/logs/ailoveshen-dev.log \
        [--input-price 0.30 --output-price 2.50 --yen 150] [--since "2026-09-25 20:00"]

価格は 100 万トークンあたりのドル（Google の料金表の今の値を入れる。思考のトークンは出力として
数える。キャッシュされた入力は安いが、ここでは入力として数える）。価格を渡さなければトークンだけ。
ログの行は GeminiTextGenerator._log_usage の形。用途（purpose=）が入る前の古いログは、thinking の
深さごとに数える。
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

LINE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Gemini .*?: (?P<ms>\d+)ms, "
    r"purpose=(?P<purpose>\S+) images=(?P<images>\d+) prompt=(?P<prompt>\d+|None) "
    r"cached=(?P<cached>\d+|None) thoughts=(?P<thoughts>\d+|None) output=(?P<output>\d+|None)"
)
# 用途が入る前の行（2026-09-26 より前のログ）: 用途の代わりに thinking の深さで分ける
OLD_LINE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Gemini .*?thinking (?:ThinkingLevel\.)?(?P<level>\w+).*?: "
    r"(?P<ms>\d+)ms, prompt=(?P<prompt>\d+|None) thoughts=(?P<thoughts>\d+|None) "
    r"output=(?P<output>\d+|None)"
)


def _n(value: str) -> int:
    return 0 if value == "None" else int(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini の呼び出しを用途ごとに集計する")
    parser.add_argument("logs", nargs="+", type=Path, help="ログのファイル（data/logs/*.log）")
    parser.add_argument("--input-price", type=float, help="入力 100 万トークンあたりのドル")
    parser.add_argument(
        "--output-price", type=float, help="出力（思考を含む）100 万トークンあたりのドル"
    )
    parser.add_argument("--yen", type=float, default=150.0, help="1 ドルの円（既定 150）")
    parser.add_argument("--since", help="この時刻から（例: 2026-09-25 20:00）")
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since) if args.since else None

    rows: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    first = last = None
    for path in args.logs:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = LINE.search(line)
            if m:
                purpose, images, cached = m["purpose"], int(m["images"]), _n(m["cached"])
            else:
                m = OLD_LINE.search(line)
                if not m:
                    continue
                purpose, images, cached = f"(thinking {m['level']})", 0, 0
            at = datetime.fromisoformat(m["time"])
            if since and at < since:
                continue
            first = at if first is None else min(first, at)
            last = at if last is None else max(last, at)
            r = rows[purpose]
            r["calls"] += 1
            r["ms"] += int(m["ms"])
            r["images"] += images
            r["cached"] += cached
            for key in ("prompt", "thoughts", "output"):
                r[key] += _n(m[key])
    if not rows:
        print("Gemini の使用量の行がない（ログのファイルを確かめる）")
        return

    hours = ((last - first).total_seconds() / 3600) if first and last and last > first else 0
    priced = args.input_price is not None and args.output_price is not None
    print(f"期間: {first} 〜 {last}（{hours:.2f} 時間）")
    head = (
        f"{'用途':<22}{'回数':>6}{'1時間':>7}{'平均秒':>7}"
        f"{'入力/回':>9}{'思考/回':>9}{'出力/回':>8}"
    )
    print(head + ("  費用(円)" if priced else ""))
    total_yen = 0.0
    for purpose, r in sorted(rows.items(), key=lambda kv: -(kv[1]["prompt"] + kv[1]["thoughts"])):
        calls = r["calls"]
        line = (
            f"{purpose:<22}{int(calls):>6}{(calls / hours if hours else 0):>7.0f}"
            f"{r['ms'] / calls / 1000:>7.1f}{r['prompt'] / calls:>9.0f}"
            f"{r['thoughts'] / calls:>9.0f}{r['output'] / calls:>8.0f}"
        )
        if priced:
            usd = (
                r["prompt"] * args.input_price + (r["thoughts"] + r["output"]) * args.output_price
            ) / 1e6
            total_yen += usd * args.yen
            line += f"  {usd * args.yen:>8.0f}"
        print(line)
    if priced:
        print(
            f"合計 {total_yen:.0f} 円"
            + (f"（1 時間あたり {total_yen / hours:.0f} 円）" if hours else "")
        )


if __name__ == "__main__":
    main()
