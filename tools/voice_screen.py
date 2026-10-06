#!/usr/bin/env python3
"""
読ませた音声（data/irodori_train/<name>/audio）の中から、途中で別の人の声に流れたクリップを探す
（docs/voice/README.md の学習し直し）。Irodori-TTS は参照音声から声をまねるだけなので、生成の途中で別の話者に
流れることがある（2026-09-26: 「途中で全然関係ない男の人の声が入る」）。長さ・音割れ・読みの速さの確かめ
（irodori_from_sbv2.py）ではわからないので、Style-Bert-VITS2 の style_gen と同じ話者の埋め込みで比べる。

    .venv/bin/python tools/voice_screen.py [--name shen_irodori] [--apply]

- 埋め込み（style_gen.get_style_vector）をクリップごとに出し、全部の中央値の向きとのコサイン類似度を見る。
  まねた声どうしは近く、別の話者は離れる。中央値から MAD の --mads 倍より下、または --min-similarity より
  下のものを「外れ」とする（既定 4 倍・0.30。参照音声そのものとは比べない: まねた音は参照より離れる）
- `screen.tsv`（id、類似度、判定）を書く。--apply で外れの WAV を `audio_rejected/` に移す（学習に入れない。
  耳で確かめて戻したいものは audio/ に戻して再実行）
- 外れは「別人の声」の疑いであって、耳で確かめる代わりにはならない。--list で外れの上位を出す
- Style-Bert-VITS2 の style_gen が動く Python で（`.venv/bin/python`。`Style-Bert-VITS2/venv` は動かない:
  tools/sbv2_add_styles.py と同じ）
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def outliers(similarity: list[float], mads: float, floor: float) -> list[bool]:
    """中央値から MAD の mads 倍より下、または floor より下を True に。"""
    ordered = sorted(similarity)
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    deviations = sorted(abs(s - median) for s in similarity)
    mad = deviations[n // 2] if n % 2 else (deviations[n // 2 - 1] + deviations[n // 2]) / 2
    # MAD が 0（全部同じ）なら中央値からの差では外れにしない
    limit = median - mads * 1.4826 * mad if mad > 0 else float("-inf")
    return [s < limit or s < floor for s in similarity]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="shen_irodori", help="data/irodori_train/<name>/audio")
    ap.add_argument("--sbv2", type=Path, default=REPO / "Style-Bert-VITS2", help="Style-Bert-VITS2 のフォルダー")
    ap.add_argument("--mads", type=float, default=4.0, help="中央値から MAD のこの倍より下を外れに")
    ap.add_argument("--min-similarity", type=float, default=0.30, help="これより下は外れ")
    ap.add_argument("--apply", action="store_true", help="外れの WAV を audio_rejected/ に移す")
    ap.add_argument("--list", type=int, default=15, help="外れの上位を何個表示するか")
    args = ap.parse_args()

    base = REPO / "data" / "irodori_train" / args.name
    files = sorted((base / "audio").glob("*.wav"))
    if len(files) < 10:
        print(f"{base / 'audio'} の WAV が {len(files)} 個（10 個より少ない）", file=sys.stderr)
        return 1

    sbv2 = args.sbv2.expanduser().resolve()
    os.chdir(sbv2)
    sys.path.insert(0, str(sbv2))
    try:
        import numpy as np

        from sbv2_add_styles import accept_old_hub_arguments

        accept_old_hub_arguments()
        from style_gen import get_style_vector
    except Exception as e:  # noqa: BLE001 - 環境が違う
        print(f"Style-Bert-VITS2 の style_gen を読めない: {e}\n.venv/bin/python で動かす", file=sys.stderr)
        return 1

    ids: list[str] = []
    vectors = []
    for n, f in enumerate(files, 1):
        v = np.asarray(get_style_vector(str(f)), dtype=float)
        if np.isnan(v).any() or not np.linalg.norm(v):
            print(f"  {f.name}: 埋め込みが取れない（NaN）: 外れにする")
            ids.append(f.stem)
            vectors.append(None)
            continue
        ids.append(f.stem)
        vectors.append(v / np.linalg.norm(v))
        if n % 100 == 0:
            print(f"  {n}/{len(files)}")
    good = [v for v in vectors if v is not None]
    center = np.median(np.stack(good), axis=0)
    center /= np.linalg.norm(center)
    similarity = [float(v @ center) if v is not None else -1.0 for v in vectors]
    flagged = outliers(similarity, args.mads, args.min_similarity)

    with (base / "screen.tsv").open("w", encoding="utf-8") as f:
        f.write("id\tsimilarity\tverdict\n")
        for i, s, bad in zip(ids, similarity, flagged):
            f.write(f"{i}\t{s:.3f}\t{'outlier' if bad else 'ok'}\n")
    out = [(i, s) for i, s, bad in zip(ids, similarity, flagged) if bad]
    ordered = sorted(similarity)
    print(f"{len(files)} 個: 類似度 最小 {ordered[0]:.2f} / 中央値 {ordered[len(ordered) // 2]:.2f} / 最大 {ordered[-1]:.2f}、外れ {len(out)} 個 → {base / 'screen.tsv'}")
    for i, s in sorted(out, key=lambda x: x[1])[: args.list]:
        print(f"  {i}  {s:.3f}")
    if args.apply and out:
        rejected = base / "audio_rejected"
        rejected.mkdir(exist_ok=True)
        for i, _ in out:
            shutil.move(str(base / "audio" / f"{i}.wav"), str(rejected / f"{i}.wav"))
        print(f"外れの {len(out)} 個を {rejected} に移した")
    elif out:
        print("移すなら --apply（耳で確かめるなら先に聞く）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
