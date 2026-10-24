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
   ├─ Twitch のチャット（IRC、匿名で読むだけ）→ 返事 / 応援（サブスク・ギフト・ビッツ・レイド）→ お礼
   ├─ Twitch のフォロー通知（EventSub。トークンが要る）→ お礼
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
- API キー: LiteLLM（`LITELLM_API_KEY`。モデル名は `LITELLM_MODEL`）、TypeSafe / Jev（`TYPESAFE_API_KEY`）

### 2. リポジトリと依存

```bash
git clone <このリポジトリ> && cd AILoveShen
git submodule update --init          # Style-Bert-VITS2（TTS サーバーのビルドに要る）
pip install -r requirements.txt      # Python の依存（OBS 用の obsws-python、再生用の sounddevice も入る）
cd minecraft-bridge && npm install && cd ..
```

ブリッジの `npm install` は、技のサンドボックス（`isolated-vm` 6、ネイティブのモジュール）も入れる。Node 22 か 24 で、macOS（Apple Silicon）・Windows（x64）・Linux ならビルド済みのものが入る（コンパイルしない）。それ以外（Intel の Mac など）はその場でコンパイルする（C++20 のビルドツール: Xcode Command Line Tools、Visual Studio Build Tools、`build-essential` と `python3`）。Node は 22 以上（`node -v`）。

Linux では `sounddevice` に PortAudio が要る（`sudo apt install libportaudio2`）。

### 3. `.env`

```bash
cp .env.example .env
```

最低限書くもの:

| 変数 | 内容 |
|---|---|
| `LITELLM_API_KEY` | LiteLLM（llm.ideta.net）の仮想キー（`key_alias` が `ailoveshen`）。pass の `LITELLM_KEY_AILOVESHEN` を `LITELLM_API_KEY="$(pass show LITELLM_KEY_AILOVESHEN)"` で渡してもよい |
| `LITELLM_MODEL` | LiteLLM のモデル名（`claude-haiku-5-5`、`gemini-3.8-flash` など） |
| `TYPESAFE_API_KEY` | Jev の API キー |
| `OBS_PASSWORD` | OBS の WebSocket のパスワード（下の 6） |
| `OBS_GAME_SOURCE` | ゲームを映す OBS のソース名（既定 `Minecraft`） |
| `TTS_MODEL_NAME` | 読み上げに使う Style-Bert-VITS2 のモデル名（既定 `shen`） |
| `TWITCH_CHANNEL` | 配信するチャンネル名（`twitch.tv/<ここ>`）。チャットを読む。空なら読まない |
| `TWITCH_ACCESS_TOKEN` | 任意。チャットに書き込むとき（`!commands` の一覧）: `chat:edit` 付きのユーザーアクセストークン（`oauth:` はあってもなくてもよい） |
| `TWITCH_BOT_LOGIN` | 任意。そのトークンのアカウントのログイン名。空ならチャンネル名（配信者のアカウント） |
| `TWITCH_CLIENT_ID` | 任意。フォロー通知（EventSub）に使うアプリの Client-Id（開発者コンソールで登録。public でよい）。これだけで、トークンは自動で取って更新する（下） |
| `TWITCH_CLIENT_SECRET` | 任意。confidential クライアントのときだけ（トークンの更新に付ける）。public なら空 |
| `TWITCH_EVENTSUB_TOKEN` | 任意。手で作ったフォロー通知のトークン（スコープ `moderator:read:followers`、同じ Client-Id で作ったもの）をそのまま使うとき。**更新されない**（4 時間で切れる）ので、普通は空にする |

Twitch のチャットを読むだけならトークンやアプリの登録は要らない（匿名で読む。返事は声で返す）。トークンがあれば、そのアカウントでログインしてチャットに書き込める。ログインに失敗したら（`Twitch に … でログインできない` のログ）匿名で読むだけに戻り、一覧は声で言う。

