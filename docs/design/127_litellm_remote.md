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
- 2026-10-15（設計書 177）: 印は最後の 1 つだけ 5 分、ほかは `ttl: 1h`（LiteLLM 経由で 340 秒あとに読めると確かめた）。system の印は、道具の定義が前にあるときだけ。`response_format` のスキーマ、思考の設定、道具の定義はどれも先頭側に入るので、1 つでも違うとキャッシュが全部外れる（スキーマが 1 文字違うと、同じ本文で読みが 0）
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

## 8. 任意の項目が多いスキーマは Claude に送らない（2026-10-17）

user: 「Schema contains too many optional parameters ってエラー大量発生してるらしいです。改善できないですか？」

- **事実**: LiteLLM 経由の Claude の `response_format: json_schema` は、任意の項目（`required` にないプロパティ。入れ子の object も足す）が 24 を超えると 400 `Schemas contains too many optional parameters (25)` で断る（実機で 12 / 16 / 20 / 25 / 30 を送って確かめた）。手前でも重い: 12 個で 34 秒、16 個で 40 秒待っても返事が来ず、20 個で `Schema is too complex`。個数は `goal` 49（失敗後 59）、`plan` 26、`reply` 28。`skill_write`（10 個）は初回 5 秒、2 回目から 1.2 秒で問題ない
- **前の挙動**: 断られてから、その用途で `response_format` を外す（`_post`）。外した状態はプロセスの中だけなので、再起動のたびに `goal` / `goal_after_failure` / `plan` で 1 回ずつ 400 とやり直しが起きた（10-15 から 30 回）。LiteLLM 側のエラーの数としては多く見える
- **変更**: `openai_compat_text_generator.py` `_too_complex_for`: Claude のモデルで任意の項目が `CLAUDE_OPTIONAL_LIMIT`（24）を超えるスキーマは、最初から `response_format` を送らない（用途ごとに 1 回 INFO で知らせる）。スキーマはいつもプロンプトに書いてあり、答えは `check` で検査するので、送らなくても同じ。ほかのモデル（Gemini、ローカル）は変えない。断られて外す仕組みはそのまま残す（上限が変わったときの備え）。数えるのは `schema_check.py` `optional_parameters`
- **直していないもの**: `goal` のスキーマ自体の任意の項目（49）を減らすこと。減らせば構造化出力の保証が戻るが、今の答えの検査と差し戻しで足りている。送らない用途の検査の失敗率は `tools/model_compare.py` で見られる
- テスト: `test_local_llm.py`（入れ子の数え方、Claude には 25 個のスキーマを送らず 24 個は送る、Claude 以外には送る）。Python 1859 通過。実機では未確認（再起動後に 400 が出ないことを見る）

### 8.1 計測（同日）: スキーマ違反は計画だけが多い

user: 「しばらく動かして計測してみて」。`response_format` は、断られて外した後はずっと送られていなかった（再起動のたびに最初の 1 回だけ送って 400）ので、10-16〜17 のログが「送らない」ときの実測になる。`stream.log` の「JSON を書き直させる」を用途ごとに数えた:

| 用途 | 呼び出し | スキーマに合わず書き直し | 割合 |
|---|---|---|---|
| goal | 1829 | 3 | 0.2% |
| goal_after_failure | 450 | 4 | 0.9% |
| plan | 312 | 66 | 21% |
| skill_write | 24 | 1 | 4% |

