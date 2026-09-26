#!/usr/bin/env python3
"""
Style-Bert-VITS2 で作った（雑音のない）音声から、Irodori-TTS の声を作る（docs/setup/irodori_tts.md §6）。

生の学習データは雑音が多いが、Style-Bert-VITS2 の出力は声はきれい（イントネーションは弱い）。

    # 1. 台本（docs/voice/*.tsv、673 文）を Style-Bert-VITS2 で読ませて WAV にする（TTS サーバーが要る）
    python tools/irodori_from_sbv2.py generate [--name shen_sbv2] [--limit 0] [--by-emotion] [--for-reference]

    # 2a. 学習なし: その中から参照音声を選んで Irodori-TTS の声にする（まずこれを試す）
    python tools/irodori_from_sbv2.py reference [--name shen_sbv2] [--seconds 30]

参照音声（2a）は毎回の合成でモデルが読むので、長いほど合成が遅くなる: 既定は 30 秒（効果の大半）。2a だけなら
generate --for-reference で語りとふつうの文（64 文）だけ作れば足りる。学習用の量（30〜40 分）は 2b の LoRA
のためで、学習した LoRA は小さく、合成の速さはほとんど変わらない（学習データは合成のときには使わない）。

    # 3. 感情ごとの声（shen_sbv2_happy …: tts.irodori.emotion_voices で感情に応じて切り替える）
    python tools/irodori_from_sbv2.py emotions [--source auto|sbv2|irodori] [--emotions happy,sad]

    # 2b. LoRA の追加学習（Irodori-TTS の学習用リポジトリが要る: scripts/irodori/setup_train_mac.sh）
    python tools/irodori_from_sbv2.py base                 # 元のモデルの重みを取ってくる
    python tools/irodori_from_sbv2.py prepare [--device mps]
    python tools/irodori_from_sbv2.py train [--device mps] [--max-steps 3000]

注意: 学習すると Style-Bert-VITS2 のイントネーションの癖も覚えうる。2a（声の質だけを参照音声から、話し方は
Irodori-TTS のまま）で足りればそれがよい。2b は tools/tts_compare.py で聞き比べて、歩数（--max-steps）を
少なめから試す。学習の Mac（MPS）での動作は Irodori-TTS 側では案内されていない（例は CUDA）: 遅すぎる・
動かないときは同じコマンドを GPU のあるマシンで --device cuda で。

出力: data/irodori_train/<name>/audio/<番号>.wav、metadata.csv（file_name,text,speaker）、除いた文の
rejected.tsv、manifest.jsonl と latents/（prepare）、lora/（train）
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import io
import os
import shutil
import subprocess
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
SCRIPTS = [REPO / "docs" / "voice" / "recording_script.tsv", REPO / "docs" / "voice" / "ita_corpus.tsv"]
DEFAULT_TRAIN_REPO = Path(os.environ.get("IRODORI_TRAIN_DIR", Path.home() / "Irodori-TTS"))
DEFAULT_BASE = "Aratako/Irodori-TTS-v4-Small"  # Irodori-TTS-Server の既定と同じ（LoRA は同じ元に載せる）

# 読み上げの出来の目安（外れたものは学習に入れない）
MIN_SECONDS = 0.6
MAX_SECONDS = 20.0
# 音割れ: 最大値の近くに張り付いたサンプルが続く。最大振幅だけでは判定しない（Style-Bert-VITS2 のサーバーは
# 出力を最大値ちょうどに揃えて返すので、どの音声も最大振幅が 1.0 になる: 全部を音割れとして捨てていた）
CLIP_LEVEL = 32767  # 最大値そのもの（なめらかな山は最大値に 1 サンプルしか届かない）
CLIP_RUN = 3
MIN_RMS = 0.005       # これより小さければほぼ無音
KANA_PER_SECOND = (3.0, 14.0)  # 読みの速さ（カナの数 / 秒）がこの外なら、読み飛ばしか間延び


@dataclass(frozen=True)
class Line:
    id: str
    text: str
    emotion: str
    kana: str = ""


def read_lines(extra: Path | None = None) -> list[Line]:
    lines: list[Line] = []
    for path in SCRIPTS:
        if path.exists():
            with path.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f, delimiter="\t"):
                    lines.append(Line(row["id"], row["text"], row.get("emotion") or "neutral", row.get("kana") or ""))
    if extra:
        for i, text in enumerate(t.strip() for t in extra.read_text(encoding="utf-8").splitlines()):
            if text:
                lines.append(Line(f"X{i + 1:04d}", text, "neutral"))
    return lines


def wav_stats(data: bytes) -> tuple[float, float, float, bool]:
    """(秒, 最大振幅, RMS, 音割れ)。16bit PCM を読む（Style-Bert-VITS2 の出力）。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate, width, channels, frames = w.getframerate(), w.getsampwidth(), w.getnchannels(), w.readframes(w.getnframes())
    seconds = len(frames) / (rate * width * channels)
    if width != 2 or not frames:
        return seconds, 0.0, 1.0, False
    samples = array("h", frames)
    peak = max(abs(s) for s in samples) / 32768.0
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768.0
    run = longest = 0
    for s in samples:
        run = run + 1 if abs(s) >= CLIP_LEVEL else 0
        longest = max(longest, run)
    return seconds, peak, rms, longest >= CLIP_RUN


