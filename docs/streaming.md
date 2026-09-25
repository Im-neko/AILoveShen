# 配信の手順

Minecraft を AI（Gemini + Jev）にプレイさせ、実況・アバター・目標の表示を OBS で配信するまでの手順とコマンド。
初回の準備（1 回だけ）と、毎回の起動・停止に分けてある。

配信の起動は `python -m ailoveshen.stream`。`examples/integration_test_minecraft.py` は試験用（ステップ数で終わる、台本のコメント）。

## 全体像

```
Minecraft サーバー (Docker, :25565)
   ↑ ボットが参加
ブリッジ (Node, minecraft-bridge, HTTP :3000) ── 視点のミラー :25578 ← Minecraft クライアント（OBS で映す）
   ↑ HTTP
配信 (Python, python -m ailoveshen.stream)
   ├─ Gemini（目標、実況、返事）/ Jev（行動の選択、見張り、アバターの表情）
   ├─ Twitch のチャット（IRC、匿名で読むだけ）→ 返事
   ├─ TTS サーバー (Docker, Style-Bert-VITS2, :5001) → 音声 → 仮想オーディオ → OBS
   ├─ OBS WebSocket (:4455) で画面を撮って Gemini に見せる
   └─ 目標ボード (HTTP :8765) → OBS のブラウザソース（目標の表示、アバター）
```

| もの | 起動 | ポート |
|---|---|---|
| Minecraft サーバー（Paper 1.21.4） | Docker | 25565 |
| TTS サーバー（Style-Bert-VITS2） | Docker | 5001 |
| ブリッジ（ボット、視点のミラー） | `npm start` | 3000（HTTP）、25578（視点） |
| 配信（プレイ、実況、チャット、目標ボード） | `python -m ailoveshen.stream` | 8765（`stream.board_port`） |
| OBS WebSocket | OBS | 4455 |

## 初回の準備

### 1. 必要なもの

- Python 3.11 以上、Node.js 20.12 以上（ブリッジが `.env` を読むのに使う機能が要る）、Docker
- Minecraft Java Edition 1.21.4 のクライアント（ボットの視点を映す用）
- OBS 28 以上（WebSocket が最初から入っている）
- API キー: Gemini（`GEMINI_API_KEY`）、TypeSafe / Jev（`TYPESAFE_API_KEY`）

### 2. リポジトリと依存

```bash
git clone <このリポジトリ> && cd AILoveShen
git submodule update --init          # Style-Bert-VITS2（TTS サーバーのビルドに要る）
pip install -r requirements.txt      # Python の依存（OBS 用の obsws-python、再生用の sounddevice も入る）
cd minecraft-bridge && npm install && cd ..
```

Linux では `sounddevice` に PortAudio が要る（`sudo apt install libportaudio2`）。

### 3. `.env`

```bash
cp .env.example .env
```

最低限書くもの:

| 変数 | 内容 |
|---|---|
| `GEMINI_API_KEY` | Gemini の API キー |
| `TYPESAFE_API_KEY` | Jev の API キー |
| `OBS_PASSWORD` | OBS の WebSocket のパスワード（下の 6） |
| `OBS_GAME_SOURCE` | ゲームを映す OBS のソース名（既定 `Minecraft`） |
| `TTS_MODEL_NAME` | 読み上げに使う Style-Bert-VITS2 のモデル名（既定 `shen`） |
| `TWITCH_CHANNEL` | 配信するチャンネル名（`twitch.tv/<ここ>`）。チャットを読む。空なら読まない |

Twitch のチャットは匿名で読むだけなので、トークンやアプリの登録は要らない（返事は声で返す）。

`.env` は git に入らない。Python とブリッジの両方が読む（シェルで `export` した値が優先）。

### 4. 音声モデル（Style-Bert-VITS2）

学習したモデルと BERT を置く（どちらも git には入らない）:

```
Style-Bert-VITS2/
├── model_assets/<モデル名>/     # config.json、*.safetensors、style_vectors.npy
└── bert/                          # BERT のモデル
```

`<モデル名>` を `.env` の `TTS_MODEL_NAME` にする。

感情で声のスタイルを変えたいときは `config/default.yaml` の `tts.emotion_style_map` にモデルのスタイル名を書く（今はすべて `Neutral`。モデルにないスタイル名は 422 エラー）。

### 5. 仮想オーディオ（読み上げの音を OBS に入れる）

- Mac: `brew install blackhole-2ch`
- Windows: VB-Audio Cable（手順は `docs/setup/obs-audio-routing.md`）

出力先を `config/development.yaml` に書く（`default.yaml` はそのままにしておく）:

