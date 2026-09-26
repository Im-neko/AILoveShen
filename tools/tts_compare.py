#!/usr/bin/env python3
"""
読み上げの方式を聞き比べる。同じ文を Style-Bert-VITS2 と Irodori-TTS で読ませ、WAV と 1 文ごとの
合成時間を並べて書く（docs/setup/irodori_tts.md）。設定は config（tts.server / tts.irodori）の値。

    python tools/tts_compare.py [--engines style_bert_vits2,irodori] [--lines lines.txt]
        [--emotion] [--repeat 1] [--out logs/tts_compare]

- --lines: 1 行 1 文のファイル（省略: 実況でよく出る文。アイテム名、数、英単語、名前、短い相づち）
- --emotion: 感情つきでも読ませる（Irodori-TTS は感情の説明と感情の声、Style-Bert-VITS2 は感情のスタイル）
- --repeat: 同じ文を何回読ませるか（seed を決めていれば同じ音になるかの確認。時間は平均）
- 結果: <out>/<日時>/<方式>/NN.wav と report.md（読み間違いは耳で確かめて report.md に書き込む）
- Minecraft と OBS を起動したまま測ると、配信中の速さに近い
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from ailoveshen.domain.value_objects import EmotionState, EmotionType  # noqa: E402
from ailoveshen.factories.tts import ENGINES, create_synthesizer, describe_engine  # noqa: E402
from ailoveshen.infrastructure.config import load_config_dict  # noqa: E402

# (文, 感情)。感情は --emotion のときだけ使う
LINES: list[tuple[str, EmotionType]] = [
    ("よし、今日も元気にマイクラやっていくよ！", EmotionType.HAPPY),
    ("丸石を64個集めたら、かまどを作るね。", EmotionType.NEUTRAL),
    ("あっ、クリーパーだ！　ちょっと離れよう。", EmotionType.SURPRISED),
    ("オークの原木があと3本足りないんだよね。", EmotionType.NEUTRAL),
    ("作業台でツルハシを作って、石を掘りに行きます。", EmotionType.NEUTRAL),
    ("うわー、ツルハシ壊れちゃった……作り直さなきゃ。", EmotionType.SAD),
    ("夜になっちゃったから、今日はここで寝ようかな。", EmotionType.NEUTRAL),
    ("まめさん、アドバイスありがとう！　やってみるね。", EmotionType.HAPPY),
    ("ゾンビに囲まれた！　こわいこわい！", EmotionType.SCARED),
    ("やったー！　ついにおうちが完成したよ！", EmotionType.EXCITED),
    ("えっと、次は小麦の種を集めたいな。", EmotionType.NEUTRAL),
    ("鉄インゴットが12個あるから、バケツも作れるね。", EmotionType.NEUTRAL),
    ("なんで扉が置けないの、もう！", EmotionType.ANGRY),
    ("うん、うん。なるほどね。", EmotionType.NEUTRAL),
    ("東に200メートルくらい行ってみる。", EmotionType.NEUTRAL),
    ("チェストに木材をしまっておくね。", EmotionType.NEUTRAL),
    ("今のはちょっと危なかったかも。", EmotionType.SCARED),
    ("ねえねえ、羊さんがいるよ！　羊毛もらっちゃおう。", EmotionType.HAPPY),
    ("地下は暗いから、たいまつを置きながら進みます。", EmotionType.NEUTRAL),
    ("コメントありがとう！　ゆっくりしていってね。", EmotionType.HAPPY),
]
STRONG = 0.8


def read_lines(path: Path | None) -> list[tuple[str, EmotionType]]:
    if path is None:
        return LINES
    return [(ln.strip(), EmotionType.NEUTRAL) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


async def run_engine(
    label: str, config: dict, lines: list[tuple[str, EmotionType]], folder: Path, repeat: int, with_emotion: bool
) -> list[tuple[float, str]]:
    synthesizer = create_synthesizer(config)
    folder.mkdir(parents=True, exist_ok=True)
    print(f"[{label}] {describe_engine(config)}")
    await synthesizer.connect()
    rows: list[tuple[float, str]] = []
    try:
        for i, (text, kind) in enumerate(lines, 1):
            emotion = EmotionState(primary=kind, intensity=STRONG) if with_emotion else EmotionState()
            times = []
            for r in range(repeat):
                start = time.perf_counter()
                try:
                    audio = await synthesizer.synthesize(text, emotion)
                except Exception as e:  # noqa: BLE001 - 失敗も表に残す
                    rows.append((float("nan"), f"失敗: {e}"))
                    print(f"  {i:02d} 失敗: {e}")
                    break
                times.append(time.perf_counter() - start)
                name = f"{i:02d}.wav" if repeat == 1 else f"{i:02d}_{r + 1}.wav"
                (folder / name).write_bytes(audio)
            else:
                t = statistics.mean(times)
                rows.append((t, ""))
                print(f"  {i:02d} {t:5.2f}s {text}")
    finally:
        await synthesizer.disconnect()
    return rows


def summary(rows: list[tuple[float, str]]) -> str:
    ok = [t for t, err in rows if not err]
    if not ok:
        return "すべて失敗"
    first, rest = ok[0], ok[1:] or ok
    return (
        f"最初 {first:.2f}s（モデルの準備を含むことがある）、以降の平均 {statistics.mean(rest):.2f}s、"
        f"最大 {max(rest):.2f}s、失敗 {len(rows) - len(ok)}"
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", default="style_bert_vits2,irodori")
    ap.add_argument("--lines", type=Path)
    ap.add_argument("--emotion", action="store_true")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", type=Path, default=REPO / "logs" / "tts_compare")
    args = ap.parse_args()

    base = load_config_dict(REPO / "config").get("tts", {})
    lines = read_lines(args.lines)
    out = args.out / datetime.now().strftime("%Y%m%d-%H%M%S")
    runs: list[tuple[str, dict, bool]] = []
    for engine in [e.strip() for e in args.engines.split(",") if e.strip()]:
        if engine not in ENGINES:
            print(f"知らない方式: {engine}（{', '.join(ENGINES)}）", file=sys.stderr)
            return 1
        config = copy.deepcopy(base)
        config["engine"] = engine
        runs.append((engine, config, False))
        if args.emotion:
            # 感情つき: Irodori-TTS は説明（と tts.irodori.emotion_voices）、Style-Bert-VITS2 はスタイル
            # （tts.emotion_style_map、tools/sbv2_add_styles.py で足したもの）
            emotional = copy.deepcopy(config)
            if engine == "irodori":
                emotional.setdefault("irodori", {})["emotion"] = True
            runs.append((f"{engine}_emotion", emotional, True))

    results: dict[str, list[tuple[float, str]]] = {}
    for label, config, with_emotion in runs:
        try:
            results[label] = await run_engine(label, config, lines, out / label, max(1, args.repeat), with_emotion)
        except Exception as e:  # noqa: BLE001 - つながらない方式は飛ばして残りを比べる
            print(f"[{label}] 使えない: {e}", file=sys.stderr)

    report = [f"# 読み上げの聞き比べ（{out.name}）", ""]
    for label, rows in results.items():
        report.append(f"- **{label}**: {summary(rows)}")
    report += ["", "| # | 文 | " + " | ".join(results) + " | 読み間違い・気づいたこと |", "|---|---|" + "---|" * len(results) + "---|"]
    for i, (text, _) in enumerate(lines):
        cells = []
        for rows in results.values():
            t, err = rows[i] if i < len(rows) else (float("nan"), "なし")
            cells.append(err or f"{t:.2f}s")
        report.append(f"| {i + 1:02d} | {text} | " + " | ".join(cells) + " |  |")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\n結果: {out}/report.md")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
