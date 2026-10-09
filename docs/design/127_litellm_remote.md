# 127. リモートの LLM を LiteLLM 経由のモデル（Claude Haiku 5.5 など）にする

作成: 2026-10-09。36（ローカル LLM と Gemini の振り分け）の続き。同じ日に、Gemini の直接の呼び出しをやめて LiteLLM に一本化した（§7）。§2〜§6 の `llm.remote.provider` / `REMOTE_LLM` / `thinking_param` / `LITELLM_PROMPT_CACHE` / `gemini.*` は §7 で消えた。

## 1. きっかけ

ユーザー（2026-10-09）: 「litellm に haiku5.5 を追加したので、gemini3.8 の代わりにこれも選べるようにしてほしい」。選び方は「Gemini の役ごと入れ替える」（用途ごとではなく）。

## 2. 形

Gemini が受け持つ呼び出しは、画像のある呼び出し、ローカルに入らない長さの呼び出し、ローカルが失敗したとき（`fallback`）、ローカルを切ったときの全部（36）。この「リモートの役」を 1 つの設定で入れ替える。

- `llm.remote.provider`: `gemini`（既定）か `litellm`（`REMOTE_LLM`）
- `litellm` のとき、役はすべて LiteLLM（OpenAI 互換のプロキシ）経由のモデルが受け持つ。Gemini のキーは要らない（`stream.py` は `LITELLM_MODEL` を要求する）
- 振り分けの表（`llm.routes`）と `llm.fallback` の値は `gemini` を `remote` に改名した（リリース前なので互換は残さない）。`routes: {build_design: remote}` のように書く

## 3. LiteLLM のアダプター

ローカルと同じ `OpenAICompatTextGenerator` を使う（LiteLLM は OpenAI 互換）。違いは次のとおり。

- **画像**: `images=True` のとき、画像を `image_url`（`data:<mime>;base64,…`）の部品としてユーザーのメッセージに添える。道具を呼ばずに文で答えて JSON で聞き直すときも画像を添え直す。記録は画像の中身ではなく id（`record_image`）。ローカルは今までどおり画像を断る
- **思考**: §5 のとおり、用途ごとに決める（初めは「指定を送らない」にしていたが、Haiku 5.5 は指定しないと思考が走るので改めた）
- **名前**: 使用量の行は `LiteLLM <model>（thinking off）: …`、記録の `model` は `litellm:<model>`。`tools/gemini_usage.py` は `litellm:<用途>` として数え、Gemini の値段では費用を出さない
- **設定**: `llm.remote.litellm`（`LITELLM_URL`、`LITELLM_MODEL`、`LITELLM_API_KEY`）。モデル名は必須（`/models` の最初のモデルが目当てとは限らない）。コンテキストは 20 万、出力は 8192 を既定にした（Haiku の実際の上限は確かめていない。400 が返ったら下げる）
- `response_format: json_schema` や `tool_choice: required` を断られたら、ローカルと同じく以後送らない（36 §3）。`response_format` は用途ごと、返事が来ないときも外す（設計書 141: Anthropic は optional 24 個超のスキーマを断り、入れ子の深い `plan` は返事が来ない）

## 4. 接続先と方針（2026-10-09）

ユーザー: 「使用モデルをクラウド版に切り替える時は全てこの LiteLLM 経由でやります」。クラウドのモデルは `https://llm.ideta.net/v1`（既定の `LITELLM_URL`）の LiteLLM 経由に統一する。鍵は pass の `LITE_LLM_MASTER_KEY`（`LITELLM_MASTER_KEY` ではない）に置き、`.env` には書かず、起動のときに `LITELLM_API_KEY="$(pass show LITE_LLM_MASTER_KEY)"` で渡す。ローカルのモデル（`llm.local`、FreeToken）は今のまま。

確かめたこと（2026-10-09、`/v1/models` と `/model/info`）: `llm.ideta.net` には 24 モデルあり、Claude 系（Haiku 5.5）はまだ載っていない。Gemini は `gemini-3.7-flash` だけで、今の `gemini-3.8-flash` はない。載るまで `REMOTE_LLM=litellm` にしても `LITELLM_MODEL` に入れる名前がない。