- `goal`（任意の項目 49）は、送らなくても違反がほとんどない。**スキーマを書き直す理由はない**。`response_format` を外す今の扱いでよい
- `plan` は 21%。66 回のうち 53 回が `$.jobs[].steps[].needs[].reason is required`（前提の小目標の理由の書き忘れ）、13 回が JSON の途切れ（Unterminated string など）。計画は平均 19 秒、最大 59 秒で、書き直し 1 回が 1 回分の呼び出し
- **直した**: `job_planner.py` `_step_schema`: 前提（needs）の中の小目標は理由を必須にしない（読み出し `_parse_step` は空を許す）。小目標と中目標の理由は必須のまま。`plan` のスキーマは任意の項目が 26 で、`response_format` では送れない（送ると 400 か、前に実機で 2 分止まった）ので、スキーマ側で書き忘れを許すのが筋
- JSON の途切れ（13 回）は、`finish_reason: length`（8192 トークンで切れた）が原因だった。思考と答えで上限に届いた計画。切れた JSON は書き直させても同じ長さで切れるので、思考中に構造化した答えが切れたら、思考なしで 1 回聞き直す（`_chat`）。思考なしの出力の上限 `plan` を 3500 → 5000 に（計画の答えは 3000〜4500 トークンになる）
- 実機の効果は未確認（再起動後に `plan` の書き直しの数が減るか）。見るもの: 同じ集計を後で取り直す

### 8.2 LLM のリクエストとレスポンスの監視（2026-10-17）

user: 「LLMのリクエストレスポンスも監視対象にして適宜改善していってください」。`tools/llm_health.py` を足した（`logs/generations` と `logs/stream.log` から、用途ごとの呼び出し数、応答時間の平均・95%・最大、プロンプトのトークン、キャッシュに当たった割合、途切れ・空の答え・エラー、ログ側のスキーマ書き直し・差し戻し・LLM エラー）。`--since "YYYY-MM-DD HH:MM"` で再起動の時刻から。

最初の診断（22:41 以降）と、そこから直したこと:

- **502 / 503**: LiteLLM の前の中継が HTML を返して 1 ステップが失敗していた（2 日で 13 回）。`_send` が 2 秒・6 秒待って 2 回までやり直す（`TRANSIENT_STATUSES`）。通らなければ今までどおりエラー
- **計画の差し戻し（約 25 秒ずつ）**: 計画のプロンプトの `facility` の説明が「施設（畑、植林場、採掘場）…新しい name を書くと設計する」で、モデルは `facility(mine_5)`（新しい採掘場）を書く。計画の検査は「採掘場は小目標ではない。鉱石の have が作って掘る」と断る。プロンプトと検査が食い違っていた。説明に「採掘場の新しい名前は書かない（鉱石の have() が作って掘る。書いてよいのは、もうある採掘場だけ）」を足した（`stream_context.py`）。目標の決定のシステム指示の長さの上限を 9500 → 9700
- **見つけたが直していない**:
  - `live`（2 日で 2084 回）と `commentary`（1030 回）の入力は 1 回 8000 トークンで、当たりは 32〜38%（未キャッシュ合わせて約 17M トークン）。**調べた結果、直す価値は小さい**: 最小トークンの壁はない（Haiku も Sonnet も 3000 トークン級から実測でキャッシュされる）。印も付いている（人格の決まりのあと、目標の階層と今の状態の間）。当たりが 3 割台なのは、印より後ろの「今の状態」の節（約 4800 字）が呼び出しごとに変わるため。その中で変わらない部分（家、建物の呼び名）は約 800 字（約 450 トークン、5%）で、並べ替えるほどではない。減らすなら、実況に要らない行（畑、チェストの中身、覚えている場所、バイオーム）を削る案だが、実況の材料になるので保留
  - `advice`（Sonnet、308 回、未キャッシュ 2.8M トークン）は先頭の一致が 36% で、残りは毎回違う状況。呼び出しの間隔も長く、直す価値は小さい。`skill_write` は 1 時間に数回で同様
  - 未キャッシュの入力の多い順（10-15〜17）: tool 14.0M、live 11.3M、goal 8.4M、tool_after_failure 8.4M、commentary 5.7M、tool_stuck 5.2M、goal_after_failure 3.6M、advice 2.8M


- 訂正: 先に「目標の決定が規則に合わない失敗が 10-16〜17 に 450 回」と書いたが、すべて 10-16（大目標が空になった日で、設計書の別の修正が済んでいる）。10-17 は 0 回
- ディスク満杯（`No space left`、10-16 19:16〜20:13 に 11 回）は、このプロジェクトのログ（`logs` 1.1GB）ではなく、マシン全体の空きの問題。今は空き 95GB
