#!/usr/bin/env python3
"""
Irodori-TTS の参照音声（声のお手本）を用意する。Style-Bert-VITS2 の学習に使った音声から、きれいな
長さのクリップを選んで置き場に写し、voices.json に声として書く（docs/setup/irodori_tts.md）。

    python tools/irodori_voice.py [--source Style-Bert-VITS2/Data/shen/wavs] [--name shen]
        [--seconds 60] [--out data/irodori_voices] [files.wav ...]

- 音声ファイルを直接渡すと、それだけを使う（順番もそのまま）
- 渡さなければ --source の WAV から、3〜12 秒のものを長い順に、合計 --seconds まで選ぶ
  （学習なしのクローンは 30 秒ほどで効果の大半、最大 120 秒）
- 起動中のサーバーは声の一覧を読み直す必要がある: サーバーを起動し直す
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = [
    REPO / "Style-Bert-VITS2" / "Data" / "shen" / "wavs",
    REPO / "Style-Bert-VITS2" / "Data" / "shen" / "raw",
]
MIN_CLIP = 3.0
MAX_CLIP = 12.0
MAX_TOTAL = 120.0


def duration(path: Path) -> float | None:
    """WAV の長さ（秒）。読めなければ None。"""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, OSError, EOFError):
        return None


def pick(clips: list[tuple[Path, float]], seconds: float) -> list[tuple[Path, float]]:
    """ほどよい長さのクリップを長い順に、合計 seconds まで選ぶ（名前順に並べ直す）。"""
    usable = sorted((c for c in clips if MIN_CLIP <= c[1] <= MAX_CLIP), key=lambda c: -c[1])
    chosen: list[tuple[Path, float]] = []
    total = 0.0
    for path, d in usable:
        if total + d > seconds:
            continue
        chosen.append((path, d))
        total += d
    return sorted(chosen, key=lambda c: c[0].name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="使う音声ファイル（省略: --source から選ぶ）")
    ap.add_argument("--source", type=Path, help="学習データの WAV のフォルダー")
    ap.add_argument("--name", default="shen", help="声の ID（config の tts.irodori.voice）")
    ap.add_argument("--seconds", type=float, default=60.0, help="合計の長さの上限（秒、最大 120）")
    ap.add_argument("--out", type=Path, default=REPO / "data" / "irodori_voices", help="参照音声の置き場")
    args = ap.parse_args()

    seconds = min(args.seconds, MAX_TOTAL)
    if args.files:
        chosen = [(f, duration(f) or 0.0) for f in args.files]
        missing = [str(f) for f in args.files if not f.exists()]
        if missing:
            print(f"見つからない: {', '.join(missing)}", file=sys.stderr)
            return 1
    else:
        source = args.source or next((s for s in DEFAULT_SOURCES if s.is_dir()), None)
        if source is None or not source.is_dir():
            print(
                "学習データのフォルダーが見つからない。--source で WAV のフォルダーを渡すか、"
                "音声ファイルを直接渡す",
                file=sys.stderr,
            )
            return 1
        clips = [(p, d) for p in sorted(source.glob("*.wav")) if (d := duration(p)) is not None]
        chosen = pick(clips, seconds)
        if not chosen:
            print(f"{source} に {MIN_CLIP:.0f}〜{MAX_CLIP:.0f} 秒の WAV がない", file=sys.stderr)
            return 1

    folder = args.out / args.name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    refs = []
    for path, _ in chosen:
        target = folder / path.name
        shutil.copy2(path, target)
        refs.append(f"{args.name}/{path.name}")

    index = args.out / "voices.json"
    voices = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
    voices[args.name] = {"ref_wavs": refs}
    index.write_text(json.dumps(voices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    total = sum(d for _, d in chosen)
    print(f"声 {args.name!r}: {len(refs)} 個、合計 {total:.0f} 秒 → {folder}")
    print(f"voices.json: {index}")
    if total and total < 20:
        print("注意: 20 秒より短い。似せるには 30 秒以上がよい", file=sys.stderr)
    print("サーバーを起動し直すと使える（scripts/irodori/start_mac.sh）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