def check(line: Line, seconds: float, peak: float, rms: float, clipped: bool = False) -> str:
    """学習に入れない理由（入れてよければ空）。"""
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        return f"長さ {seconds:.1f} 秒"
    if clipped:
        return "音割れ"
    if rms < MIN_RMS:
        return "ほぼ無音"
    if line.kana:
        speed = len([c for c in line.kana if c not in "。、！？ー・ 　"]) / seconds
        lo, hi = KANA_PER_SECOND
        if not lo <= speed <= hi:
            return f"読みの速さ {speed:.1f} カナ/秒（読み飛ばしか間延び）"
    return ""


def folder(name: str) -> Path:
    return REPO / "data" / "irodori_train" / name


# ---- 1. generate ----

async def generate(args: argparse.Namespace) -> int:
    from ailoveshen.domain.value_objects import EmotionState, EmotionType
    from ailoveshen.factories.tts import create_synthesizer
    from ailoveshen.infrastructure.config import load_config_dict

    config = copy.deepcopy(load_config_dict(REPO / "config").get("tts", {}))
    config["engine"] = "style_bert_vits2"
    synthesis = config.setdefault("synthesis", {})
    # 学習用は揺れを小さく（同じ声で、はっきり）。明示されたものだけ上書き
    for key, value in (("sdp_ratio", args.sdp_ratio), ("noise", args.noise), ("noisew", args.noisew), ("length", args.length)):
        if value is not None:
            synthesis[key] = value
    synthesizer = create_synthesizer(config)
    await synthesizer.connect()

    out = folder(args.name)
    audio = out / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    lines = read_lines(args.texts)
    if args.for_reference:
        # 参照音声の候補だけ（語り L と、ふつうの文 N）
        lines = [line for line in lines if line.id[0] in "LN" and line.id[1:].isdigit()]
    if args.limit:
        lines = lines[: args.limit]
    kept: list[tuple[Line, float]] = []
    rejected: list[tuple[Line, str]] = []
    try:
        for n, line in enumerate(lines, 1):
            target = audio / f"{line.id}.wav"
            if target.exists() and not args.overwrite:
                data = target.read_bytes()
            else:
                kind = EmotionType(line.emotion) if args.by_emotion and line.emotion in EmotionType._value2member_map_ else EmotionType.NEUTRAL
                try:
                    data = await synthesizer.synthesize(line.text, EmotionState(primary=kind, intensity=0.8))
                except Exception as e:  # noqa: BLE001 - 1 文の失敗で止めない
                    rejected.append((line, f"合成の失敗: {e}"))
                    continue
            seconds, peak, rms, clipped = wav_stats(data)
            why = check(line, seconds, peak, rms, clipped)
            if why:
                rejected.append((line, why))
                target.unlink(missing_ok=True)
                continue
            target.write_bytes(data)
            kept.append((line, seconds))
            if n % 25 == 0:
                print(f"  {n}/{len(lines)}（使う {len(kept)}、除いた {len(rejected)}）")
    finally:
        await synthesizer.disconnect()

    with (out / "metadata.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file_name", "text", "speaker"])
        for line, _ in kept:
            w.writerow([f"audio/{line.id}.wav", line.text, args.speaker])
    with (out / "rejected.tsv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["id", "reason", "text"])
        for line, why in rejected:
            w.writerow([line.id, why, line.text])
    total = sum(s for _, s in kept)
    print(f"使う {len(kept)} 文（{total / 60:.1f} 分）、除いた {len(rejected)} 文 → {out}")
    if rejected:
        print(f"除いた理由: {out / 'rejected.tsv'}（耳で確かめて、よければ台本の文を直す）")
    print(f"次: python tools/irodori_from_sbv2.py reference --name {args.name}")
    return 0 if kept else 1


# ---- 2a. reference ----

def reference(args: argparse.Namespace) -> int:
    out = folder(args.name)
    meta = out / "metadata.csv"
    if not meta.exists():
        print(f"{meta} がない: 先に generate", file=sys.stderr)
        return 1
    with meta.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    # 短いきれいなクリップを何個も（Irodori-TTS の docs/parameters.md: v4-Small は短い発話をつないで
    # 学習していて、合計 30 秒ほどで似せる効果の大半）。長い語り 3 本だと、途中で別の声に流れることがあった。
    # ふつうの文（N…）を優先し、いろいろな文から選ぶ（台本の順に一定の間隔で）
    candidates = []
    for row in rows:
        path = out / row["file_name"]
        seconds = wav_stats(path.read_bytes())[0]
        if args.clip_min <= seconds <= args.clip_max:
            candidates.append((0 if path.stem.startswith("N") else 1, path, seconds))
    candidates.sort(key=lambda c: (c[0], c[1].name))
    chosen, total = [], 0.0
    for _, path, seconds in candidates:
        if total + seconds > args.seconds:
            continue
        chosen.append(path)
        total += seconds
    if not chosen:
        print("参照音声に使える WAV がない", file=sys.stderr)
        return 1
    cmd = [sys.executable, str(REPO / "tools" / "irodori_voice.py"), "--name", args.voice or args.name, *map(str, chosen)]
    print(f"参照音声: {len(chosen)} 個、{total:.0f} 秒")
    return subprocess.call(cmd)


# ---- 3. 感情ごとの声 ----

EMOTIONS = ("happy", "surprised", "sad", "angry", "scared")


async def emotions(args: argparse.Namespace) -> int:
    """
    感情ごとの参照音声を作り、声 <name>_<感情> として登録する（tts.irodori.emotion_voices で使う）。

    読ませるのは台本のその感情の文（楽しい H、驚き S、悲しい D、むっ A、怖い F）。読み手:
    - sbv2: Style-Bert-VITS2 のその感情のスタイル（tts.emotion_style_map がモデルにある Neutral 以外の
      スタイルを指しているときだけ。shen モデルは Neutral しかない）
    - irodori: Irodori-TTS に、ふだんの声（--name）と感情の話し方の説明（tts.irodori.emotion_captions）で
    - auto（既定）: sbv2 でできる感情は sbv2、ほかは irodori
    """
    from ailoveshen.domain.value_objects import EmotionState, EmotionType
    from ailoveshen.factories.tts import create_synthesizer
    from ailoveshen.infrastructure.config import load_config_dict

    tts = load_config_dict(REPO / "config").get("tts", {})
    wanted = [e.strip() for e in args.emotions.split(",") if e.strip()]
    unknown = [e for e in wanted if e not in EMOTIONS]
    if unknown:
        print(f"知らない感情: {', '.join(unknown)}（{', '.join(EMOTIONS)}）", file=sys.stderr)
        return 1
    lines = read_lines()

    # Style-Bert-VITS2 でその感情のスタイルが使えるか
    sbv2_ok: set[str] = set()
    sbv2 = None
    if args.source in ("auto", "sbv2") and not args.register_only:
        config = copy.deepcopy(tts)
        config["engine"] = "style_bert_vits2"
        sbv2 = create_synthesizer(config)
        try:
            await sbv2.connect()
            styles = set(await sbv2.get_available_styles())  # type: ignore[attr-defined]
            mapping = {k.lower(): v for k, v in (tts.get("emotion_style_map") or {}).items()}
            default = (tts.get("voice") or {}).get("default_style", "Neutral")
            for e in wanted:
                style = mapping.get(e)
                if style and style in styles and style != default:
                    sbv2_ok.add(e)
            print(f"Style-Bert-VITS2 のスタイル: {', '.join(sorted(styles))}。感情のスタイルで読めるもの: {', '.join(sorted(sbv2_ok)) or 'なし'}")
        except Exception as e:  # noqa: BLE001 - サーバーがなければ irodori で
            print(f"Style-Bert-VITS2 につながらない: {e}")
            sbv2 = None
    if args.source == "sbv2" and not args.register_only:
        missing = [e for e in wanted if e not in sbv2_ok]
        if missing:
            print(f"Style-Bert-VITS2 にスタイルがない感情（tts.emotion_style_map）: {', '.join(missing)}。--source irodori か auto で", file=sys.stderr)
            wanted = [e for e in wanted if e in sbv2_ok]

    irodori = None
    if not args.register_only and any(e not in sbv2_ok for e in wanted):
        config = copy.deepcopy(tts)
        config["engine"] = "irodori"
        c = config.setdefault("irodori", {})
        c.update({"voice": args.name, "emotion": True, "caption_min_intensity": 0.0, "seed": None, "emotion_voices": {}})
        irodori = create_synthesizer(config)
        await irodori.connect()

    status = 0
    registered: list[str] = []
    try:
        for e in wanted:
            voice = f"{args.name}_{e}"
            out = folder(voice) / "audio"
            out.mkdir(parents=True, exist_ok=True)
            reader = "sbv2" if e in sbv2_ok else "irodori"
            mine = [line for line in lines if line.emotion == e][: args.per_emotion]
            if not args.register_only:
                synth = sbv2 if reader == "sbv2" else irodori
                kept = rejected = 0
                for line in mine:
                    target = out / f"{line.id}.wav"
                    if target.exists() and not args.overwrite:
                        continue
                    try:
                        data = await synth.synthesize(line.text, EmotionState(primary=EmotionType(e), intensity=0.9))  # type: ignore[union-attr]
                    except Exception as err:  # noqa: BLE001
                        print(f"  {line.id} 失敗: {err}")
                        rejected += 1
                        continue
                    if check(line, *wav_stats(data)):
                        rejected += 1
                        continue
                    target.write_bytes(data)
                    kept += 1
                print(f"{e}（{reader}）: 作った {kept}、除いた {rejected} → {out}")
            clips = []
            total = 0.0
            for path in sorted(out.glob("*.wav")):
                seconds = wav_stats(path.read_bytes())[0]
                if 1.5 <= seconds <= 8.0 and total + seconds <= args.seconds:
                    clips.append(path)
                    total += seconds
            if not clips:
                print(f"  {e}: 参照音声にできる WAV がない", file=sys.stderr)
                status = 1
                continue
            print(f"  声 {voice}: {len(clips)} 個、{total:.0f} 秒")
            if subprocess.call([sys.executable, str(REPO / "tools" / "irodori_voice.py"), "--name", voice, *map(str, clips)]) == 0:
                registered.append(e)
    finally:
        for synth in (sbv2, irodori):
            if synth is not None:
                await synth.disconnect()

    if not registered:
        return 1
    print("\n聞いてみて、違う感情に聞こえるクリップや声の違うものは消し、--register-only でもう一度登録する。")
    print("Irodori-TTS で感情の声を使う（config/development.yaml）:")
    print("tts:\n  irodori:\n    emotion_voices:")
    for e in registered:
        print(f"      {e}: {args.name}_{e}")
    if "happy" in registered:
        print(f"      excited: {args.name}_happy")
    print("Style-Bert-VITS2 のスタイルにする: <Style-Bert-VITS2 の Python> tools/sbv2_add_styles.py")
    return status


# ---- 2b. LoRA ----

def train_repo(args: argparse.Namespace) -> Path:
    repo = Path(args.train_repo).expanduser()
    if not (repo / "train.py").exists():
        sys.exit(f"{repo} に Irodori-TTS の学習用リポジトリがない: 先に scripts/irodori/setup_train_mac.sh")
    return repo


def run(cmd: list[str], cwd: Path) -> int:
    print("$ " + " ".join(cmd))
    env = dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK="1")
    return subprocess.call(cmd, cwd=cwd, env=env)