```yaml
tts:
  audio:
    device: "BlackHole 2ch"     # Windows: "CABLE Input (VB-Audio Virtual Cable)"
```

書かなければ OS の既定の出力から鳴る。

### 6. OBS

1. 「ツール」→「WebSocket サーバー設定」
   - 「WebSocket サーバーを有効にする」をオン（ポート 4455）
   - 「認証を有効にする」をオン
   - 「接続情報を表示」のパスワードを `.env` の `OBS_PASSWORD` に書く
2. シーンにソースを足す（URL は毎回の起動の後に使える）

| ソース | 種類 | 設定 |
|---|---|---|
| `Minecraft` | ウィンドウキャプチャ（かゲームキャプチャ） | ボットの視点の Minecraft クライアントのウィンドウ。名前は `OBS_GAME_SOURCE` と同じにする（Gemini はこのソースだけを画像で見る） |
| 目標の表示 | ブラウザ | `http://127.0.0.1:8765/overlay/vtuber`、幅 1920、高さ 1080 |
| アバター | ブラウザ | `http://127.0.0.1:8765/avatar`、幅 1920、高さ 1080 |
| 読み上げの音 | 音声入力キャプチャ | `BlackHole 2ch`（Windows: `CABLE Output`） |

ブラウザソースは背景が透明なので、ゲームの上に重ねる。

表示の調整（URL の後ろに付ける）:

- 目標: `?pos=right`、`?theme=mint|sky|lemon`（既定はピンク）、`?scale=0.8`、`?compact=1`、`?toast=0`（クリアの知らせを出さない）
- アバター: `?pos=left|center|right`（既定 right）、`?view=full`（全身）、`?scale=1.2`
- 見た目だけ確かめる: `?demo=1`（プレイの処理がなくても動く）

## 毎回の起動

ターミナルを 3 つ使う。順番どおりに。

### 1. Minecraft サーバーと TTS サーバー（Docker）

```bash
docker compose --env-file .env -f docker/docker-compose.minecraft.yml up -d
cd docker && docker compose up -d && cd ..
```

TTS サーバーは BERT の読み込みに 1〜2 分かかる。準備できたか:

```bash
curl http://localhost:5001/models/info                    # 自分のモデルが出れば OK
curl "http://localhost:5001/voice?text=テスト&model_name=<モデル名>" -o test.wav
```

### 2. ブリッジ（ターミナル 1）

```bash
cd minecraft-bridge && npm start
```

ボットが Minecraft サーバーに参加し、視点のミラー（25578）と HTTP（3000）が開く。これだけではボットは動かない（動かすのは次の 4）。

### 3. ボットの視点を映す

Minecraft 1.21.4 のクライアントで「マルチプレイ」→「ダイレクト接続」→ `127.0.0.1:25578`。ボットの目線がそのまま映る（操作はできない、見るだけ）。このウィンドウを OBS の `Minecraft` ソースで映す。

### 4. 配信（ターミナル 2）

```bash
python -m ailoveshen.stream
```

リポジトリの直下で動かす。起動すると次が出る:

```
[tts] 読み上げる（モデル <モデル名>）
[chat] Twitch #<チャンネル> のチャットを読む（読むだけ）
[obs] 目標: http://127.0.0.1:8765/overlay/vtuber  アバター: http://127.0.0.1:8765/avatar
[debug] Gemini の思考: http://127.0.0.1:8765/debug/gemini
Twitch のチャット #<チャンネル> を読み始めた
```

- Ctrl-C まで止まらない（ステップの上限はない）
- Gemini・Jev・ブリッジが失敗しても止まらない。「… 秒待ってやり直す」と出して 5 秒から倍々（最長 60 秒）に待ち、やり直す。ブリッジを再起動しても配信はそのまま続く
- TTS サーバーにつながらなければ始めない（黙った配信を防ぐ）。直し方が出る
- チャットのコメントには 1 つずつ声で返事をする。返事の間は 5 秒以上。作っている間に来たコメントは新しい 3 件だけ取っておく（`twitch.response`）。`!` で始まるコメントには返事をしない。頼みごとは中目標に足すことがある
- 画面のログ（`[say]` 実況、`[chat]`、`[reply]`、`[goal]` 小目標、`[mid+]` / `[mid✓]` / `[mid×]` 中目標）は `data/logs/ailoveshen.log` にも残る

既定は設定（`config/default.yaml` の `stream` と `twitch`）。その回だけ変えるオプション:

| オプション | 内容 |
|---|---|
| `--control tools` | Gemini が道具を呼んで直接操作する（既定 `candidates` は候補から Jev が選ぶ。設定は `minecraft.agent.control`） |
| `--no-speak` | 読み上げない（TTS サーバーなしで試すとき） |
| `--no-chat` | Twitch のチャットを読まない |
| `--no-board` | 目標ボードとアバターを出さない |
| `--board-port N` | 目標ボードのポート（既定 8765） |
| `--debug` | DEBUG のログも出す |

