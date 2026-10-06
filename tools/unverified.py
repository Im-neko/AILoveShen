#!/usr/bin/env python3
"""
実機でまだ確かめていないことの一覧を、docs/WORKLOG.md から作る（docs/design/90）。「未確認」が散らばった
文章のままだと、確かめていないものがどれだけ溜まっているかが数えられない。この一覧は WORKLOG から
毎回作るので、別の一覧が WORKLOG とずれることはない。

    python tools/unverified.py                  # 古い順に、記録ごとの件数と未確認の行
    python tools/unverified.py --since 2026-10-01 --brief
    python tools/unverified.py --worklog docs/WORKLOG.md --today 2026-10-07

数えるもの: `### ` の見出しに日付（YYYY-MM-DD）がある記録のうち、見出しか本文に「未確認」を含み、見出しに
「確認済み」を含まないもの。
- 見出しに「未確認」: 記録の全体がまだ確かめられていない
- 本文だけに「未確認」: 確かめたが残りがある（「実機未確認: …」の行）。件数としては上限で、記録の全体が
  未確認という意味ではない

実機で確かめたら、その記録の見出しの「未確認」を「確認済み（日付）」に書き換える。そうすればここから消える。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

WORKLOG = Path(__file__).resolve().parents[1] / "docs" / "WORKLOG.md"
DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
MARK = "未確認"
DONE = "確認済み"


@dataclass(frozen=True)
class Entry:
    """WORKLOG の 1 つの記録（`### ` の見出し 1 つ）。"""

    day: date
    title: str
    whole: bool  # 見出しに「未確認」（記録の全体が未確認）
    lines: tuple[str, ...]  # 本文のうち「未確認」を含む行


def parse(text: str) -> list[Entry]:
    """見出しに日付があり、未確認が残っている記録（確認済みは除く）。"""
    entries = []
    for block in re.split(r"(?m)^### ", text)[1:]:
        title, _, body = block.partition("\n")
        found = DATE.search(title)
        if not found or DONE in title:
            continue
        lines = tuple(line.strip() for line in body.splitlines() if MARK in line)
        whole = MARK in title
        if not whole and not lines:
            continue
        entries.append(Entry(date.fromisoformat(found.group(1)), title.strip(), whole, lines))
    return entries


def render(entries: list[Entry], today: date, brief: bool) -> str:
    if not entries:
        return "未確認の記録はない"
    out = []
    whole = [e for e in entries if e.whole]
    out.append(
        f"未確認が残っている記録 {len(entries)} 件（全体が未確認 {len(whole)}、本文に残りがある "
        f"{len(entries) - len(whole)}）。最も古い {min(e.day for e in entries)}（{(today - min(e.day for e in entries)).days} 日前）"
    )
    by_age: dict[str, int] = {"7 日以内": 0, "8〜14 日": 0, "15 日より前": 0}
    for e in entries:
        age = (today - e.day).days
        by_age["7 日以内" if age <= 7 else "8〜14 日" if age <= 14 else "15 日より前"] += 1
    out.append("  古さ: " + "、".join(f"{k} {v}" for k, v in by_age.items()))
    if brief:
        return "\n".join(out)
    for e in sorted(entries, key=lambda e: e.day):
        out.append(f"\n{'【全体】' if e.whole else '【残り】'} {e.title[:110]}（{(today - e.day).days} 日前）")
        for line in e.lines[:3]:
            out.append(f"    {line[:160]}")
        if len(e.lines) > 3:
            out.append(f"    …ほか {len(e.lines) - 3} 行")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--worklog", type=Path, default=WORKLOG)
    parser.add_argument("--since", help="この日（YYYY-MM-DD）以降の記録だけ")
    parser.add_argument("--today", help="年齢の基準日（既定: 今日）")
    parser.add_argument("--brief", action="store_true", help="件数だけ")
    args = parser.parse_args(argv)
    if not args.worklog.is_file():
        print(f"{args.worklog} がない", file=sys.stderr)
        return 1
    entries = parse(args.worklog.read_text(encoding="utf-8"))
    if args.since:
        since = date.fromisoformat(args.since)
        entries = [e for e in entries if e.day >= since]
    print(render(entries, date.fromisoformat(args.today) if args.today else date.today(), args.brief))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