def base_checkpoint(args: argparse.Namespace) -> Path:
    return folder(args.name).parent / "base" / args.base.replace("/", "__") / "model.safetensors"


def base(args: argparse.Namespace) -> int:
    repo = train_repo(args)
    target = base_checkpoint(args)
    target.parent.mkdir(parents=True, exist_ok=True)
    code = (
        "import sys, shutil; from huggingface_hub import hf_hub_download, list_repo_files;"
        f"files = list_repo_files({args.base!r});"
        "name = 'model.safetensors' if 'model.safetensors' in files else next((f for f in files if f.endswith('.safetensors') and '/' not in f), None);"
        "name or sys.exit('no .safetensors in the repo: ' + ', '.join(files));"
        f"shutil.copy(hf_hub_download({args.base!r}, name), {str(target)!r}); print('base:', name)"
    )
    status = run(["uv", "run", "--no-sync", "python", "-c", code], repo)
    if status == 0:
        print(f"→ {target}（サーバーも同じ元のモデルで動かす: サーバーの .env の IRODORI_HF_CHECKPOINT={args.base}）")
    return status


def prepare(args: argparse.Namespace) -> int:
    repo = train_repo(args)
    out = folder(args.name).resolve()
    if not (out / "metadata.csv").exists():
        print(f"{out / 'metadata.csv'} がない: 先に generate", file=sys.stderr)
        return 1
    # data/irodori_train/<name>/ は Hugging Face datasets の audiofolder（metadata.csv の file_name が音声）
    return run([
        "uv", "run", "--no-sync", "python", "prepare_manifest.py",
        "--dataset", str(out), "--split", "train",
        "--audio-column", "audio", "--text-column", "text", "--speaker-column", "speaker",
        "--output-manifest", str(out / "manifest.jsonl"), "--latent-dir", str(out / "latents"),
        "--device", args.device, "--prefetch", "0",
    ], repo)


