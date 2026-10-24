#!/usr/bin/env python3
"""
LoL の試合映像を LLM が読めるかを測る（docs/design/202_lol_commentary.md の Phase 0）。

    python tools/lol_vision_probe.py clip.mp4 --every 5 --max-frames 12
    python tools/lol_vision_probe.py frames_dir/ --widths 768,1280 --crops

入力は動画（ffmpeg が要る）か、画像（jpg / png）の入ったフォルダ。フレームごとに

1. read: 画面の読み取り（時間、キル数、チャンピオン、キルフィード、今起きていること）を JSON で
2. chat: ゆるい雑談の一言（配信者の口調。解説はしない）

を LiteLLM（LITELLM_URL / LITELLM_MODEL / LITELLM_API_KEY）に聞き、幅ごと・切り出しごとに
遅延・トークン・読み取りの結果を出す。正解は自動では判定しない: 結果の JSON（`--out`）を目で見て、
読めている項目の割合を決める（Phase 1 に進む基準は設計書 §6）。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ailoveshen.infrastructure.config import load_env_file  # noqa: E402

# 1920x1080 の標準の HUD の領域（左, 上, 右, 下: 画面に対する割合）。HUD の大きさ設定で動くので目安
CROPS: dict[str, tuple[float, float, float, float]] = {
    "scoreboard": (0.40, 0.0, 0.60, 0.07),  # 上中央: 時間、キル数
    "killfeed": (0.75, 0.08, 1.0, 0.40),  # 右上: キルフィード
    "minimap": (0.80, 0.68, 1.0, 1.0),  # 右下: ミニマップ
    "champion": (0.25, 0.80, 0.75, 1.0),  # 下中央: 自分のチャンピオン、スキル、体力
}

READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "game_time": {"type": "string", "description": "mm:ss。読めなければ空"},
        "kills": {"type": "string", "description": "青チーム-赤チームのキル数。読めなければ空"},
        "champions": {"type": "array", "items": {"type": "string"}, "description": "見分けられたチャンピオン名"},
        "kill_feed": {"type": "array", "items": {"type": "string"}, "description": "キルフィードの行"},
        "happening": {"type": "string", "description": "今起きていること（1 文）"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["game_time", "kills", "champions", "kill_feed", "happening", "confidence"],
}

READ_PROMPT = (
    "これは League of Legends の試合画面です。見えるものだけを答えてください。"
    "読めない・自信がない項目は空にして、推測で埋めないでください。"
)
CHAT_PROMPT = (
    "あなたは LoL を友達の隣で見ている、ゆるい配信者です。画面を見て、今の雰囲気について"
    "一言（40 字以内）だけ雑談してください。解説・戦術の指摘はしない。読めないものは触れない。"
)


def load_frames(source: Path, every: float, max_frames: int) -> list[tuple[str, bytes]]:
    """動画なら every 秒ごとに切り出し、フォルダなら画像を順に読む。"""
    if source.is_dir():
        files = sorted(p for p in source.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        return [(p.name, p.read_bytes()) for p in files[:max_frames]]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(source), "-vf", f"fps=1/{every}",
             "-frames:v", str(max_frames), "-q:v", "2", f"{tmp}/f_%04d.jpg"],
            check=True,
        )
        return [(p.name, p.read_bytes()) for p in sorted(Path(tmp).glob("f_*.jpg"))]


def prepare(raw: bytes, width: int, crop: tuple[float, float, float, float] | None) -> bytes:
    """切り出して、幅を width に縮めた JPEG にする。"""
    from PIL import Image

    image = Image.open(io.BytesIO(raw)).convert("RGB")
    if crop:
        w, h = image.size
        image = image.crop((int(crop[0] * w), int(crop[1] * h), int(crop[2] * w), int(crop[3] * h)))
    if image.width > width:
        image = image.resize((width, max(1, round(image.height * width / image.width))))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()


def ask(client: httpx.Client, model: str, prompt: str, jpegs: list[bytes], schema: dict | None) -> dict[str, Any]:
    """画像つきで 1 回聞く。遅延とトークンも返す。"""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for jpeg in jpegs:
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        content.append({"type": "image_url", "image_url": {"url": url}})
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 1024}
    if schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "read", "schema": schema, "strict": False},
        }
    started = time.monotonic()
    r = client.post("/chat/completions", json=body)
    seconds = time.monotonic() - started
    if r.status_code >= 400:
        return {"error": f"{r.status_code} {r.text[:200]}", "seconds": round(seconds, 2)}
    data = r.json()
    text = (data["choices"][0]["message"].get("content") or "").strip()
    usage = data.get("usage") or {}
    result: dict[str, Any] = {
        "seconds": round(seconds, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }
    if schema:
        try:
            result["answer"] = json.loads(text)
        except json.JSONDecodeError:
            result["answer_raw"] = text
    else:
        result["answer"] = text
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="動画ファイル、または画像のフォルダ")
    parser.add_argument("--every", type=float, default=5.0, help="動画から切り出す間隔（秒）")
    parser.add_argument("--max-frames", type=int, default=12)
    parser.add_argument("--widths", default="768,1280", help="全体画像の幅（カンマ区切り）")
    parser.add_argument("--crops", action="store_true", help="HUD の領域の切り出し（高解像度）も試す")
    parser.add_argument("--model", default=None, help="既定は LITELLM_MODEL")
    parser.add_argument("--out", type=Path, default=Path("logs/lol_probe.json"))
    args = parser.parse_args()

    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    base_url = os.environ.get("LITELLM_URL", "https://llm.ideta.net/v1").rstrip("/")
    api_key = os.environ.get("LITELLM_API_KEY", "")
    model = args.model or os.environ.get("LITELLM_MODEL", "")
    if not api_key or not model:
        print("LITELLM_API_KEY と LITELLM_MODEL が要る（.env）", file=sys.stderr)
        return 2

    frames = load_frames(args.source, args.every, args.max_frames)
    if not frames:
        print("フレームがない", file=sys.stderr)
        return 2

    widths = [int(w) for w in args.widths.split(",") if w]
    variants: list[tuple[str, list[bytes]]] = []
    rows: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=60) as client:
        for name, raw in frames:
            variants = [(f"full{w}", [prepare(raw, w, None)]) for w in widths]
            if args.crops:
                variants.append(("crops", [prepare(raw, 640, box) for box in CROPS.values()]))
            for label, jpegs in variants:
                read = ask(client, model, READ_PROMPT, jpegs, READ_SCHEMA)
                chat = ask(client, model, CHAT_PROMPT, jpegs, None)
                rows.append({"frame": name, "variant": label, "bytes": sum(map(len, jpegs)), "read": read, "chat": chat})
                answer = read.get("answer") or {}
                print(
                    f"{name} {label:9s} read {read.get('seconds')}s "
                    f"time={answer.get('game_time')!r} kills={answer.get('kills')!r} "
                    f"conf={answer.get('confidence')} | chat {chat.get('seconds')}s {chat.get('answer')!r}"
                )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"model": model, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{args.out} に書いた。変種ごとの平均:")
    for label in dict.fromkeys(r["variant"] for r in rows):
        sub = [r for r in rows if r["variant"] == label]
        secs = [r["read"]["seconds"] for r in sub if "error" not in r["read"]]
        toks = [r["read"].get("prompt_tokens") or 0 for r in sub if "error" not in r["read"]]
        errors = sum("error" in r["read"] for r in sub)
        if secs:
            print(f"  {label:9s} read {sum(secs) / len(secs):.1f}s  prompt {sum(toks) // len(toks)} tokens  errors {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
