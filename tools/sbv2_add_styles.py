#!/usr/bin/env python3
"""
Irodori-TTS が感情をつけて読んだ音声（tools/irodori_from_sbv2.py emotions）から、Style-Bert-VITS2 の
モデルに感情のスタイル（Happy / Surprised / Sad / Angry / Fear）を足す（docs/setup/irodori_tts.md §8）。

学習し直さない: Style-Bert-VITS2 のスタイルは、音声から取った 256 次元のベクトル（pyannote の wespeaker）の
平均。今のスタイル（Neutral など）はそのまま残し、同じ名前のスタイルだけ置き換えるか足す。公式の WebUI の
「スタイル作成 → 方法0（サブフォルダごと）」は Neutral をフォルダー全体の平均で作り直してしまうので使わない。

Style-Bert-VITS2 の学習に使った環境（pyannote が入っている）の Python で動かす:

    <Style-Bert-VITS2>/venv/bin/python tools/sbv2_add_styles.py [--sbv2 Style-Bert-VITS2] [--model shen]
        [--from shen_sbv2] [--emotions happy,surprised,sad,angry,scared] [--dry-run]

- 読む音声: data/irodori_train/<from>_<感情>/audio/*.wav（聞いて、違う感情のものは消しておく）
- 書く: <sbv2>/model_assets/<model>/style_vectors.npy と config.json（前のものは *.<日時>.bak）
- そのあと TTS サーバーを起動し直す（cd docker && docker compose restart）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STYLES = {"happy": "Happy", "surprised": "Surprised", "sad": "Sad", "angry": "Angry", "scared": "Fear"}


def accept_old_hub_arguments() -> None:
    """
    pyannote.audio（style_gen が使う）の古い版は hf_hub_download(use_auth_token=...) と呼ぶが、新しい
    huggingface_hub にはその引数がない（「unexpected keyword argument 'use_auth_token'」で読めなかった）。
    pyannote を読み込む前に、use_auth_token を token に読み替える形に差し替える。
    """
    import functools

    import huggingface_hub
    from huggingface_hub import file_download

    original = huggingface_hub.hf_hub_download
    if getattr(original, "_accepts_use_auth_token", False):
        return

    @functools.wraps(original)
    def hf_hub_download(*args, use_auth_token=None, **kwargs):
        if use_auth_token is not None and "token" not in kwargs:
            kwargs["token"] = None if use_auth_token is True else use_auth_token
        return original(*args, **kwargs)

    hf_hub_download._accepts_use_auth_token = True  # type: ignore[attr-defined]
    huggingface_hub.hf_hub_download = hf_hub_download
    file_download.hf_hub_download = hf_hub_download


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sbv2", type=Path, default=REPO / "Style-Bert-VITS2", help="Style-Bert-VITS2 のフォルダー")
    ap.add_argument("--model", default="shen", help="model_assets/<model>")
    ap.add_argument("--from", dest="source", default="shen_sbv2", help="data/irodori_train/<from>_<感情>/audio")
    ap.add_argument("--emotions", default=",".join(STYLES), help="足す感情（カンマ区切り）")
    ap.add_argument("--min-clips", type=int, default=5, help="これより少ない感情は足さない")
    ap.add_argument("--dry-run", action="store_true", help="書かずに、使う音声とスタイルの一覧だけ")
    args = ap.parse_args()

    sbv2 = args.sbv2.expanduser().resolve()
    assets = sbv2 / "model_assets" / args.model
    config_path, vectors_path = assets / "config.json", assets / "style_vectors.npy"
    if not config_path.exists() or not vectors_path.exists():
        print(f"{assets} に config.json と style_vectors.npy がない（--sbv2 と --model を確かめる）", file=sys.stderr)
        return 1

    clips: dict[str, list[Path]] = {}
    for e in [x.strip() for x in args.emotions.split(",") if x.strip()]:
        if e not in STYLES:
            print(f"知らない感情: {e}（{', '.join(STYLES)}）", file=sys.stderr)
            return 1
        folder = REPO / "data" / "irodori_train" / f"{args.source}_{e}" / "audio"
        files = sorted(folder.glob("*.wav"))
        if len(files) < args.min_clips:
            print(f"{e}: {folder} の WAV が {len(files)} 個（{args.min_clips} 個より少ない）: 足さない")
            continue
        clips[e] = files
        print(f"{e} → スタイル {STYLES[e]}: {len(files)} 個")
    if not clips:
        print("足せる感情がない: 先に python tools/irodori_from_sbv2.py emotions --source irodori", file=sys.stderr)
        return 1
    if args.dry_run:
        return 0

    # Style-Bert-VITS2 の style_gen（読み込むときに埋め込みのモデルを用意する）。そのフォルダーで動かす
    os.chdir(sbv2)
    sys.path.insert(0, str(sbv2))
    try:
        import numpy as np

        accept_old_hub_arguments()
        from style_gen import get_style_vector
    except Exception as e:  # noqa: BLE001 - 環境が違う
        print(
            f"Style-Bert-VITS2 の style_gen を読めない: {e}\n"
            "Style-Bert-VITS2 の学習に使った環境の Python で動かす（例: <Style-Bert-VITS2>/venv/bin/python）",
            file=sys.stderr,
        )
        return 1

    config = json.loads(config_path.read_text(encoding="utf-8"))
    style2id: dict[str, int] = dict(config["data"]["style2id"])
    vectors = np.load(vectors_path)
    rows = [vectors[i] for i in range(vectors.shape[0])]
    for e, files in clips.items():
        embs = []
        for f in files:
            v = np.asarray(get_style_vector(str(f)))
            if np.isnan(v).any():
                print(f"  {f.name}: NaN のため除いた")
                continue
            embs.append(v)
        if not embs:
            continue
        mean = np.mean(np.stack(embs), axis=0)
        name = STYLES[e]
        if name in style2id:
            rows[style2id[name]] = mean
            print(f"  {name}: 置き換えた（{len(embs)} 個の平均）")
        else:
            style2id[name] = len(rows)
            rows.append(mean)
            print(f"  {name}: 足した（{len(embs)} 個の平均）")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for path in (config_path, vectors_path):
        shutil.copy2(path, path.with_name(f"{path.name}.{stamp}.bak"))
    np.save(vectors_path, np.stack(rows, axis=0))
    config["data"]["style2id"] = style2id
    config["data"]["num_styles"] = len(rows)
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n→ {vectors_path}（{len(rows)} スタイル: {', '.join(style2id)}）。前のものは *.{stamp}.bak")
    print("次に: cd docker && docker compose restart （TTS サーバーがスタイルを読み直す）")
    print("使う（config/development.yaml）:\ntts:\n  emotion_style_map:\n    neutral: \"Neutral\"")
    for e in STYLES:
        print(f"    {e}: \"{STYLES[e] if e in clips or STYLES[e] in style2id else 'Neutral'}\"")
    print(f"    excited: \"{'Happy' if 'Happy' in style2id else 'Neutral'}\"")
    print("  synthesis:\n    style_weight: 2.0   # 感情の強さ（1 で弱ければ上げる。上げすぎると声が崩れる）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