## 5. 思考・モデル・キャッシュ（2026-10-09、実機の LiteLLM と Haiku 5.5 で確かめた）

ユーザー: 「順にすすめてほしい。状況に応じて思考もオンになるようになっていると嬉しい。コード実装時は Sonnet も使うように」。

**確かめたこと**（`https://llm.ideta.net/v1`、`claude-haiku-5-5` / `claude-sonnet-5-5`）
- Haiku 5.5 は指定しないと思考が走る（数千トークンの前置きの質問で 160〜300 トークン。上限 300 の呼び出しは思考だけで使い切って答えが空）。`thinking: {"type": "disabled"}` で 0 になる。`reasoning_effort: "none"` と `low` では止まらない。`thinking: adaptive` + `reasoning_effort: high` で思考が走る
- 思考の設定を呼び出しのあいだで変えると、プロンプトキャッシュが外れる（「思考あり」で書いたものは「思考なし」で読めず、書き直しになる）。用途ごとに固定する
- `json_schema`、`tool_choice: required`、画像（`data:` URL）は Haiku 5.5 で通る（`json_schema` はスキーマによる: 設計書 141）
- `cache_control` は system のブロックにも、ユーザーメッセージの部品（変わらない部分）にも効く。`ttl: "1h"` もブロックごとに効く（5 分で足りるので使わない）。使用量は `prompt_tokens_details.cached_tokens` と `cache_creation_input_tokens`

**思考**: `thinking_param: anthropic`（既定）。Gemini の `thinking_levels` が medium / high の用途だけ思考させる（`thinking: adaptive` + `reasoning_effort`）。ほかは `thinking: disabled`。つまり失敗の後（`tool_after_failure`、`tool_stuck`、`goal_after_failure`）、画面を見る呼び出し、計画の呼び出し（town、site、house、build_design）、コードを書く呼び出しで思考する。Claude 以外のモデルを LiteLLM 経由で使うときは `thinking_param: none`

**モデル**: `llm.remote.litellm.models`（用途 → モデル）。`LITELLM_CODE_MODEL=claude-sonnet-5-5` を書くと、コードを書く用途（`skill_write`、`check_write`）が LiteLLM に回ったとき Sonnet になる。振り分けは変えない: `llm.routes` が local の用途はローカルのモデルで書く（ユーザー 2026-10-09: 「llm.routes がローカルの場合はコードもローカルのモデルで書いてほしい」。最初は models に書いた用途を LiteLLM に回していたが、やめた）。Sonnet で書かせたいときは、`llm.routes` でその用途を `remote` にする（ローカルを切っていれば全部 LiteLLM）。思考ありの用途だけを書く（Sonnet 5.5 は思考を切れない）。同じしくみで `LITELLM_ADVISOR_MODEL` が用途 `advice` のモデルを決める（迷ったときに聞く advisor。設計書 143）。使用量の行と記録の `model` は、実際に答えたモデル

**キャッシュ**: `prompt_cache`（`LITELLM_PROMPT_CACHE`、既定 true。Claude 用。Gemini を LiteLLM 経由で使うときは false: `cache_control` を付けると明示キャッシュに切り替わり、暗黙のキャッシュが止まって保管料がかかる）
- プロンプトに境目の印 `CACHE_BREAK`（`text_generator.py`）を置く。アダプターは、印の手前のブロックと system に `cache_control` を付ける（最大 4 つ。印のないプロンプトには付けない: 末尾に付けると書き込みの割増だけを払う）。Gemini とローカルは印を取り除くだけ
- 道具の 1 手（`TOOL_TEMPLATE`）を並べ替えた: 決まり（変わらない）、印、目標の階層（`format_activity(cache_break=True)` が今の小目標の前にも印）、今の状態。前は決まりが状態の後ろにあり、先頭から同じ部分は全体の 26%（中央値）だった。並べ替え後の試算は 40%（印で区切った前置きの一致）
- 実機で 4 回送って確かめた（過去のプロンプトを並べ替えて Haiku へ）: 1 回目に 3958 トークン書き込み、2〜4 回目は 3958 トークンを読み出し（プロンプト 8.2〜8.5k のうち約 47%）
- 使用量の行に `cache_write=`（書き込んだトークン）、記録の `tokens` に `cached` と `cache_write`

