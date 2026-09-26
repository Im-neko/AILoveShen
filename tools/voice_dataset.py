#!/usr/bin/env python3
"""
録音した台本（docs/voice/recording_script.tsv）を Style-Bert-VITS2 の学習データにする。文字起こし
（Whisper）は要らない: 台本の文をそのまま esd.list に書く（docs/voice/README.md）。

    python tools/voice_dataset.py <録音のフォルダー> [--model shen] [--data Style-Bert-VITS2/Data]
        [--with-long] [--dry-run]

- 録音のファイル名は台本の番号（N001.wav、H012.wav …）。フォルダー分けは自由（下の階層も探す）
- 感情ごとにサブフォルダーに写す: Data/<model>/raw/<スタイル>/<番号>.wav。Style-Bert-VITS2 2.5.0 以降は
  サブフォルダーごとにスタイルができる（Neutral / Happy / Surprised / Sad / Angry / Fear）
- esd.list: `<スタイル>/<番号>.wav|<model>|JP|<文>`（前のものは esd.list.bak に）
- 語り（L…、15 秒前後）は Irodori-TTS の参照音声用なので、学習には入れない（--with-long で入れる）
- 録音のない番号と、台本にないファイルを表示する
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "docs" / "voice" / "recording_script.tsv"
STYLES = {
    "neutral": "Neutral",
    "happy": "Happy",
    "surprised": "Surprised",
    "sad": "Sad",
    "angry": "Angry",
    "scared": "Fear",
}


def read_script(path: Path = SCRIPT) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recordings", type=Path, help="録音（<番号>.wav）のフォルダー")
    ap.add_argument("--model", default="shen", help="モデル名（Data/<model>/）")
    ap.add_argument("--data", type=Path, default=REPO / "Style-Bert-VITS2" / "Data", help="Style-Bert-VITS2 の Data/")
    ap.add_argument("--with-long", action="store_true", help="語り（L…）も学習に入れる")
    ap.add_argument("--dry-run", action="store_true", help="写さずに、足りない録音だけ表示する")
    args = ap.parse_args()

    if not args.recordings.is_dir():
        print(f"{args.recordings} がない", file=sys.stderr)
        return 1
    lines = [ln for ln in read_script() if args.with_long or not ln["id"].startswith("L")]
    files = {p.stem.upper(): p for p in args.recordings.rglob("*.wav")}
    missing = [ln["id"] for ln in lines if ln["id"] not in files]
    known = {ln["id"] for ln in read_script()}
    extra = sorted(stem for stem in files if stem not in known)

    raw = args.data / args.model / "raw"
    esd = args.data / args.model / "esd.list"
    rows = []
    for ln in lines:
        src = files.get(ln["id"])
        if src is None:
            continue
        style = STYLES.get(ln["emotion"], "Neutral")
        rel = f"{style}/{ln['id']}.wav"
        rows.append(f"{rel}|{args.model}|JP|{ln['text']}")
        if not args.dry_run:
            (raw / style).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, raw / rel)

    if not args.dry_run:
        if esd.exists():
            shutil.copy2(esd, esd.with_suffix(".list.bak"))
        esd.parent.mkdir(parents=True, exist_ok=True)
        esd.write_text("\n".join(rows) + "\n", encoding="utf-8")

    per_style: dict[str, int] = {}
    for r in rows:
        style = r.split("/", 1)[0]
        per_style[style] = per_style.get(style, 0) + 1
    print(f"録音 {len(rows)} / 台本 {len(lines)}（{', '.join(f'{k} {v}' for k, v in per_style.items())}）")
    if missing:
        print(f"録音がない: {' '.join(missing)}")
    if extra:
        print(f"台本にないファイル（使わない）: {' '.join(extra)}")
    if not args.dry_run and rows:
        print(f"→ {raw}/<スタイル>/ と {esd}")
        print("次に Style-Bert-VITS2 の WebUI（学習）で、スライスと文字起こしを飛ばして前処理から")
        print("参照音声（Irodori-TTS）: python tools/irodori_voice.py <録音のフォルダー>/L00*.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