**応援へのお礼**（設計書 71）: サブスク、ギフト、ビッツ、レイドは、チャットの接続から**トークンなしで**届く（フォローだけが別の仕組み）。フォローを受けるには、Twitch の開発者コンソール（https://dev.twitch.tv/console）でアプリを登録して（クライアントの種類は「公開」でよい。Client Secret は要らない）、その Client-Id を `TWITCH_CLIENT_ID` に書くだけでよい。**トークンは自動で取って更新する**（デバイスコード方式）: 初回の起動でログに `[twitch] フォロー通知のトークンを取るため、配信者本人が次の URL を開いて許可してください: https://www.twitch.tv/activate?… （コード XXXXXXXX、30 分以内）` と出て、**この PC の既定のブラウザで許可の画面が開き**（`twitch.open_browser: false` で開かない）、声でも「Twitch の許可が必要だよ」と知らせる（どちらも許可を求め始めたとき 1 回だけ。コードが切れて取り直すときはログだけ）ので、配信者本人のアカウントで許可を押す（ここだけ人の操作。許可されるまで、フォロー通知以外は普通に動く。既定のブラウザが配信者本人でログインしていないときは、別のアカウントで許可しないこと: フォロー通知の持ち主が変わる）。許可されると `data/twitch_token.json`（`twitch.token_path`、git に入らない。権限 600）に残り、次からは何もしなくてよい。アクセストークンは 4 時間で切れるので、期限の 10 分前と、Twitch が無効と言ったとき（1 時間ごとに確かめる）に、更新トークンで自動で更新する（更新トークンは使い捨てで、更新のたびに新しいものを保存する。**30 日つないでいないと失効する**ので、そのときは許可からやり直す）。コードを 30 分以内に許可しないと新しいコードを取り直してもう一度ログに出す。`TWITCH_EVENTSUB_TOKEN` に手で作ったトークンを書くと、それをそのまま使う（更新はしない）。トークンを取り直したいときは `data/twitch_token.json` を消して起動する。`chat:edit` だけのトークン（`TWITCH_ACCESS_TOKEN`）はチャットへの書き込み用で、フォロー通知には使わない。

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

Irodori-TTS（日本語専用、参照音声から学習なしで声をまねる。Mac では Docker の外で動かす）に切り替えるときは `docs/setup/irodori_tts.md`。`.env` の `TTS_ENGINE=irodori` で切り替わり、下の「TTS サーバー（Docker）」の代わりに `scripts/irodori/start_mac.sh` を起動する（確認は `curl http://localhost:8088/health`）。

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

- 目標: `?pos=right`、`?theme=mint|sky|lemon`（既定はピンク）、`?scale=0.8`、`?compact=1`（最終目標・今の大目標・小目標だけ。中目標のカードを出さない）、`?toast=0`（クリアの知らせを出さない）
- アバター: 既定は全身が収まる表示で中央（OBS のクロップで好きな部分を切り抜く）。`?view=bust`（胸から上）、`?pos=left|center|right`（既定 center）、`?scale=1.2`
- 見た目だけ確かめる: `?demo=1`（プレイの処理がなくても動く）

## 毎回の起動

ターミナルを 3 つ使う。順番どおりに。

### 1. Minecraft サーバーと TTS サーバー（Docker）

```bash
docker compose --env-file .env -f docker/docker-compose.minecraft.yml up -d
cd docker && docker compose up -d && cd ..
```

`TTS_ENGINE=irodori` のときは TTS の Docker の代わりに、別のターミナルで `scripts/irodori/start_mac.sh`（`docs/setup/irodori_tts.md`）。

TTS サーバーは BERT の読み込みに 1〜2 分かかる。準備できたか:

```bash
curl http://localhost:5001/models/info                    # 自分のモデルが出れば OK
curl "http://localhost:5001/voice?text=テスト&model_name=<モデル名>" -o test.wav
```

### 2. ブリッジ（ターミナル 1）

```bash
cd minecraft-bridge && npm start
```

