#!/usr/bin/env bash
# Irodori-TTS-Server を Mac（Apple Silicon）に入れる。Docker は使わない（Docker からは GPU（MPS）が
# 使えない）。手順と確かめ方: docs/setup/irodori_tts.md
#
#   scripts/irodori/setup_mac.sh
#
# 環境変数: IRODORI_DIR（入れる場所。既定 ~/Irodori-TTS-Server）
#           IRODORI_VOICES_DIR（参照音声の置き場。既定 <このリポジトリ>/data/irodori_voices）
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DIR="${IRODORI_DIR:-$HOME/Irodori-TTS-Server}"
VOICES="${IRODORI_VOICES_DIR:-$REPO/data/irodori_voices}"

if [ "$(uname)" != "Darwin" ]; then
  echo "注意: Mac 用の手順（MPS を使う）。ほかの OS では README の uv sync --extra cu128 などを使う" >&2
fi
if ! command -v uv >/dev/null; then
  echo "uv が要る: brew install uv" >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null; then
  echo "注意: ffmpeg がない（wav 以外の参照音声や出力形式に要る）: brew install ffmpeg" >&2
fi

if [ -d "$DIR/.git" ]; then
  echo "更新: $DIR"
  git -C "$DIR" pull --ff-only
else
  echo "取得: $DIR"
  git clone https://github.com/Aratako/Irodori-TTS-Server.git "$DIR"
fi

cd "$DIR"
# macOS の CPU / MPS 用の PyTorch（PyPI のもの）
uv sync --extra cpu

[ -f .env ] || cp .env.example .env
# .env の KEY を VALUE にする（なければ足す）
set_env () {
  grep -v "^$1=" .env > .env.tmp || true
  echo "$1=$2" >> .env.tmp
  mv .env.tmp .env
}
set_env IRODORI_HOST 127.0.0.1
set_env IRODORI_PORT 8088
set_env IRODORI_MODEL_DEVICE mps
set_env IRODORI_MODEL_PRECISION fp32   # MPS では bf16 のほうが遅いという報告がある
set_env IRODORI_PRELOAD true           # 起動時にモデルを読む（最初の読み上げが遅れない）
set_env IRODORI_VOICES_DIR "$VOICES"
set_env IRODORI_DEFAULT_VOICE shen
mkdir -p "$VOICES"

cat <<MSG

入れ終わった: $DIR
次に:
  1. 参照音声を用意する: python tools/irodori_voice.py （Style-Bert-VITS2 の学習データから選ぶ）
  2. 起動する:           scripts/irodori/start_mac.sh （最初はモデルのダウンロードで数分かかる）
  3. 確かめる:           curl http://localhost:8088/health
  4. 聞き比べる:         python tools/tts_compare.py
モデルの重みのライセンスは Hugging Face のモデルカード（Aratako/Irodori-TTS-v4-Small）で確かめる。
MSG
