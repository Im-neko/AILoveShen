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

## 6. Style-Bert-VITS2 の音声から Irodori-TTS の声を作る

生の学習データは雑音が多いが、Style-Bert-VITS2 で作った音声は声がきれい（イントネーションは弱い）。台本（`docs/voice/*.tsv`、書き下ろし 249 文 + ITA コーパス 424 文）を Style-Bert-VITS2 に読ませて、それを Irodori-TTS のお手本にする（`tools/irodori_from_sbv2.py`）。

```bash
# 1. Style-Bert-VITS2 で読ませる（TTS サーバー :5001 を起動しておく。673 文で 30〜40 分ぶんの音声）
python tools/irodori_from_sbv2.py generate            # --limit 50 で試しに、--by-emotion で台本の感情のスタイル
```

`data/irodori_train/shen_sbv2/audio/<番号>.wav` と `metadata.csv`。揺れを小さくするため `sdp_ratio 0.1`、`noise 0.4`、`noisew 0.6` で読ませる（引数で変えられる）。長さ・音割れ・無音・読みの速さ（ITA はカナの読みがあるので、読み飛ばしや間延びがわかる）で外れたものは使わず、`rejected.tsv` に理由を書く。途中で止めても、作ったものは次に使う。

### 6a. 学習なし: 参照音声を差し替える（まずこれ）

```bash
python tools/irodori_from_sbv2.py generate --for-reference   # 6a だけなら: 語りとふつうの文（64 文）だけ
python tools/irodori_from_sbv2.py reference           # ふつうの文の短いクリップ（2.5〜6 秒）を合計 30 秒 → 声 shen_sbv2
```

参照音声は短いきれいなクリップを何個も（Irodori-TTS の docs/parameters.md: v4-Small は短い発話をつないで学習していて、合計 30 秒ほどで似せる効果の大半）。長いクリップ 3 本では、生成の途中で別の人の声に流れることがあった。声がまだ参照から離れるときは、`tts.irodori.cfg_scale_speaker` を少しずつ上げる（サーバーの既定は 5.0。上げすぎると不自然）か、6b の LoRA（モデル自体をこの声に寄せる）。参照音声は毎回の合成でモデルが読むので、長いほど遅い（既定 30 秒で効果の大半、`--seconds` で最大 120）。30〜40 分の量は 6b の学習用で、合成の速さには関係しない（LoRA は小さく、学習データは合成のときには使わない）。

`.env` に `IRODORI_VOICE=shen_sbv2`（サーバーを起動し直す）。Irodori-TTS は参照音声から声の質を、話し方（イントネーション）は自分のモデルから作るので、雑音のない声で、イントネーションは Irodori-TTS のままになる見込み。

### 6b. LoRA の追加学習（6a で足りなければ）

```bash
scripts/irodori/setup_train_mac.sh                    # 学習用のリポジトリ（~/Irodori-TTS。サーバーとは別）
python tools/irodori_from_sbv2.py base                # 元のモデル（既定 Aratako/Irodori-TTS-v4-Small）の重み
python tools/irodori_from_sbv2.py prepare             # 音声を学習用に符号化（manifest.jsonl、latents/）
python tools/irodori_from_sbv2.py train --max-steps 3000
```

- 学習の設定は元の `configs/train_v4_small_lora.yaml`（LoRA、rank 16）を、少ないデータ・1 台向けに小さくして使う（batch 4 × 積算 4、3000 歩、500 歩ごとに保存と検証、5% を検証用）
- できた LoRA（`data/irodori_train/shen_sbv2/lora/checkpoint_final`、途中のものも）を `.env` の `IRODORI_LORA=<そのフォルダーの絶対パス>` に。サーバーの元のモデルは学習と同じものにする（サーバーの `.env` の `IRODORI_HF_CHECKPOINT`）
- **注意**: 学習すると Style-Bert-VITS2 のイントネーションの癖も覚えうる。途中の保存（500 歩ごと）を `tools/tts_compare.py --engines irodori` で聞き比べ、声は似て話し方が崩れていないところを選ぶ
- **Mac での学習**: Irodori-TTS の案内は CUDA の例だけで、MPS での学習は確かめられていない（`--device mps`、fp32 になる）。動かない・遅すぎるときは、同じ手順を GPU のあるマシン（クラウドでも）で `--device cuda` にして、できた LoRA のフォルダーを Mac に持ってくる

## 7. 困ったとき

| 症状 | 見るところ |
|---|---|
| `TTS サーバー（Irodori-TTS …）につながらない` | `scripts/irodori/start_mac.sh` が動いているか、`curl http://localhost:8088/health` |
| 参照音声と違う声（別の人の声が混ざる） | `python tools/irodori_check.py --voice shen_sbv2 --say "テストだよ。"`: 設定の声（`IRODORI_VOICE`）、サーバーが知っている声とその中身、手元のファイル、その声 / 参照音声なし / 設定の声で読んだ WAV（`logs/irodori_check/`）。その声と「なし」が似ていれば参照音声が効いていない |
| 起動のログに `声 'shen' がない` | `python tools/irodori_voice.py` のあとサーバーを起動し直したか |
| 遅い | Activity Monitor の GPU、`num_steps` を下げる、量子化モデル（サーバーの `.env` の `IRODORI_HF_CHECKPOINT`。int8 など。MPS で速くなるかは未確認） |
| MPS のエラーで止まる | `start_mac.sh` は `PYTORCH_ENABLE_MPS_FALLBACK=1`（ない演算は CPU）。それでもなら `IRODORI_MODEL_DEVICE=cpu` で動くか確かめる |

実機（M5 / 32GB）ではまだ動かしていない。速さと読みは聞き比べで確かめる。