開発中は `npm run dev`（`src/` のファイルが変わると自動で再起動する。`git pull` でも）。再起動のたびに、実行中の行動は止まり、状態（`state.json`）を保存し、ボットは入り直す（数秒）。Python 側（配信）はそのまま動き続け、つながらない間は待ってやり直す。視点のミラーにつないだ Minecraft のクライアントは切れるので、つなぎ直す（配信中の OBS の画面も一度切れる。本番の配信では `npm start`）

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
[admin] 管理画面（このマシンからだけ）: http://127.0.0.1:8765/admin
Twitch のチャット #<チャンネル> を読み始めた
```

- Ctrl-C まで止まらない（ステップの上限はない）
- Gemini・Jev・ブリッジが失敗しても止まらない。「… 秒待ってやり直す」と出して 5 秒から倍々（最長 60 秒）に待ち、やり直す。ブリッジを再起動しても配信はそのまま続く
- TTS サーバーにつながらなければ始めない（黙った配信を防ぐ）。直し方が出る
- チャットのコメントには 1 つずつ声で返事をする。返事の間は 5 秒以上。作っている間に来たコメントは新しい 3 件だけ取っておく（`twitch.response`）。`!` で始まるコメントには返事をしない。コマンドは `!yomi よみ`（その人の名前の読みを覚える）と `!yomi 言葉 よみ`（言葉の読みを直す。例: `!yomi 小麦 こむぎ`）と `!commands`（`!help` / `!コマンド`。使えるコマンドの一覧をチャットに書く。書けなければ声で。30 秒に 1 回まで）。配信者自身（チャンネルのアカウント）と `TWITCH_BOT_LOGIN` のコメントには返事をしない（コマンドは使える）。ボット（`twitch.ignore_users`、既定 `[streamelements]`）には何も反応しない。頼みごとは大目標に足す（今の大目標の材料や一歩になる小さな頼みは、その大目標の中目標に足す）ことがある。視聴者の名前の読み（`data/readings.json`、設計書 30）は、返答のときに Gemini が推測するか本人の `!yomi` で覚え、読み上げのときだけ名前を置き換える。辞書は `http://127.0.0.1:8765/api/readings`（`?name=` で 1 人）
- 配信への応援（フォロー、サブスク、ギフト、ビッツ、レイド）にお礼を言い、アバターも喜ぶ（表情はハッピー、フォローとレイドは手を振り、贈り物とビッツは万歳。設計書 71）。お礼は決まった言い方から選ぶ（LLM は待たない）。フォローは 2 秒待って溜まった分を 1 回にまとめ（「A さん、B さん、C さんほか 2 人」）、同じ人のフォローは 1 時間は繰り返さない。お礼の間は 3 秒以上（`twitch.support`、`enabled: false` で言わない）。ログに `[support] follow: …` と `[say] …`。ログに `[support] フォロー通知は受けない` と出たら、`TWITCH_CLIENT_ID` がない（上）
- 行動の途中でも、読み上げが終わってから 10 秒黙っていたら一言話す（`stream.live_interval_seconds`、0 で話さない。設計書 43）。道具に添えた新しい「今やろうとしていること」はそのまま読み、なければ変わったこと（行動の結果、持ち物の増減、動いた距離）から一言を作る（purpose `live`）。直前の数行と似た実況は読まない（ログに `[say] 直前と同じ内容なので言わない`）。状況が何も変わらないまま `stream.live_idle_seconds`（60、0 で話さない）黙っていたら、待ち時間の雑談を 1 行（新しくやることは約束しない。夜の穴の中で朝を待つ間など）。ローカルの LLM がなければ Gemini で 1 時間に最大 360 回呼ぶ
- ほかのゲーム（LoL など）やゲームの外の雑談にも乗る。自分のこと（使っているキャラ、好きなもの）をその場で決めたら `data/self_facts.json` に覚え、次からの返事はそれに合わせる（設計書 40。手で直してもよい。ワールドを作り直しても残る）
- 画面のログ（`[say]` 実況、`[chat]`、`[reply]`、`[goal]` 小目標、`[objective+]` / `[objective✓]` / `[objective×]` 大目標）は、設定の `logging.file` のファイルにも残る（`.env` の `APP_ENV=development` なら `data/logs/ailoveshen-dev.log`）。ログは INFO まで（`--debug` で DEBUG も）

既定は設定（`config/default.yaml` の `stream` と `twitch`）。その回だけ変えるオプション:

| オプション | 内容 |
|---|---|
| `--control candidates` | 前の方式（ブリッジの候補から Jev が選ぶ）で動かす。既定は `tools`（Gemini が道具を呼んで直接操作し、技（Gemini が書いて覚える手順。`minecraft.agent.skills`、既定オン）も使う）。設定は `minecraft.agent.control` |
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

