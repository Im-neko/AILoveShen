#!/usr/bin/env python3
"""
台本を埋め込んだ、1 ファイルの録音ページを作る（docs/voice/README.md）。

    python tools/build_voice_recorder.py [--out data/voice_recorder/voice_recorder.html]

tools/voice_recorder.template.html の台本の場所に docs/voice/*.tsv を埋め込む。できた HTML は
サーバーなしでブラウザ（Chrome / Edge）で開くだけで録音でき、録った WAV は選んだフォルダーに
（またはブラウザの中に）番号のファイル名（N001.wav …）で残る。録音のあとは
python tools/voice_dataset.py <録音のフォルダー> で学習データにする。台本の文を直したらここで作り直す。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "voice_recorder.template.html"
SCRIPTS = [REPO / "docs" / "voice" / "recording_script.tsv", REPO / "docs" / "voice" / "ita_corpus.tsv"]
PLACEHOLDER = "/*SCRIPT_JSON*/"
COLUMNS = ("id", "section", "text", "note", "ruby")  # 録音のページが使う列（emotion と kana は使わない）


def read_script() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in SCRIPTS:
        with path.open(encoding="utf-8", newline="") as f:
            rows += [{c: row[c] for c in COLUMNS if row.get(c)} for row in csv.DictReader(f, delimiter="\t")]
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("台本の番号が重複している")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "data" / "voice_recorder" / "voice_recorder.html")
    args = ap.parse_args()

    rows = read_script()
    # </script> を含む文が来ても HTML を壊さない
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8")
    if html.count(PLACEHOLDER) != 1:
        print(f"{TEMPLATE.name} に {PLACEHOLDER} が 1 つだけない", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html.replace(PLACEHOLDER, data), encoding="utf-8")
    print(f"{args.out}（{len(rows)} 文、{args.out.stat().st_size // 1024} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