**あとで**
- 状態の後ろにある変わりにくい部分（これまでの小目標、メモ、教訓）を前に出す、3 つ目の境目
- 道具の 1 手以外（目標の決定は system がある。実況・返事）の並べ替え
- 並べ替えで決定の質が変わっていないかの確認（`mise run bench` か、配信でのログ）。決まりが状態の前に来たので、指示の効き方が変わりうる

## 6. 決めなかったこと・あとで

- 実機（実際の LiteLLM と Haiku 5.5）での確認: モデル名、`json_schema` と `tool_choice: required` が通るか、画像の読み、1 手の速さ
- Claude の思考の指定（LiteLLM の `reasoning_effort` など）
- ローカルを切って全部を Haiku にしたときの用途ごとの出力の上限（`output_max_tokens` はローカルだけに効く）
- `gemini.filter_model`（コメントの振り分け。予定）の置き換え

## 7. Gemini の直接の呼び出しをやめた（2026-10-09）

ユーザー: 「gemini は litellm 経由で呼べるので gemini 専用の環境変数は消したい」。選んだ形は、LiteLLM の OpenAI 互換に一本化する（Gemini のアダプターを残して向き先だけ変える案はやめた）と、モデルごとの違いはモデル名で自動で決める。

- **消したもの**: `GeminiTextGenerator`（`adapters/gemini/`）とそのテスト、`google-genai` の依存、`GEMINI_API_KEY`、`settings.gemini`（`GeminiSettings`、retry、rate_limit）、`llm.remote.provider` / `REMOTE_LLM`、`llm.remote.litellm.thinking_param`、`LITELLM_PROMPT_CACHE`。リリース前なので互換は残さない
- **移したもの**: `llm.remote.litellm` → `llm.litellm`。思考の表 `thinking_levels` と `default_thinking_level`（旧 `gemini.main_thinking_level`）、呼び出しの記録（`generation_log_dir`、`generation_log_keep_days`）、`debug_log_size` は `llm` の直下。`create_text_generator(llm, generation_log)`、`create_llm_service` / `create_game_service` / `collect_run_info` から `gemini` の引数をなくした
- **失ったもの**: 画像の解像度の指定（`media_resolution`。OpenAI 互換の `image_url` には `detail` しかなく、使っていない）、思考の要約（`include_thoughts`。`--debug` で `/debug/gemini` に出していた）、SDK の再試行とレート制限（1 秒に 1 回）。LiteLLM 側の再試行に任せる。ローカルと同じ `max_concurrent` で同時に送る数だけ抑える
- **モデル名で決める**: 名前が `claude` で始まる（`anthropic/claude-…` も）呼び出しにだけ、思考の指定（`thinking: adaptive` + `reasoning_effort` か `disabled`）と `cache_control` を付ける。それ以外（`gemini-3.8-flash` など）には `cache_control` を付けず、思考の深さ（`thinking_levels` の low / medium / high）をそのまま `reasoning_effort` で送る。用途ごとのモデル（`models`）が Claude ならその呼び出しだけ Claude の指定になる。実機（2026-10-09）で `gemini-3.8-flash` は `reasoning_effort` の low / medium / high を受け（同じ問いの思考が 69 / 92 / 183 トークン）、`none` / `minimal` は 400、`thinking: disabled` は効かない（思考を切れない）。指定なしでも思考が走る（挨拶で 200 トークン前後）。使用量の行の「thinking off」は Claude の `disabled` だけを指し、Gemini では off と出ても思考は走る
- **必須の設定**: `LITELLM_API_KEY` と `LITELLM_MODEL`（`stream.py` が両方を確かめる）。Gemini 3.8 にするなら `LITELLM_MODEL=gemini-3.8-flash`。`llm.ideta.net` に載っているモデル名で確かめる
- **未確認**: `gemini-3.8-flash` を LiteLLM 経由で実際に動かした配信（この日の確認は Haiku 5.5 と Sonnet 5.5）。LiteLLM に載っている Gemini の名前