#### ローカルの LLM で動かす（設計書 36）

文だけの呼び出し（実況、返事、目標の決定、道具の 1 手、技を書く…）をローカルの LLM（OpenAI 互換: FreeToken、Ollama など）に送り、画像のある呼び出し（画面、建物の地図）とローカルに入らない長さのものだけ Gemini に送る。

```bash
# 先に測る（JSON スキーマで縛るか、道具を呼ぶか、速さ、先頭のキャッシュ）
python tools/llm_probe.py                                             # FreeToken（:1919）
python tools/llm_probe.py --base-url http://127.0.0.1:11434/v1 --model <ollama のモデル名>
# 動かす（.env に書いてもよい）
LOCAL_LLM=1 python -m ailoveshen.stream
LOCAL_LLM=1 LOCAL_LLM_URL=http://127.0.0.1:11434/v1 LOCAL_LLM_MODEL=<名前> python -m ailoveshen.stream
```

- 起動のログに「LLM: ローカル …、振り分け …、画像は Gemini」と出る。使用量の行は `Local <モデル>`（Gemini は `Gemini <モデル>`）。`python tools/gemini_usage.py <ログ>` は `local:<用途>` の行に分けて数える（費用 0）
- 用途ごとにリモート（LiteLLM のモデル）へ回す: `config/development.yaml` などで `llm.routes: {default: local, skill_write: remote}`。コードを書く用途（スキル・条件のチェック）だけなら `CODE_LLM=remote`（`LITELLM_CODE_MODEL=claude-sonnet-5-5` で Sonnet。設計書 135）。迷ったときに強いモデルに聞く道具（`ask_advisor`）は `LITELLM_ADVISOR_MODEL=claude-sonnet-5-5` で出る（空なら出ない。設計書 143）
- `llm.local.context_tokens` はサーバーの 1 回の最大の長さに合わせる（FreeToken は `--max-seq-len-override`、Ollama は `OLLAMA_CONTEXT_LENGTH`。Ollama の既定は小さく、超えた分は黙って切られる）。入らない呼び出しは Gemini に回る（ログに「too long for the local model」）
- ローカルが落ちたらエラーのまま（`llm.fallback: none`）。Gemini で続けるなら `fallback: gemini`
- FreeToken: `--enable-cache-report` を付けると使用量の行の `cached=` が出る
- Ollama: 思考の切り替え（`chat_template_kwargs`）が効かないことがある。そのときは `llm.local.thinking_param: none`

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
| 今の小目標と歩みの数え | `data/goal.json` | 再起動のあと、ブリッジが同じ小目標を追っていれば続きから追う（設計書 63）。ワールドのリセットで取っておく |
| ミッションと大目標 | `data/mission.json` | 大目標の一覧と、その中目標の状態と進み具合。最初のミッションは `config/default.yaml` の `minecraft.mission`。形式の版（`format`）が違うファイルは読まず、最初の大目標で始め直す（元は `mission.json.unreadable` に残る） |
| 家と記憶 | `minecraft-bridge/data/state.json` | 家（名前、設計図、場所）、前に見た場所、チェストの中身 |
| 覚えた技 | `minecraft-bridge/data/skills/*.json` | 技のコード、版、成功と失敗の数。ワールドを作り直しても残す |

最初からやり直したいときは、ブリッジとプレイの処理を止めてから、上の 2 つ（ミッションと大目標、家と記憶）を別の名前に移す（消さずに取っておく）。技はそのまま残してよい（どのワールドでも使える）。

### 技の掃除（配信の外で）

一度も成功していない技（失敗作）と、成功していない版を消す。配信とブリッジを止めてから:

```bash
cd minecraft-bridge
npm run skills:clean -- --dry-run   # 何を消すかだけ見る
npm run skills:clean                # 消す（ブリッジが動いていると断る）
```

## ワールドを作り直す

ワールドと、ボットが覚えていること（家、記憶、大目標、建物、メモ、技、動きの帳）は別々に保存されている。ワールドだけ変えると、ボットは前のワールドの家を探しに行ってしまうので、両方をそろえて作り直す。

**mise のタスク**（初めてなら `mise trust`）: 配信とブリッジを Ctrl-C で止めてから、

