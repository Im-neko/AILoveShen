#!/usr/bin/env python3
"""
ブリッジが手を断る・失敗させる箇所（ゲート）を全部挙げ、ログに出た回数を付ける（docs/design/123）。

    python tools/gate_inventory.py                       # 一覧（JSON Lines）を標準出力へ
    python tools/gate_inventory.py --markdown            # 表（Markdown）
    python tools/gate_inventory.py --out gates.jsonl     # ファイルへ

対象: `minecraft-bridge/src/*.mjs` の `refuse(`（道具の事前チェック）、`throw new Error|Refused|FacilityError(`
（動きの途中の失敗）。箇所ごとに、ファイル・行・囲んでいる関数・直前の条件・メッセージの型を取り、
`logs/steps/*.jsonl` の結果の文と照らして回数を数える（メッセージの `${...}` は任意の文字列として照合する）。
短すぎる型（固定部分が 12 文字未満）は、ほかの文と区別できないので回数を数えない（null）。
分類（安全・検証・作業知識・台帳）は人が付ける: 表の元はこの一覧で、分類は docs/design/123 に置く。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "minecraft-bridge" / "src"
STEPS = REPO / "logs" / "steps"

SITE = re.compile(r"\b(refuse|throw new (?:Error|Refused|FacilityError))\(\s*([`'\"])")
FUNC = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s{2,6}(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{\s*$")
MIN_FIXED = 12


def literal(text: str, start: int, quote: str) -> str:
    """start（引用符の直後）から、対応する引用符までの文字列。テンプレート内の ${...} の入れ子は飛ばす。"""
    depth = 0
    out: list[str] = []
    i = start
    while i < len(text):
        ch = text[i]
        if quote == "`" and ch == "$" and text[i + 1 : i + 2] == "{":
            depth += 1
            out.append("${")
            i += 2
            continue
        if depth:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                out.append("}")
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if ch == "\\":
            out.append(text[i + 1 : i + 2])
            i += 2
            continue
        if ch == quote:
            break
        out.append(ch)
        i += 1
    return "".join(out)


def template_regex(message: str) -> re.Pattern[str] | None:
    parts = re.split(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", message)
    fixed = "".join(parts)
    if len(fixed.strip()) < MIN_FIXED:
        return None
    body = ".+?".join(re.escape(p) for p in parts)
    return re.compile(body, re.S)


CONST = re.compile(r"^(?:export\s+)?const\s+([A-Z][A-Z0-9_]+)\s*=\s*(['\"`])", re.M)


def constants() -> dict[str, str]:
    """全部大文字の定数（文字列）。メッセージの ${NAME} をその中身に置き換えて、型を区別できるようにする。"""
    out: dict[str, str] = {}
    for path in sorted(SRC.glob("*.mjs")):
        text = path.read_text()
        for m in CONST.finditer(text):
            value = literal(text, m.end(), m.group(2))
            if "${" not in value:
                out.setdefault(m.group(1), value)
    return out


def sites() -> list[dict]:
    consts = constants()
    out: list[dict] = []
    for path in sorted(SRC.glob("*.mjs")):
        lines = path.read_text().split("\n")
        text = "\n".join(lines)
        offsets = [0]
        for ln in lines:
            offsets.append(offsets[-1] + len(ln) + 1)
        func = ""
        for no, line in enumerate(lines, 1):
            m = FUNC.match(line)
            if m and not line.lstrip().startswith(("if ", "for ", "while ", "switch ", "catch ", "else")):
                func = m.group(1) or m.group(2) or func
            for s in SITE.finditer(line):
                quote = s.group(2)
                start = offsets[no - 1] + s.end()
                message = re.sub(r"\$\{([A-Z][A-Z0-9_]+)\}", lambda m: consts.get(m.group(1), m.group(0)), literal(text, start, quote))
                cond = line[: s.start()].strip()
                if not cond.startswith("if") and no > 1 and not cond:
                    cond = lines[no - 2].strip()
                out.append({
                    "file": path.name,
                    "line": no,
                    "function": func,
                    "kind": "refuse" if s.group(1) == "refuse" else "throw",
                    "condition": cond[:200],
                    "message": message[:240],
                })
    return out


TOOL_LINE = re.compile(r"\[tool\] \S+ .*?: (?:断った refused|失敗 failed): (.*)$")
BRIDGE_LOG = REPO / "logs" / "bridge.log"


def results() -> list[str]:
    """道具の呼び出しの失敗・断りの文。ブリッジのログの [tool] 行（do_suggestion も 1 回ずつ。[act] 行は重複なので使わない）。"""
    out: list[str] = []
    if BRIDGE_LOG.exists():
        for line in BRIDGE_LOG.read_text(errors="replace").split("\n"):
            m = TOOL_LINE.search(line)
            if m:
                out.append(m.group(1))
    return out


def step_results() -> list[str]:
    out: list[str] = []
    for f in sorted(STEPS.glob("*.jsonl")):
        for line in f.read_text().split("\n"):
            if '"event": "result"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("ok"):
                continue
            r = d.get("result")
            if isinstance(r, str):
                out.append(r)
    return out


def count(rows: list[dict], texts: list[str]) -> None:
    patterns = [(i, template_regex(r["message"])) for i, r in enumerate(rows)]
    hits: Counter[int] = Counter()
    for t in texts:
        for i, rx in patterns:
            if rx is not None and rx.search(t):
                hits[i] += 1
    for i, r in enumerate(rows):
        r["count"] = hits[i] if patterns[i][1] is not None else None


def markdown(rows: list[dict]) -> str:
    out = ["| ファイル:行 | 関数 | 種類 | 条件 | メッセージ | 回数 |", "|---|---|---|---|---|---|"]
    for r in rows:
        esc = lambda s: s.replace("|", "\\|").replace("`", "'")  # noqa: E731
        c = "-" if r["count"] is None else str(r["count"])
        out.append(f"| {r['file']}:{r['line']} | {r['function']} | {r['kind']} | {esc(r['condition'])} | {esc(r['message'])} | {c} |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    rows = sites()
    count(rows, results())
    body = markdown(rows) if args.markdown else "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    if args.out:
        Path(args.out).write_text(body + "\n")
    else:
        sys.stdout.write(body + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
