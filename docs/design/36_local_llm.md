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
- **考える深さ**: Gemini の `thinking_levels` を使い、`medium` / `high` は思考あり、`low` は思考なし。渡し方は `thinking_param`（`chat_template_kwargs`: `{"enable_thinking": …}`、FreeToken / vLLM / llama.cpp の Qwen、または `none`）。答えに `<think>…</think>` が混ざったら取り除く
- 同時に送るのは `max_concurrent`（2）まで（KV キャッシュの取り合いを避ける）
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
    max_output_tokens: 4096
    response_format: auto     # auto | off
    thinking_param: chat_template_kwargs   # chat_template_kwargs | none
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