```bash
mise run world:reset -- --dry-run          # やることだけ出す（何も変えない）
mise run world:reset                       # 名前の末尾の数字を 1 増やす（world2 → world3）、シードは今のまま
mise run world:reset -- --level world5 --seed random --yes
mise run world:reset -- --level world  --existing   # 前のワールドに戻る（取っておいた state-*.json、skills-*、worldmap-*、mission-*.json、notes-*.json、learned_candidates-*.json は自分で戻す）
```

下の 1〜4 をまとめてやる（`scripts/reset_world.sh`。同じ日にもう取ってあれば、名前に時刻をつける）。動いている配信やブリッジは止めずに断る。ボリュームにすでにある名前は、`--existing` を付けなければ断る（前のワールドに入ってしまうため）。途中で失敗したら戻し方を出す。終わったら 5 の起動だけ自分でやる。手でやるなら下のとおり:

1. 配信とブリッジを止める（Ctrl-C）
2. ワールドについての覚えを取っておく（消さずに名前を変える）。教訓（`data/lessons.json`）、自分について話したこと（`data/self_facts.json`）、名前の読み（`data/readings.json`）はワールドによらないので、そのまま残す:

   ```bash
   d=$(date +%Y%m%d)
   mv minecraft-bridge/data/state.json minecraft-bridge/data/state-$d.json
   mv minecraft-bridge/data/skills minecraft-bridge/data/skills-$d
   mv minecraft-bridge/data/worldmap minecraft-bridge/data/worldmap-$d
   mv data/mission.json data/mission-$d.json
   mv data/notes.json data/notes-$d.json
   mv data/learned_candidates.json data/learned_candidates-$d.json
   ```

3. `.env` のワールドの名前を変え、シードを決める（空ならランダム）:

   ```
   MC_LEVEL=world2
   MC_SEED=
   ```

   前のワールドは同じボリュームの中に `world` のまま残るので、`MC_LEVEL=world` に戻せばまた遊べる（そのときは 2 で取っておいたファイルも元の名前に戻す）
4. Minecraft サーバーを作り直して起動する:

   ```bash
   docker compose --env-file .env -f docker/docker-compose.minecraft.yml up -d --force-recreate
   docker compose -f docker/docker-compose.minecraft.yml logs -f   # "Done" が出るまで待つ（新しいワールドは生成に 1〜2 分）
   ```

5. ブリッジ、視点のクライアント、配信を、いつもの順に起動する。ボットは何も持たずに新しいワールドの初期地点に出て、家の設計から始める

シードの選び方:
- 空（ランダム）にして、ボットの視点のクライアントで初期地点のまわりを見て、平らな草原と木があるか確かめる。だめなら 3 と 4 をやり直す（`MC_LEVEL` を world3 などにする）
- 決めたシードを使うなら、シードの地図のサイト（Chunkbase など）で Java 版 1.21.4 の初期地点のまわりを見て選ぶ。平原（Plains）か森の端、近くに村・水・石の出た斜面があると、家、食料、石の道具まで進みやすい
- 砂漠、雪原、海の近く、山岳は、ボットに難しい（木が少ない、食料が少ない、足場が悪い）

ワールドを全部消したいときは `docker compose -f docker/docker-compose.minecraft.yml down -v`（ボリュームごと消える。元に戻せない）。

## ベンチマーク（変更が効いたかを測る）

直した変更が効いたのかは、配信の成績の数字（`python tools/goal_outcomes.py`、設計書 82）か、再現できる課題（設計書 83）で見る。後者は、**配信とは別のサーバー（Docker の `ailoveshen-bench`、ポート 25566）とブリッジ（ポート 3001）**で、毎回新しいワールド（`.env` の `MC_SEED` と同じシード = 同じ地形）に素手で入り、課題 1 つを上限の時間まで追う。成否はブリッジが世界から判定するので、人は見ない。配信のワールド、`state.json`、`data/`、`logs/` には触れない。

**配信とブリッジを Ctrl-C で止めてから**（ローカルの LLM を共有すると時間の測定が意味をなさない。動いていれば断る）:

