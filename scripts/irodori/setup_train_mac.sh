#!/usr/bin/env bash
# Irodori-TTS の学習用リポジトリ（推論サーバーとは別）を入れる。LoRA の追加学習に使う
# （tools/irodori_from_sbv2.py base / prepare / train、docs/setup/irodori_tts.md §6）
#
#   scripts/irodori/setup_train_mac.sh
#
# 環境変数: IRODORI_TRAIN_DIR（入れる場所。既定 ~/Irodori-TTS）
set -euo pipefail

DIR="${IRODORI_TRAIN_DIR:-$HOME/Irodori-TTS}"
command -v uv >/dev/null || { echo "uv が要る: brew install uv" >&2; exit 1; }
command -v ffmpeg >/dev/null || echo "注意: ffmpeg がない（音声の読み込み torchcodec に要る）: brew install ffmpeg" >&2

if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone https://github.com/Aratako/Irodori-TTS.git "$DIR"
fi
cd "$DIR"
uv sync --extra cpu   # macOS は CPU / MPS の PyTorch
echo "入れ終わった: $DIR"
echo "次に: python tools/irodori_from_sbv2.py base"
