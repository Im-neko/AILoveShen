#!/usr/bin/env python3
"""
記録した LLM の呼び出しから、プロンプトキャッシュに効く「先頭からの一致」を見る（docs/design/177）。

Claude のキャッシュは先頭からの完全一致（ツール定義 → システム指示 → 本文）なので、同じ用途の
隣り合う 2 回の呼び出しで、先頭から何字まで同じかが、固まる上限になる。

    python tools/prompt_prefix.py                                  # 最新の記録、用途ごと
    python tools/prompt_prefix.py --since "2026-10-12 05:21" --purpose goal
    python tools/prompt_prefix.py --diff goal                      # 隣り合う 2 回の違いが始まる節と、違う行

見るもの（用途ごと）:
- 呼び出し数、本文の平均の長さ、隣り合う呼び出しの先頭一致の平均（字数と割合）
- 一致が終わる節（`## ` の見出し）の多い順。ここがキャッシュの境目の候補
- 末尾の一致（同じ用途で毎回同じ尾）。スキーマの説明のように、先頭に近い所へ置けば固まる文が見つかる
- 実測の `cached` / `cache_write` の割合（Claude の呼び出しだけ）

U+E000（CACHE_BREAK）は記録に残らないので、境目の位置は本文の見出しで見る。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "logs" / "generations"
HEADING = re.compile(r"^## .*$", re.M)


def read_entries(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def common_suffix(a: str, b: str, limit: int) -> int:
    n = min(len(a), len(b), limit)
    i = 0
    while i < n and a[-1 - i] == b[-1 - i]:
        i += 1
    return i


def heading_at(text: str, pos: int) -> str:
    """pos を含む節の見出し（なければ「（見出しの前）」）。"""
    title = "（見出しの前）"
    for m in HEADING.finditer(text):
        if m.start() > pos:
            break
        title = m.group(0)[3:].split("（")[0].strip()
    return title


def full_text(entry: dict[str, Any]) -> str:
    return (entry.get("system") or "") + "\n" + (entry.get("prompt") or "")


def parse_since(value: str | None) -> str | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace(" ", "T"))
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path, help="記録のファイル（省略時は最新の 1 つ）")
    ap.add_argument("--purpose", help="この用途だけ")
    ap.add_argument("--since", help="この時刻以降（ローカル時刻。例: '2026-10-12 05:21'）")
    ap.add_argument("--diff", metavar="PURPOSE", help="この用途の隣り合う 2 回の違いを行で見る")
    args = ap.parse_args(argv)

    paths = args.paths or sorted(DEFAULT_DIR.glob("*.jsonl"))[-1:]
    since = parse_since(args.since)
    by_purpose: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in read_entries(paths):
        if since and e["at"] < since:
            continue
        by_purpose[e["purpose"]].append(e)

    if args.diff:
        return show_diff(by_purpose.get(args.diff, []))

    print(f"{'purpose':22}{'n':>5}{'len':>8}{'prefix':>8}{'%':>5}{'suffix':>8}{'cached%':>9}{'write%':>8}")
    detail: list[str] = []
    for purpose, rows in sorted(by_purpose.items(), key=lambda kv: -sum(len(full_text(r)) for r in kv[1])):
        if args.purpose and purpose != args.purpose:
            continue
        texts = [full_text(r) for r in rows]
        prefixes = [common_prefix(a, b) for a, b in zip(texts, texts[1:])]
        suffixes = [common_suffix(a, b, 40000) for a, b in zip(texts, texts[1:])]
        mean_len = sum(map(len, texts)) // len(texts)
        mean_prefix = sum(prefixes) // len(prefixes) if prefixes else 0
        mean_suffix = sum(suffixes) // len(suffixes) if suffixes else 0
        tok = [r.get("tokens") or {} for r in rows]
        prompt_tokens = sum(t.get("prompt") or 0 for t in tok)
        cached = 100 * sum(t.get("cached") or 0 for t in tok) / prompt_tokens if prompt_tokens else 0
        written = 100 * sum(t.get("cache_write") or 0 for t in tok) / prompt_tokens if prompt_tokens else 0
        share = 100 * mean_prefix / mean_len if mean_len else 0
        print(f"{purpose:22}{len(rows):>5}{mean_len:>8}{mean_prefix:>8}{share:>5.0f}{mean_suffix:>8}{cached:>9.0f}{written:>8.0f}")
        if args.purpose or len(rows) >= 20:
            ends = Counter(heading_at(b, p) for (a, b), p in zip(zip(texts, texts[1:]), prefixes))
            detail.append(f"\n[{purpose}] 先頭の一致が終わる節（隣り合う {len(prefixes)} 組）")
            for title, n in ends.most_common(6):
                detail.append(f"  {n:>4}  {title}")
    print("\n".join(detail))
    return 0


def show_diff(rows: list[dict[str, Any]]) -> int:
    if len(rows) < 2:
        print("2 回以上の呼び出しがない")
        return 1
    a, b = full_text(rows[-2]), full_text(rows[-1])
    p = common_prefix(a, b)
    print(f"先頭一致 {p} 字 / {len(b)} 字（{heading_at(b, p)} の中で違い始める）")
    la, lb = a.splitlines(), b.splitlines()
    shown = 0
    for x, y in zip(la, lb):
        if x != y:
            print(f"- {x[:160]}\n+ {y[:160]}")
            shown += 1
            if shown >= 12:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