```bash
mise run bench -- --dry-run                      # やることだけ出す（何も起動しない）
mise run bench                                   # 食料 8 個・石のツルハシ・家 x 3 回、1 回 最長 15 分（最悪で約 2 時間強）
mise run bench -- --tasks food --repeats 1 --cap-minutes 5   # 手始めの確かめ
mise run bench -- report logs/bench/<バッチ>.jsonl
mise run bench -- compare 前.jsonl 後.jsonl      # 変更の前後を並べる
```

- **ボットの視点を見るなら `127.0.0.1:25579`**（Minecraft 1.21.4 でダイレクト接続。ブリッジの視点ミラーで、見るだけ）。**25566（サーバー本体）には繋がない**: 別のプレイヤーとして入って普通に操作でき、ボットの成績に混ざる（モブの湧きも変わる）
- 課題と既定（繰り返し、上限、ポート）は `config/benchmark.yaml`。定石は空にし、大目標をその課題 1 つにする
- 昼のまま時間と天気を止める（15 分の途中で夜が来て結果が揺れないように）。夜も含めるなら `--no-freeze-day`
- 起きた事故の再現（`config/benchmark.yaml` の `scenarios`）は、既定では走らない。`mise run bench -- --tasks night,pond,cage` と名前で指定する（どれも条件は `have(log, 4)`。`night`: 夕方から始めて夜を越す、昼固定なし、**死亡の数を見る**。`pond`: 半径 12 の池の真ん中から陸に上がる。`cage`: 水の入った土の小部屋から素手で出る。終わりまで続けて 3 回以上 LLM の呼び出しが失敗した回は、サーバー停止として `error`（成否に数えない）。設計書 90 §5）
- 結果は `logs/bench/<バッチ>.jsonl` に 1 回 1 行、各回の記録（ステップ、小目標の終わり、ブリッジの出力、プレイのログ）は `logs/bench/<バッチ>/<課題>-<回>/`
- サーバーの起動に失敗した、ブリッジが落ちた、プレイが途中で終わった回は `error` で、成功率に数えない
- 後片づけ（サーバーとブリッジを止め、ボリュームを捨てる）は途中で失敗しても行う。Ctrl-C で止めたあとに残っていれば `docker compose -p ailoveshen-bench -f docker/docker-compose.bench.yml down -v`

## 見るところ・記録

