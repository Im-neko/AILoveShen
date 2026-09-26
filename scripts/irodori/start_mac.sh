#!/usr/bin/env bash
# Irodori-TTS-Server を起動する（Ctrl-C で止める）。先に scripts/irodori/setup_mac.sh。
# 設定は $IRODORI_DIR/.env（setup_mac.sh が MPS、fp32、ポート 8088、参照音声の置き場を書く）。
set -euo pipefail

DIR="${IRODORI_DIR:-$HOME/Irodori-TTS-Server}"
if [ ! -d "$DIR" ]; then
  echo "$DIR がない: 先に scripts/irodori/setup_mac.sh" >&2
  exit 1
fi
cd "$DIR"
# MPS にない演算は CPU で動かす（止まらないように）
export PYTORCH_ENABLE_MPS_FALLBACK=1
exec uv run --no-sync python -m irodori_openai_tts --host 127.0.0.1 --port "${IRODORI_PORT:-8088}"
