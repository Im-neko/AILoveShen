#!/usr/bin/env python3
"""
Irodori-TTS が読んだ音声の、最後に付くノイズを切る（docs/voice/README.md の学習し直し）。

Irodori-TTS は固定の長さの潜在表現を作ってから音声にする。サーバーの `trim_tail` は潜在表現が完全に平坦・
ゼロ付近になった点より後ろだけを切るので、そうならないと発話が終わったあとに 0.3〜0.5 秒の低い雑音（-22〜-30dB、
高域が多い）が残る（2026-10-02: 新しい生成の 45%、9/26 の tts_compare の 65%。Style-Bert-VITS2 の出力は 0〜6%）。
そのまま学習に入れると Style-Bert-VITS2 が末尾の雑音まで覚える。

    .venv/bin/python tools/voice_trim_tail.py [--name shen_irodori] [--apply]

- 発話の終わり = 「声の大きさが全体の目安（90 パーセンタイル）の -18dB より上で、高域の割合が小さい」最後の区間。
  その後ろ --margin 秒（既定 0.08）まで残し、--fade 秒（0.03）で消す。雑音は声より高域が多いので、静かな語尾
  （低い音量の母音の余韻）は残り、あとに続く高域の多い雑音は切れる。語尾の摩擦音（し、す）は高域が大半なので、
  高域が 45% 以上で声の目安から 22dB 以内なら声に含める
- 既定は `audio_trimmed/` に書くだけ（元は変えない）。耳で確かめて --apply で `audio/` を置き換える
  （元は `audio_untrimmed/` に残す）
- 切った長さの一覧は `trim.tsv`。切る長さが --max-cut（既定 1.5 秒）を超えるものは切らずに一覧に印を付ける
  （声の途中で終わりと間違えている疑い）
"""

from __future__ import annotations

import argparse
import shutil
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
FRAME_SECONDS = 0.04
HOP_SECONDS = 0.02
HIGH_HZ = 6000
VOICE_BELOW_LEVEL_DB = 18.0   # 声とみなす音量の下限: 目安の音量より何 dB 下まで
VOICE_MAX_HIGH_SHARE = 0.15   # 声とみなす高域（6kHz 超）の割合の上限
# 語尾の摩擦音（し、す、ふ）は高域が大半。雑音（高域 20〜25%）より高域が多く、目安の音量から --22dB 以内なら声にする
# （無音の床は高域が多いが、音量がずっと低い）
FRICATIVE_MIN_HIGH_SHARE = 0.45
FRICATIVE_BELOW_LEVEL_DB = 22.0


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path.name}: 16bit モノラルの WAV だけ読める")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy(), w.getframerate()


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())


def speech_end(samples: np.ndarray, rate: int, *, margin: float = 0.08) -> int:
    """発話の終わり（サンプル番号。そのあとの margin 秒まで含める）。声が見つからなければ全部（変えない）。"""
    x = samples.astype(np.float64) / 32768.0
    frame, hop = int(rate * FRAME_SECONDS), int(rate * HOP_SECONDS)
    if len(x) < frame * 3:
        return len(x)
    count = 1 + (len(x) - frame) // hop
    window = np.hanning(frame)
    freqs = np.fft.rfftfreq(frame, 1 / rate)
    levels = np.empty(count)
    highs = np.empty(count)
    for i in range(count):
        part = x[i * hop : i * hop + frame]
        levels[i] = 20 * np.log10(np.sqrt(np.mean(part**2)) + 1e-9)
        spectrum = np.abs(np.fft.rfft(part * window))
        highs[i] = spectrum[freqs > HIGH_HZ].sum() / (spectrum.sum() + 1e-12)
    reference = np.percentile(levels, 90)
    voiced = ((levels > reference - VOICE_BELOW_LEVEL_DB) & (highs < VOICE_MAX_HIGH_SHARE)) | (
        (levels > reference - FRICATIVE_BELOW_LEVEL_DB) & (highs >= FRICATIVE_MIN_HIGH_SHARE)
    )
    if not voiced.any():
        return len(x)
    last = int(np.nonzero(voiced)[0][-1])
    return min(len(x), last * hop + frame + int(rate * margin))


def trim(samples: np.ndarray, rate: int, *, margin: float, fade: float) -> np.ndarray:
    end = speech_end(samples, rate, margin=margin)
    out = samples[:end].astype(np.float64)
    n = min(int(rate * fade), len(out))
    if n > 0:
        out[-n:] *= np.linspace(1.0, 0.0, n)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="shen_irodori", help="data/irodori_train/<name>/audio")
    ap.add_argument("--margin", type=float, default=0.08, help="発話の終わりのあとに残す秒数")
    ap.add_argument("--fade", type=float, default=0.03, help="終わりを消す秒数")
    ap.add_argument("--max-cut", type=float, default=1.5, help="これより長く切るものは切らずに印を付ける（秒）")
    ap.add_argument("--apply", action="store_true", help="audio/ を置き換える（元は audio_untrimmed/）")
    args = ap.parse_args()

    base = REPO / "data" / "irodori_train" / args.name
    files = sorted((base / "audio").glob("*.wav"))
    if not files:
        print(f"{base / 'audio'} に WAV がない", file=sys.stderr)
        return 1
    out_dir = base / "audio_trimmed"
    out_dir.mkdir(exist_ok=True)

    rows: list[tuple[str, float, float, str]] = []
    for n, path in enumerate(files, 1):
        samples, rate = read_wav(path)
        seconds = len(samples) / rate
        end = speech_end(samples, rate, margin=args.margin)
        cut = (len(samples) - end) / rate
        if cut > args.max_cut:
            rows.append((path.stem, seconds, cut, "too_long"))
            continue
        write_wav(out_dir / path.name, trim(samples, rate, margin=args.margin, fade=args.fade), rate)
        rows.append((path.stem, seconds, cut, "cut" if cut >= 0.02 else "same"))
        if n % 200 == 0:
            print(f"  {n}/{len(files)}")

    with (base / "trim.tsv").open("w", encoding="utf-8") as f:
        f.write("id\tseconds\tcut_seconds\tverdict\n")
        for i, s, c, v in rows:
            f.write(f"{i}\t{s:.2f}\t{c:.2f}\t{v}\n")
    cuts = [c for _, _, c, v in rows if v == "cut"]
    flagged = [i for i, _, _, v in rows if v == "too_long"]
    print(f"{len(rows)} 個: 切った {len(cuts)}（中央値 {np.median(cuts) if cuts else 0:.2f} 秒、最大 {max(cuts) if cuts else 0:.2f} 秒）、"
          f"そのまま {sum(1 for r in rows if r[3] == 'same')}、長すぎて切らなかった {len(flagged)} → {out_dir}、{base / 'trim.tsv'}")
    if flagged:
        print(f"  切らなかった（声の途中で終わりと間違えた疑い。耳で確かめる）: {' '.join(flagged)}")

    if args.apply:
        backup = base / "audio_untrimmed"
        backup.mkdir(exist_ok=True)
        done = 0
        for path in files:
            trimmed = out_dir / path.name
            if not trimmed.exists():
                continue
            shutil.move(str(path), str(backup / path.name))
            shutil.copy2(trimmed, path)
            done += 1
        print(f"audio/ の {done} 個を置き換えた（元は {backup}）")
    else:
        print("置き換えるなら --apply（先に audio_trimmed/ を聞く）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