| もの | 場所 |
|---|---|
| 管理画面（配信中に喋らせる・プリセット・配信者への指示。OBS には入れない。設計書 54） | `http://127.0.0.1:8765/admin`（このマシンからだけ。プリセットは `data/presets.json`） |
| Gemini の思考（直近の呼び出し） | `http://127.0.0.1:8765/debug/gemini`（JSON: `/api/debug/gemini?limit=20`） |
| 今の目標（JSON） | `http://127.0.0.1:8765/api/goals` |
| 街の理想図（区画ごとの用途。設計書 47 §6） | `http://127.0.0.1:8765/town`（JSON は `/api/goals` の `town_map`、保存は `data/mission.json`） |
| 施設と覚えた場所・アイテムの検索（Jev が選んだキー） | `logs/search/*.jsonl`、施設の一覧は `http://127.0.0.1:3000/facilities` |
| 見張りの記録（道具モード） | `logs/watch/*.jsonl` |
| ブリッジの標準出力（`[move]` など。端末を閉じても残る。20 MB で 1 世代） | `logs/bridge.log` |
| 1 手を Jev が選んだか・Gemini に回した理由と、その手の結果（設計書 34 §3.1） | `logs/steps/*.jsonl` |
| コメントの仕分け（返事をしなかったコメントの文も） | `logs/chat/*.jsonl` |
| 返事の約束の確かめ（約束だけで何もしない返事は書き直し） | `logs/promises/*.jsonl` |
| 実況の間合い（話した・取っておいた） | `logs/commentary/*.jsonl` |
| 読み上げた文と、そのときの状態・照合の結果・読み上げの結果（played / dropped_old）。数えるのは `python tools/speech_consistency.py lines --judge jev`、予告した小目標のその後は `... forecasts`（設計書 65） | `logs/spoken/*.jsonl` |
| 行き詰まりの確認（Jev の答え、考え直させたか） | `logs/stuck/*.jsonl` |
| 覚えた候補（LLM が選んでうまくいった手を覚え、Jev の選択肢に出す: 覚えた・効いた・効かなかった・消した）。覚えた候補そのものは `data/learned_candidates.json`（設計書 52） | `logs/learned_candidates/*.jsonl` |
| 教訓を思い出した記録（状況、候補、Jev の答え、出した教訓）。教訓帳そのものは `data/lessons.json` | `logs/lessons/*.jsonl` |
| 覚えた技（一覧: 成功 n/m、最後の失敗） | `http://127.0.0.1:3000/skills`（1 つのコード: `/skills/<名前>`）、書いたときのコードは `/debug/gemini`（`skill_write`） |
| アバターの表情の判断（Jev と規則） | `logs/avatar/*.jsonl` |
| 小目標の選択（Jev が選んだもの、Gemini に回した理由） | `logs/goals/*.jsonl` |
| 小目標が終わるたびの成績（達成・失敗・死亡・切った、ステップ、秒数）と、実行ごとの条件（commit・設定・モデル）。集計は `python tools/goal_outcomes.py`（設計書 82） | `logs/goal_ends/*.jsonl`、`logs/runs/*.jsonl` |
| ベンチマークの結果（課題 x 回）と各回の記録（設計書 83） | `logs/bench/*.jsonl`、`logs/bench/<バッチ>/` |
| LLM の呼び出しの全文（システム指示、プロンプト、出力、道具の呼び出し、トークン、答えたモデル。3 日で消える）。再生は `python tools/replay_decisions.py`、プロンプトの内訳は `python tools/prompt_budget.py`（設計書 90） | `logs/generations/gen-*.jsonl` |
| 断られた手の集計（理由の種類ごと）と、まだ確かめていないことの一覧（設計書 90） | `python tools/refusal_report.py`、`python tools/unverified.py`（ログのファイルではなく、`logs/steps` と `docs/WORKLOG.md` から作る） |
| 襲われたときの反射（規則と Jev の選択、確信度） | `logs/reflex/*.jsonl`（ブリッジのターミナルにも `[reflex] …` と出る） |
| Gemini の費用（用途ごとの回数とトークン） | `python tools/gemini_usage.py data/logs/ailoveshen-dev.log`（価格を渡すと円の目安） |
| 配信のログ | ターミナルと `data/logs/ailoveshen-dev.log`（`APP_ENV=development` のとき。設定の `logging.file`） |
| ボットの様子 | ブリッジのターミナルの出力 |

## うまくいかないとき