def train(args: argparse.Namespace) -> int:
    repo = train_repo(args)
    out = folder(args.name).resolve()
    manifest = out / "manifest.jsonl"
    checkpoint = base_checkpoint(args).resolve()
    if not manifest.exists():
        print(f"{manifest} がない: 先に prepare", file=sys.stderr)
        return 1
    if not checkpoint.exists():
        print(f"{checkpoint} がない: 先に base", file=sys.stderr)
        return 1
    warmup = max(1, args.max_steps // 20)
    status = run([
        "uv", "run", "--no-sync", "python", "train.py",
        "--config", "configs/train_v4_small_lora.yaml",
        "--manifest", str(manifest), "--output-dir", str(out / "lora"),
        "--init-checkpoint", str(checkpoint),
        "--device", args.device, "--precision", "bf16" if args.device.startswith("cuda") else "fp32",
        # 少ないデータ（数十分）・1 台向け: 元の設定（batch 40、30000 歩）から小さく
        "--batch-size", str(args.batch_size), "--gradient-accumulation-steps", str(args.grad_accum),
        "--num-workers", "2", "--max-steps", str(args.max_steps),
        "--warmup-steps", str(warmup), "--stable-steps", str(int(args.max_steps * 0.8)),
        "--save-every", str(args.save_every), "--valid-ratio", "0.05", "--valid-every", str(args.save_every),
        *(["--lora-r", str(args.lora_r)] if args.lora_r else []),
    ], repo)
    if status == 0:
        print(f"→ {out / 'lora' / 'checkpoint_final'}")
        print("使う: config/development.yaml の tts.irodori.lora_adapter にこのパスを書き、tools/tts_compare.py で聞き比べる")
    return status


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--name", default="shen_sbv2", help="データの名前（data/irodori_train/<name>/）")

    g = sub.add_parser("generate", help="台本を Style-Bert-VITS2 で読ませる")
    common(g)
    g.add_argument("--texts", type=Path, help="足す文（1 行 1 文）")
    g.add_argument("--limit", type=int, default=0, help="先頭から何文だけ（試しに）")
    g.add_argument("--for-reference", action="store_true", help="参照音声の候補（語りとふつうの文、64 文）だけ作る")
    g.add_argument("--by-emotion", action="store_true", help="台本の感情のスタイルで読ませる（tts.emotion_style_map）")
    g.add_argument("--overwrite", action="store_true", help="作ってあるものも作り直す")
    g.add_argument("--speaker", default="shen")
    g.add_argument("--sdp-ratio", type=float, default=0.1, help="小さいほど読みが安定（既定 0.1）")
    g.add_argument("--noise", type=float, default=0.4)
    g.add_argument("--noisew", type=float, default=0.6)
    g.add_argument("--length", type=float, default=None)

    r = sub.add_parser("reference", help="参照音声を選んで Irodori-TTS の声にする")
    common(r)
    r.add_argument("--seconds", type=float, default=30.0, help="参照音声の長さ（長いほど合成が遅い。最大 120）")
    r.add_argument("--voice", help="声の ID（既定は --name）")
    r.add_argument("--clip-min", type=float, default=2.5, help="使うクリップの長さの下限（秒）")
    r.add_argument("--clip-max", type=float, default=6.0, help="使うクリップの長さの上限（秒）")

    em = sub.add_parser("emotions", help="感情ごとの参照音声を作って声にする")
    common(em)
    em.add_argument("--source", choices=("auto", "sbv2", "irodori"), default="auto", help="読み手（既定 auto）")
    em.add_argument("--emotions", default=",".join(EMOTIONS), help="作る感情（カンマ区切り）")
    em.add_argument("--per-emotion", type=int, default=14, help="感情ごとに読ませる文の数")
    em.add_argument("--seconds", type=float, default=30.0, help="感情ごとの参照音声の長さ")
    em.add_argument("--overwrite", action="store_true", help="作ってあるものも作り直す")
    em.add_argument("--register-only", action="store_true", help="作らずに、残っている WAV で登録し直す")

    for cmd, text in (("base", "元のモデルの重みを取ってくる"), ("prepare", "学習用に符号化する"), ("train", "LoRA を学習する")):
        p = sub.add_parser(cmd, help=text)
        common(p)
        p.add_argument("--train-repo", default=str(DEFAULT_TRAIN_REPO), help="Irodori-TTS の学習用リポジトリ")
        p.add_argument("--base", default=DEFAULT_BASE, help="元のモデル（Hugging Face）")
        if cmd in ("prepare", "train"):
            p.add_argument("--device", default="mps", help="mps / cuda / cpu")
        if cmd == "train":
            p.add_argument("--max-steps", type=int, default=3000)
            p.add_argument("--batch-size", type=int, default=4)
            p.add_argument("--grad-accum", type=int, default=4)
            p.add_argument("--save-every", type=int, default=500)
            p.add_argument("--lora-r", type=int, default=0, help="LoRA の rank（0: 設定のまま 16）")

    args = ap.parse_args()
    if args.cmd == "generate":
        return asyncio.run(generate(args))
    if args.cmd == "emotions":
        return asyncio.run(emotions(args))
    return {"reference": reference, "base": base, "prepare": prepare, "train": train}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
