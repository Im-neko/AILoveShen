#!/usr/bin/env python3
"""
教訓帳から「手引きに上げる候補」を一覧にする（docs/design/201 §8 の昇格）。教訓は経験則（確かめていない）で、
手引きは確かな知識。何度も効いた教訓（`proven`: 効いた 2 回以上で、効かなかった数より多い）は、手引きに
書く価値があるかもしれない。この一覧を見て、書くものはユーザーが手引き（`guides/<名前>/SKILL.md`）に取り込む。

    python tools/lesson_promotion.py                   # 効いた教訓の候補（手引きにありそうなものは「済み?」）
    python tools/lesson_promotion.py --all             # 効いた教訓でないものも含める
    python tools/lesson_promotion.py --min-helped 5    # 効いた回数の下限
    python tools/lesson_promotion.py --pruned          # 整理で消えた教訓（logs/lessons の moment: pruned）を、手引きにあるかと一緒に

整理（効かない教訓の削除、教訓帳がいっぱいのときの削除）で消えた教訓は、効かなかっただけで間違いとは限らない
（確かな事実が消えることがある）。`--pruned` は、消えた教訓のうち手引きにまだ無いもの（候補）と、あるもの（済み?）を分ける。

読むだけで、教訓帳も手引きも書き換えない。「済み?」は、手引きの行に文が似ているもの（目安）。
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAME_AS_GUIDE = 0.5


def guide_lines(guides_dir: Path) -> list[str]:
    """手引きの本文を、短い行に分けたもの。"""
    lines: list[str] = []
    for path in sorted(guides_dir.glob("*/SKILL.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip(" -*#|")
            if len(line) >= 8:
                lines.append(line)
    return lines


def covered_by(text: str, lines: list[str]) -> float:
    """教訓の文が、手引きのどれかの行にどれだけ似ているか（0〜1）。"""
    return max((difflib.SequenceMatcher(None, text, line).ratio() for line in lines), default=0.0)


def candidates(lessons: list[dict], lines: list[str], min_helped: int, everything: bool) -> list[dict]:
    out = []
    for n in lessons:
        proven = n["helped"] >= min_helped and n["helped"] > n["failed"]
        if not (proven or everything):
            continue
        out.append({**n, "proven": proven, "covered": covered_by(n["text"], lines)})
    out.sort(key=lambda n: (n["proven"], n["helped"] - n["failed"], n["taught"]), reverse=True)
    return out


def pruned_lessons(logs_dir: Path) -> list[dict]:
    """記録（logs/lessons）にある、整理で消えた教訓（重ならないよう id と文で 1 件に）。"""
    found: dict[tuple[str, str], dict] = {}
    for path in sorted(logs_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("moment") == "pruned":
                n = {**row["lesson"], "why": row.get("why", "")}
                found[(n["id"], n["text"])] = n
    return list(found.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lessons", type=Path, default=ROOT / "data" / "lessons.json")
    parser.add_argument("--guides", type=Path, default=ROOT / "guides")
    parser.add_argument("--min-helped", type=int, default=2)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--pruned", action="store_true")
    parser.add_argument("--logs", type=Path, default=ROOT / "logs" / "lessons")
    args = parser.parse_args()
    if args.pruned:
        lines = guide_lines(args.guides)
        gone = pruned_lessons(args.logs)
        for n in sorted(gone, key=lambda n: n.get("used", 0), reverse=True):
            mark = "済み?" if covered_by(n["text"], lines) >= SAME_AS_GUIDE else "候補"
            print(f"[{mark}] {n['id']} ({n['why']}) 思い出した {n.get('used', 0)} / 効いた {n.get('helped', 0)} — {n['text']}")
        print(f"\n{len(gone)} 件", file=sys.stderr)
        return 0
    data = json.loads(args.lessons.read_text(encoding="utf-8"))
    found = candidates(data["lessons"], guide_lines(args.guides), args.min_helped, args.all)
    for n in found:
        mark = "済み?" if n["covered"] >= SAME_AS_GUIDE else "候補"
        where = " ".join(f"{k}={v}" for k, v in n["situation"].items()) or "いつでも"
        print(
            f"[{mark}] {n['id']} ({where}) 効いた {n['helped']} / 効かなかった {n['failed']} / 教わった {n['taught']}"
            f" — {n['text']}" + (f"（{n['condition']}）" if n.get("condition") else "")
        )
    print(f"\n{len(found)} 件（教訓帳 {len(data['lessons'])} 件）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