| 出るもの | 原因と対処 |
|---|---|
| `プレイを始められない（GameBridgeError: Minecraft bridge unreachable …）。N 秒待ってやり直す` | ブリッジが動いていない → 起動の 2。起動すれば配信はそのまま始まる |
| `ステップが失敗した（…）。N 秒待ってやり直す` | Gemini・Jev・ブリッジの一時的な失敗。続くなら中身（…）を見る |
| `TTS サーバー（Irodori-TTS …）につながらない` で起動しない | `scripts/irodori/start_mac.sh` を起動する（`docs/setup/irodori_tts.md`）。Style-Bert-VITS2 に戻すなら `.env` の `TTS_ENGINE` を消す |
| `TTS サーバー（…）につながらない` で起動しない | TTS サーバーを起動する（起動の 1）。読み上げなしなら `--no-speak` |
| 視点のクライアントが `… was larger than I expected, found N bytes extra whilst reading packet …` で切れる | ミラーが自分で作ったパケットの形が本物のクライアントとずれている。持ち物（`container_set_content`）は数だけの部品に絞って送るようにした（2026-09-26）。まだ出るならクライアントの `logs/latest.log` のその行（パケットの名前）を控える。ブリッジを再起動すると直る |
| `Twitch のチャットにつながらない` | ネットワークを確かめる。自動でつなぎ直す |
| `Twitch から: … NOTICE …` | チャンネル名を確かめる（`TWITCH_CHANNEL`） |
| `[eventsub] フォロー通知が届かない: トークンにスコープ moderator:read:followers がない` | 手で書いたトークンなら作り直す。自動なら `data/twitch_token.json` を消して起動し、許可し直す。ほかの応援（サブスクなど）は届く |
| `[eventsub] トークンは別の Client-Id … で作られている` | `TWITCH_CLIENT_ID` と、トークンを作ったアプリを同じにする（手で書いたトークンのとき。自動なら起きない） |
| `[twitch] フォロー通知のトークンを取るため、…URL を開いて許可してください` | 配信者本人がその URL を開いて許可する（初回と、30 日以上つながなかったあと） |
| `[twitch] 更新トークンが使えない` | 続けて出る URL で許可し直す（30 日以上つないでいない、または別の場所で取り直した） |
| `[eventsub] 許可されなかった` / `デバイスコードを取れない` | 許可の画面で拒否した、または `TWITCH_CLIENT_ID` が違う。ログの本文（Twitch の返事）を見る |
| `[eventsub] フォローの購読を断られた（403 …）` | トークンの持ち主が、配信者本人でもそのチャンネルのモデレーターでもない |
| `.env に LITELLM_API_KEY … を書く` | `.env` にキーとモデル名を書く |
| `目標ボードのポート 8765 が使われている` で起動しない | 前の配信や examples が動いたままになっている → 止める。別のポートにするなら `--board-port` と OBS の URL |
| `…が止まった（…）。配信は続くが、直すには再起動する` | 目標ボードかチャットへの返事が止まった。プレイと実況は続く。区切りのよいところで配信を再起動する |
| `config/default.yaml がない` | リポジトリの直下で動かす |
| `OBS から画面を撮れない（…）` | OBS が開いていない、パスワード違い、WebSocket がオフ → 準備の 6。画面なしでプレイは続く |
| `OBS と話すためのモジュール obsws_python がない` | `pip install -r requirements.txt` |
| `OBS にソース「Minecraft」がない` | OBS のソース名を `OBS_GAME_SOURCE` と同じにする |
| `Failed to connect to TTS server` | TTS サーバーがまだ起動中か、止まっている → `docker compose logs`（`docker/` で） |
| 読み上げで 422 | `tts.emotion_style_map` にモデルにないスタイル名がある |
| 音が OBS に入らない | `tts.audio.device` と OBS の音声入力キャプチャのデバイスを確かめる |
| Ctrl-C で止まらない | 最新のコードにする（`git pull`） |
| `[skill] … 失敗 too many actions` | 技が上限（行動 40 回、調べもの 200 回）に当たった。Gemini が理由を読んで直すか、道具で進める。続くなら配信の後に掃除する |
| ブリッジのログに `[bridge] サーバーとの接続が切れた（…）: 状態を保存して終わる` が出て、ブリッジが終わる | Minecraft サーバーが落ちた・再起動した。ブリッジを起動し直す（Python の配信は、ブリッジが戻るまで待つ）。サーバーが落ちた理由は `docker logs ailoveshen-minecraft` の `A fatal error has been detected`（`/data/hs_err_pid*.log`） |
| サーバーが `SIGSEGV … libasyncProfiler.so`（spark）で落ちる（Apple Silicon の Docker、2026-10-11） | Paper に同梱の spark のバックグラウンドプロファイラが原因。`docker exec ailoveshen-minecraft sh -c "sed -i 's/\"backgroundProfiler\": true/\"backgroundProfiler\": false/' /data/plugins/spark/config.json"` のあと `docker restart ailoveshen-minecraft`。ボリュームを作り直すと戻るので、そのつど同じにする |
| ブリッジの起動で `isolated-vm` のエラー | `cd minecraft-bridge && npm install`（ビルドツールが要ることがある。準備の 2） |
| ブラウザソースが空 | 配信が動いているか、`--no-board` にしていないか。動いていれば OBS でソースを再読み込み |

## 関係する設計書

- 目標の階層: `docs/design/13_goal_hierarchy.md`
- 道具での操作と見張り: `docs/design/21_tool_control.md`
- 技（Gemini が書いて覚える手順）: `docs/design/22_skills.md`
- 画面を Gemini に見せる: `docs/design/23_screen_vision.md`
- アバター: `docs/design/24_avatar.md`
- 行動の途中の一言: `docs/design/43_live_narration.md`
- 小目標の成績の記録と集計: `docs/design/82_play_report.md`
- 再現できる課題での測定（ベンチマーク）: `docs/design/83_benchmark.md`
- TTS サーバー（Docker）: `docker/README.md`、`docs/setup/docker.md`
