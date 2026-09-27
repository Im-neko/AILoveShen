# 36. ローカル LLM（Qwen3.6）と Gemini の振り分け

作成: 2026-09-26。19（道具と考える深さ）、21（道具での操作）、23（画面）、26 §4（キャッシュに向くプロンプト）に関係する。

## 1. きっかけ

ユーザー（2026-09-26）: 「費用節約のため、LLM をローカルで動かしている Qwen3.6 に切り替えられるようにして様子を見たい。Vision は無効で動かしているので、Vision だけは Gemini を使いたい。コンテキストサイズの違い、モデルが 2 つになるところをどう扱うか」。サーバーは FreeToken（OpenAI 互換、既定 :1919）。JSON スキーマで縛れないと厳しければ Ollama も考える。

## 2. 形: 用途ごとの振り分け

`ITextGenerator` の実装を 2 つ持ち、`RoutingTextGenerator`（これも `ITextGenerator`）が呼び出しごとに選ぶ。ユースケースは何も変えない。

- 用途（`purpose`）の表 `llm.routes`（`default` と用途ごと、`local` か `gemini`）
- **画像がある呼び出しは必ず Gemini**（表によらない）。ローカルには画像を送らない
- ローカルに送る前にトークン数を見積もり、`context_tokens` から出力の分を引いた量を超えるならその 1 回を Gemini に回す（切り詰めない: スキーマや決まりが欠けると壊れ方が読めない）
- ローカルが失敗したとき: `fallback: none`（エラーのまま。様子を見る段階の既定）か `gemini`
- どちらが答えたかは、使用量のログの行（`Local <model>` / `Gemini <model>`）とデバッグの記録（`model`）に出る

## 3. ローカルのアダプター（`OpenAICompatTextGenerator`）

OpenAI 互換の `/v1/chat/completions`（FreeToken、Ollama、llama.cpp、vLLM）。

- **JSON**: スキーマ（説明つき）をいつもプロンプトの終わりに書く。`response_format: json_schema` も送る（`response_format: auto`）。サーバーが 400/422 で断ったら以後は送らない。答えからコードで JSON を取り出し、スキーマの主な決まり（型、enum、required、範囲、数）を確かめ、違えば理由をつけて 1 回だけ書き直させる。使えない決定の差し戻し（目標の決定、返事）はその上にある
- **道具**: `tools` と `tool_choice: required`（断られたら `auto`）。道具を呼ばずに文で答えたら、「道具の名前（enum）と引数」の JSON で選ばせる
- **考える深さ**: 思考させる用途を `thinking_purposes` で決める（既定 `skill_write` / `house` / `town` / `site`。ほかは思考なし）。ユーザー（2026-09-27）: 「thinking: on だとめちゃ遅いので、普段の操作判断は off に」。道具の 1 手・目標の決定（失敗の後も）・実況・返事は思考なし。渡し方は `thinking_param`（`chat_template_kwargs`: `{"enable_thinking": …}`、FreeToken / vLLM / llama.cpp の Qwen、または `none`）。答えに `<think>…</think>` が混ざったら取り除く
- 同時に送るのは `max_concurrent`（2）まで（KV キャッシュの取り合いを避ける）。配信ではゲームと返事で 1 つの生成器を共有するので、プロセス全体で 2
- 思考で出力の上限を使い切って答えが空（`finish_reason: length`）なら、同じ頼み直しはせずに理由つきのエラー（上限を上げるか、その用途の思考を浅く）
- サーバーが 400/422 を返したら、知らないかもしれない指定（`response_format`、`chat_template_kwargs`、`tool_choice: required`）を 1 つずつ外して試し、通った指定だけを以後送らない
- 使用量の行は Gemini と同じ形（`purpose=` `prompt=` `cached=` `output=`）。`cached` はサーバーが返すとき（FreeToken は `--enable-cache-report`）

## 4. 速さを測る（`tools/llm_probe.py`）

同じ OpenAI 互換の URL に、このプロジェクトの実際の大きさのプロンプト（目標の決定のシステム指示＋道具の定義、約 1 万トークン）を送り、次を出す:

- JSON スキーマが本当に縛るか（enum の外の答えを誘っても守るか）、道具を呼ぶか
- 初回（キャッシュなし）の最初のトークンまでの時間と読み込みの速さ、同じ先頭での 2 回目、別の先頭を挟んだ 3 回目（先頭のキャッシュを複数持てるか）、出力の速さ

FreeToken と Ollama を同じ物差しで比べる。

## 5. 設定

```yaml
llm:
  local:
    enabled: false            # LOCAL_LLM=1 でも有効
    base_url: http://127.0.0.1:1919/v1
    model: ""                 # 空: /v1/models の最初
    context_tokens: 32768
    max_output_tokens: 16384  # 思考を含む。1 回ごとにコンテキストの残り（見積もり）までに減らす
    response_format: auto     # auto | off
    thinking_param: chat_template_kwargs   # chat_template_kwargs | none
    thinking_purposes: [skill_write, house, town, site]   # ほかは思考なし（[] で全部なし）
    max_concurrent: 2
    timeout_seconds: 180
  routes:
    default: local
  fallback: none              # none | gemini
```

## 6. 決めなかったこと・あとで

- 画面を Gemini に文にさせてローカルが判断する（今は画像のある呼び出しを丸ごと Gemini）
- トークン数を数える API（Anthropic の `count_tokens` など）を使う。今は文字数からの見積もり（日本語は 1 字 1 トークン、ほかは 4 字 1 トークン）
- 実機での確認はまだ

## 7. 振り分けの見直し（2026-09-27）

Gemini の支出の上限に達した（403 Spend cap breached）。ユーザー:「振り分けを見直してほしい」。今日（7:20〜14:14、`tools/gemini_usage.py`）の Gemini の入力は、`tool` 685 回・`tool_after_failure` 479 回（どちらも 1 回 1.2 万トークン）で全体の約 8 割。ローカル（Qwen3.6-35B、FreeToken）で崩れた JSON は `goal` / `goal_after_failure` / `reply` / `promise`（複雑なスキーマ）に集まり、`tool`（function calling）には出ていない。返事はローカル 6.6 秒、Gemini 1.9 秒。

- ローカル（`default`）: `tool`、`tool_after_failure`、`commentary`、`live`。回数が多く、形が単純か、数秒遅れても困らない
- Gemini: `goal`、`goal_after_failure`（計画の質。1 時間 36 回ほど）、`reply`、`promise`（視聴者を待たせない、スキーマが崩れやすい）、`town`、`site`、`house`、`build_design`、`skill_write`、`check_write`（少なく、考えさせたい。ローカルの town は 1 回 49 秒）。画像つき（`screen_review` など）はいつも Gemini
- `fallback: gemini`: ローカルが失敗した回だけ Gemini（様子を見る段階は終わった）
- 見込み: 今日の使い方なら Gemini の入力は約 2 割に減る。道具の 1 手は 2.7 秒から 8〜9 秒に延びる
- 有効にするのは `.env` の `LOCAL_LLM=1`（ローカルを切れば全部 Gemini に戻る）

## 8. 画像以外はすべてローカル（2026-09-27）

ユーザー:「ローカルでも 130k まではコンテキストウィンドウ用意してあるので、画像以外は全てローカルにしてほしい」。
- `llm.routes` は `default: local` だけにした（§7 で Gemini に残した goal / goal_after_failure / reply / promise / town / site / house / build_design / skill_write / check_write もローカル）
- `llm.fallback: none`: ローカルが失敗しても Gemini に回さない（崩れた答えはアダプターが誤りを添えて 1 回聞き直す。それでも駄目ならその呼び出しはエラー）
- `llm.local.context_tokens: 130000`（サーバーの `/v1/models` は `max_model_len` 262144）
- Gemini に行くのは、画面の画像つきの呼び出しと、130000 トークンに入らないプロンプト（`RoutingTextGenerator._pick`。ローカルでは受けられないので残した）だけ
- §7 で測ったとおり、goal / reply / promise はローカルで schema を崩しやすく、返答は遅い（6.6 秒対 1.9 秒）。ローカルでは考える（thinking）のは `thinking_purposes`（skill_write、house、town、site）だけで、目標の決定は考えずに答える。`logs` の `Local … purpose=goal` と、聞き直し・エラーの数を見て、崩れが多ければ goal に thinking を足すか、schema を軽くする