試験用（ステップ数で終わる、台本のコメントを流す）:

```bash
python examples/integration_test_minecraft.py --board-port 8765 --speak --max-steps 300 --comments c.json
```

`c.json`（`after_seconds` はプレイ開始からの秒数）:

```json
[{"after_seconds": 60, "user": "neko", "message": "ベッド作って！"}]
```

### 5. OBS

OBS を開く（すでに開いていればブラウザソースを「再読み込み」）。ログに「OBS から画面を撮れるようになった」と出れば、Gemini が画面を見られている。

## 止め方

1. 配信: Ctrl-C（「配信を止めた」「[stop] Ctrl-C で止めた」と出て終わる。OBS のブラウザソースはつないだままでよい）
2. ブリッジ: Ctrl-C
3. Docker（片付けるとき）:

```bash
cd docker && docker compose down && cd ..
docker compose -f docker/docker-compose.minecraft.yml down
```

Minecraft のワールドは Docker のボリューム（`minecraft-data`）に残る。`down -v` にするとワールドも消える。

## 次の配信に引き継がれるもの

| もの | 場所 | 中身 |
|---|---|---|
| ミッションと中目標 | `data/mission.json` | 中目標の一覧と進み具合。最初のミッションは `config/default.yaml` の `minecraft.mission` |
| 家と記憶 | `minecraft-bridge/data/state.json` | 家（名前、設計図、場所）、前に見た場所、チェストの中身 |

最初からやり直したいときは、ブリッジとプレイの処理を止めてから、この 2 つを別の名前に移す（消さずに取っておく）。

## 見るところ・記録

| もの | 場所 |
|---|---|
| Gemini の思考（直近の呼び出し） | `http://127.0.0.1:8765/debug/gemini`（JSON: `/api/debug/gemini?limit=20`） |
| 今の目標（JSON） | `http://127.0.0.1:8765/api/goals` |
| 見張りの記録（`--control tools`） | `logs/watch/*.jsonl` |
| アバターの表情の判断（Jev と規則） | `logs/avatar/*.jsonl` |
| 配信のログ | ターミナルと `data/logs/ailoveshen.log` |
| ボットの様子 | ブリッジのターミナルの出力 |

## うまくいかないとき

| 出るもの | 原因と対処 |
|---|---|
| `プレイを始められない（GameBridgeError: Minecraft bridge unreachable …）。N 秒待ってやり直す` | ブリッジが動いていない → 起動の 2。起動すれば配信はそのまま始まる |
| `ステップが失敗した（…）。N 秒待ってやり直す` | Gemini・Jev・ブリッジの一時的な失敗。続くなら中身（…）を見る |
| `TTS サーバー（…）につながらない` で起動しない | TTS サーバーを起動する（起動の 1）。読み上げなしなら `--no-speak` |
| `Twitch のチャットにつながらない` | ネットワークを確かめる。自動でつなぎ直す |
| `Twitch から: … NOTICE …` | チャンネル名を確かめる（`TWITCH_CHANNEL`） |
| `.env に GEMINI_API_KEY … を書く` | `.env` にキーを書く |
| `config/default.yaml がない` | リポジトリの直下で動かす |
| `OBS から画面を撮れない（…）` | OBS が開いていない、パスワード違い、WebSocket がオフ → 準備の 6。画面なしでプレイは続く |
| `OBS と話すためのモジュール obsws_python がない` | `pip install -r requirements.txt` |
| `OBS にソース「Minecraft」がない` | OBS のソース名を `OBS_GAME_SOURCE` と同じにする |
| `Failed to connect to TTS server` | TTS サーバーがまだ起動中か、止まっている → `docker compose logs`（`docker/` で） |
| 読み上げで 422 | `tts.emotion_style_map` にモデルにないスタイル名がある |
| 音が OBS に入らない | `tts.audio.device` と OBS の音声入力キャプチャのデバイスを確かめる |
| Ctrl-C で止まらない | 最新のコードにする（`git pull`） |
| ブラウザソースが空 | 配信が動いているか、`--no-board` にしていないか。動いていれば OBS でソースを再読み込み |

## 関係する設計書

- 目標の階層: `docs/design/13_goal_hierarchy.md`
- 道具での操作と見張り: `docs/design/21_tool_control.md`
- 画面を Gemini に見せる: `docs/design/23_screen_vision.md`
- アバター: `docs/design/24_avatar.md`
- TTS サーバー（Docker）: `docker/README.md`、`docs/setup/docker.md`
