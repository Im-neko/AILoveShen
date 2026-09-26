# Irodori-TTS を Mac で動かす（読み上げの方式の切り替え）

作成: 2026-09-26。背景と候補の比較: `docs/design/32_tts_options.md`。

Irodori-TTS は日本語専用の音声合成。参照音声（30 秒〜2 分）から学習なしで声をまね、感情を話し方の説明（caption）で指定できる。サーバーは [Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server)（OpenAI 互換の `POST /v1/audio/speech`、ポート 8088）。

**Mac では Docker を使わない**: Docker の中からは Apple Silicon の GPU（MPS）が使えない。サーバーは Mac の上で直接動かす（`scripts/irodori/`）。Style-Bert-VITS2（Docker、:5001）はそのまま残し、設定で切り替える。

## 1. 入れる（最初の 1 回）

```bash
brew install uv ffmpeg
scripts/irodori/setup_mac.sh
```

`~/Irodori-TTS-Server` に取得して `uv sync --extra cpu`（macOS の CPU/MPS 用の PyTorch）をし、サーバーの `.env` に次を書く:

| 変数 | 値 | 理由 |
|---|---|---|
| `IRODORI_MODEL_DEVICE` | `mps` | Apple Silicon の GPU |
| `IRODORI_MODEL_PRECISION` | `fp32` | MPS では bf16 のほうが遅いという報告がある |
| `IRODORI_PRELOAD` | `true` | 起動時にモデルを読む（最初の読み上げが遅れない） |
| `IRODORI_VOICES_DIR` | `<このリポジトリ>/data/irodori_voices` | 参照音声の置き場（git に入らない） |
| `IRODORI_HOST` / `IRODORI_PORT` | `127.0.0.1` / `8088` | この Mac からだけ |

場所を変えるときは `IRODORI_DIR=... scripts/irodori/setup_mac.sh`（起動も同じ変数で）。

モデルの重み（既定 `Aratako/Irodori-TTS-v4-Small`）のライセンスは Hugging Face のモデルカードで確かめる（サーバーのコードは MIT）。配信に使ってよいかは重みのライセンス次第。

## 2. 参照音声（声のお手本）

Style-Bert-VITS2 の学習に使った音声から選ぶ:

```bash
python tools/irodori_voice.py                         # Style-Bert-VITS2/Data/shen/wavs（なければ raw）から
python tools/irodori_voice.py --source <WAV のフォルダー>
python tools/irodori_voice.py a.wav b.wav c.wav      # 使うファイルを自分で選ぶ
```

3〜12 秒の WAV を長い順に合計 60 秒（`--seconds`、最大 120）まで選び、`data/irodori_voices/shen/` に写して `voices.json` に声 `shen` として書く。雑音・BGM・笑い声の多いクリップは声が崩れる元なので、`a.wav b.wav …` で選び直すとよい。変えたらサーバーを起動し直す。

## 3. 起動と確認

```bash
scripts/irodori/start_mac.sh             # Ctrl-C で止める。最初はモデルのダウンロードで数分
curl http://localhost:8088/health
curl http://localhost:8088/v1/audio/voices   # shen が出れば OK
curl http://localhost:8088/v1/audio/speech -H "Content-Type: application/json" \
  -d '{"model":"irodori-tts","input":"テストだよ。","voice":"shen"}' -o test.wav && afplay test.wav
```

## 4. 聞き比べ

```bash
(cd docker && docker compose up -d)                  # Style-Bert-VITS2 も比べるとき
python tools/tts_compare.py                           # 両方で実況によく出る 20 文
python tools/tts_compare.py --engines irodori --emotion --repeat 2
```

`logs/tts_compare/<日時>/` に方式ごとの WAV と `report.md`（1 文ごとの合成時間、平均、最大）。読み間違いやアクセントは耳で確かめて `report.md` の右の列に書く。Minecraft と OBS を起動したまま測ると配信中の速さに近い。

見るところ:

- **読み**: アイテム名（丸石、インゴット）、数（64 個、200 メートル）、視聴者の名前、短い相づち（「うん、うん」）
- **速さ**: 実況は短い文が続く。1 文 1〜2 秒を超えると話すのが遅れて聞こえる
- **安定性**: `--repeat 2` で同じ文が同じ音になるか（seed を決めているので同じになるはず）。語の抜け・繰り返しがないか
- **感情**（`--emotion`）: 説明を付けると声が参照から離れることがある。よければ `tts.irodori.emotion: true`

## 5. 配信で使う

`.env`:

```bash
TTS_ENGINE=irodori
# IRODORI_VOICE=shen
```

`python -m ailoveshen.stream` の起動のログに `[tts] 読み上げる（Irodori-TTS localhost:8088、声 shen）`。サーバーにつながらなければ配信は始まらない（直し方が出る）。戻すときは `TTS_ENGINE=style_bert_vits2`（または消す）。

調整は `config/development.yaml` の `tts.irodori`（`default.yaml` はそのまま）:

| 項目 | 既定 | 意味 |
|---|---|---|
| `speed` | 1.0 | 話す速さ |
| `num_steps` | null（モデルの既定） | 少ないほど速く、粗い |
| `seed` | 1234 | 決めると同じ文は同じ音（null: 毎回変わる） |
| `cfg_scale_speaker` | null | 上げると参照の声に近づく |
| `emotion` | false | 感情を話し方の説明にする（`emotion_captions`、強さ `caption_min_intensity` 以上） |
| `timeout_seconds` | 60 | 1 文を待つ秒数 |

## 6. 困ったとき

| 症状 | 見るところ |
|---|---|
| `TTS サーバー（Irodori-TTS …）につながらない` | `scripts/irodori/start_mac.sh` が動いているか、`curl http://localhost:8088/health` |
| 起動のログに `声 'shen' がない` | `python tools/irodori_voice.py` のあとサーバーを起動し直したか |
| 遅い | Activity Monitor の GPU、`num_steps` を下げる、量子化モデル（サーバーの `.env` の `IRODORI_HF_CHECKPOINT`。int8 など。MPS で速くなるかは未確認） |
| MPS のエラーで止まる | `start_mac.sh` は `PYTORCH_ENABLE_MPS_FALLBACK=1`（ない演算は CPU）。それでもなら `IRODORI_MODEL_DEVICE=cpu` で動くか確かめる |

実機（M5 / 32GB）ではまだ動かしていない。速さと読みは聞き比べで確かめる。
