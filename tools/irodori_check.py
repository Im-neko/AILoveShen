#!/usr/bin/env python3
"""
Irodori-TTS がどの参照音声で読んでいるかを確かめる（docs/setup/irodori_tts.md §7）。

    python tools/irodori_check.py [--voice shen_sbv2] [--say "テストだよ。"]

1. 設定（.env の IRODORI_VOICE、tts.irodori.voice）: 配信と tools/tts_compare.py が使う声
2. サーバー: /health（読み込んだモデル、参照音声の置き場など）、声の一覧、その声の中身（どのファイルか）
3. このリポジトリの data/irodori_voices/voices.json のその声と、ファイルがあるか
4. --say: 同じ文を「その声」と「参照音声なし（voice: none）」と「設定の声」で読ませて
   logs/irodori_check/ に保存する。その声となしが同じような声なら、参照音声が効いていない
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def get(url: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(url, timeout=5) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001 - つながらない
        return 0, str(e)


def say(server: str, voice: str, text: str, target: Path, lora: str) -> str:
    body: dict[str, object] = {"model": "irodori-tts", "input": text, "voice": voice, "response_format": "wav",
                               "irodori": {"seed": 1234, "chunking_enabled": False}}
    if lora:
        body["irodori"]["lora_adapter"] = lora  # type: ignore[index]
    req = urllib.request.Request(f"{server}/v1/audio/speech", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            target.write_bytes(res.read())
        return f"→ {target}"
    except urllib.error.HTTPError as e:
        return f"失敗 {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
    except Exception as e:  # noqa: BLE001
        return f"失敗: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voice", help="確かめる声（既定: 設定の tts.irodori.voice）")
    ap.add_argument("--say", help="この文で聞き比べる WAV を作る")
    args = ap.parse_args()

    from ailoveshen.infrastructure.config import load_config_dict

    tts = load_config_dict(REPO / "config").get("tts", {})
    c = tts.get("irodori", {})
    server = f"http://{c.get('host', 'localhost')}:{c.get('port', 8088)}"
    configured = str(c.get("voice", "shen"))
    voice = args.voice or configured
    lora = str(c.get("lora_adapter") or "")

    print("■ 設定")
    print(f"  読み上げの方式 tts.engine: {tts.get('engine')}（irodori でなければ配信は Irodori-TTS を使わない）")
    print(f"  配信と tts_compare が使う声 tts.irodori.voice: {configured}（.env の IRODORI_VOICE）")
    print(f"  LoRA: {lora or 'なし'}")
    if voice != configured:
        print(f"  注意: 確かめる声 {voice!r} と設定の声 {configured!r} が違う。使うなら .env に IRODORI_VOICE={voice}")

    print(f"\n■ サーバー {server}")
    status, health = get(f"{server}/health")
    if status != 200:
        print(f"  つながらない: {health}（scripts/irodori/start_mac.sh）")
        return 1
    print("  /health: " + json.dumps(health, ensure_ascii=False)[:600])
    status, voices = get(f"{server}/v1/audio/voices")
    items = voices.get("data", voices) if isinstance(voices, dict) else voices
    ids = [str(v.get("id", v.get("voice_id", ""))) if isinstance(v, dict) else str(v) for v in items or []]
    print(f"  声の一覧: {', '.join(ids) or '（なし）'}")
    if voice not in ids:
        print(f"  × 声 {voice!r} をサーバーが知らない: 知らない声の要求がどうなるか（既定の声で読むか）はサーバー次第。"
              "start_mac.sh を止めて起動し直す。参照音声の置き場（サーバーの .env の IRODORI_VOICES_DIR）も確かめる")
    status, meta = get(f"{server}/v1/audio/voices/{voice}")
    print(f"  声 {voice!r} の中身: " + (json.dumps(meta, ensure_ascii=False)[:800] if status == 200 else f"取れない（{status} {meta}）"))

    print("\n■ このリポジトリの参照音声")
    index = REPO / "data" / "irodori_voices" / "voices.json"
    local = json.loads(index.read_text(encoding="utf-8")).get(voice) if index.exists() else None
    if not local:
        print(f"  {index} に {voice!r} がない")
    else:
        refs = local.get("ref_wavs", []) if isinstance(local, dict) else [local]
        missing = [r for r in refs if not (index.parent / r).exists()]
        print(f"  {len(refs)} 個: {', '.join(refs[:6])}{' …' if len(refs) > 6 else ''}")
        print(f"  {'× ないファイル: ' + ', '.join(missing) if missing else 'ファイルは全部ある'}（置き場 {index.parent}）")

    if args.say:
        out = REPO / "logs" / "irodori_check"
        out.mkdir(parents=True, exist_ok=True)
        print(f"\n■ 聞き比べ（{out}）")
        for name, label in ((voice, "その声"), ("none", "参照音声なし"), (configured, "設定の声")):
            if name == voice and label == "設定の声":
                continue
            print(f"  {label} {name}: {say(server, name, args.say, out / f'{name}.wav', lora)}")
        print("  その声と「参照音声なし」が同じような声なら、参照音声が効いていない")
    return 0


if __name__ == "__main__":
    sys.exit(main())
