#!/usr/bin/env python3
"""
台本を 1 文ずつ録音するページ（docs/voice/README.md）。ブラウザでマイクから録り、その行の番号の
ファイル名（N001.wav、EMOTION100_001.wav …）で、このパソコンのフォルダーに WAV を保存する。

    python tools/voice_recorder.py [--out data/voice_recordings] [--port 8770]
    → ブラウザで http://localhost:8770 を開く（マイクの使用を許可する）

- Python の標準ライブラリだけで動く（録る人のパソコンでも、このリポジトリと Python があればよい）
- この PC からだけ開ける（127.0.0.1）。マイクは localhost ならブラウザが許可する
- 撮り直すと上書きする（前のものは <out>/_previous/ に 1 つだけ残す）
- 録ったあとは python tools/voice_dataset.py <out> で Style-Bert-VITS2 の学習データに
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "voice_recorder.html"
SCRIPTS = [REPO / "docs" / "voice" / "recording_script.tsv", REPO / "docs" / "voice" / "ita_corpus.tsv"]
MAX_BYTES = 50 * 1024 * 1024  # 1 文の WAV の上限（48kHz 16bit モノラルで 9 分弱）
ID = re.compile(r"^[A-Z0-9_]{2,40}$")


def read_script() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in SCRIPTS:
        if path.exists():
            with path.open(encoding="utf-8", newline="") as f:
                rows += list(csv.DictReader(f, delimiter="\t"))
    return rows


def wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except (wave.Error, OSError, EOFError):
        return None


def make_handler(out: Path, script: list[dict[str, str]]):
    ids = {row["id"] for row in script}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # 静かに（保存だけ表示する）
            pass

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: object, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _id_from(self, prefix: str) -> str | None:
            name = self.path[len(prefix):].split("?", 1)[0].removesuffix(".wav")
            return name if ID.match(name) and name in ids else None

        def do_GET(self) -> None:  # noqa: N802 - http.server の名前
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif self.path == "/api/script":
                self._json(script)
            elif self.path == "/api/recordings":
                self._json({p.stem: wav_seconds(p) for p in out.glob("*.wav") if p.stem in ids})
            elif self.path.startswith("/audio/"):
                rid = self._id_from("/audio/")
                path = out / f"{rid}.wav" if rid else None
                if path and path.exists():
                    self._send(200, path.read_bytes(), "audio/wav")
                else:
                    self._json({"error": "not found"}, 404)
            else:
                self._json({"error": "not found"}, 404)

        def do_PUT(self) -> None:  # noqa: N802
            rid = self._id_from("/api/recordings/") if self.path.startswith("/api/recordings/") else None
            if not rid:
                self._json({"error": "台本にない番号"}, 400)
                return
            size = int(self.headers.get("Content-Length") or 0)
            if not 44 < size <= MAX_BYTES:
                self._json({"error": f"大きさがおかしい（{size} バイト）"}, 400)
                return
            body = self.rfile.read(size)
            if body[:4] != b"RIFF" or body[8:12] != b"WAVE":
                self._json({"error": "WAV ではない"}, 400)
                return
            target = out / f"{rid}.wav"
            if target.exists():
                previous = out / "_previous"
                previous.mkdir(exist_ok=True)
                shutil.copy2(target, previous / target.name)
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(target)
            seconds = wav_seconds(target)
            print(f"保存: {target.name}（{seconds} 秒）")
            self._json({"id": rid, "seconds": seconds})

        def do_DELETE(self) -> None:  # noqa: N802
            rid = self._id_from("/api/recordings/") if self.path.startswith("/api/recordings/") else None
            target = out / f"{rid}.wav" if rid else None
            if not target or not target.exists():
                self._json({"error": "not found"}, 404)
                return
            previous = out / "_previous"
            previous.mkdir(exist_ok=True)
            target.replace(previous / target.name)
            print(f"消した: {target.name}（_previous/ に移した）")
            self._json({"id": rid})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "data" / "voice_recordings", help="録音の保存先")
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args()

    script = read_script()
    if not script:
        print("台本（docs/voice/*.tsv）がない", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.out, script))
    print(f"録音のページ: http://localhost:{args.port}  （保存先: {args.out}、Ctrl-C で止める）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
