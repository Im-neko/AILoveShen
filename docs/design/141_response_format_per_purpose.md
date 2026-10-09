# 141. response_format を断られたら、その用途だけやめる

2026-10-09、user: 「LiteLLM が response_format を受け取らないはおかしい。調べてもらえますか？」→ 調査 → 「ログで受け取らないと出ていました。通るように修正してもらえますか？一度通らないともう使わなくなっちゃうので」

## 1. 何が起きていたか
- LiteLLM は `response_format`（`json_schema`、strict あり・なし、`json_object`）を受け付ける。断っていたのは LiteLLM でなく Anthropic で、スキーマによって通る・通らないが分かれる（`llm.ideta.net`、`claude-haiku-5-5` に直接送って確かめた）:
  - `reply`: 400「Schemas contains too many optional parameters (27)… (limit: 24)」。optional のプロパティが 24 個を超えるスキーマは通らない
  - `plan`: optional は 22 個で上限内なのに、`response_format` をつけると返事が来ない（Cloudflare 524 まで 125 秒）。つけなければ 1.4 秒。前提を入れ子で何段も書く形が重いと見ているが、原因は確かめていない
  - `town`、`stage`、`promise`、`lesson_when`: 通る
- アダプターは 400/422 を 1 回受けると `_use_response_format = False` にして、プロセス全体で以後 `response_format` を送らなかった。1 つのスキーマが断られただけで、通る用途も使えなくなっていた
- 返事が来ないとき（読み取りの時間切れ、5xx）は救済がなく、`timeout_seconds`（LiteLLM は 120 秒）待ってエラーになった

## 2. 直したこと
- 断られた用途（`purpose`）を覚え、その用途だけ以後 `response_format` を送らない（`OpenAICompatTextGenerator._no_response_format`）。スキーマでなく用途にしたのは、目標の決定のようにスキーマの中身が呼ぶたびに変わる用途があるため。`response_format: off` は今までどおり全部送らない
- `response_format` つきの呼び出しが 400/422 のほか、読み取りの時間切れや 5xx でも、`response_format` を外して聞き直し、通ったときだけその用途で以後送らない。外しても失敗するなら（回線の断など）何も覚えず、そのエラーを返す
- スキーマは今までどおりプロンプトにも書き、答えをコードで確かめて 1 回書き直させるので、外した用途も形は守られる（設計書 36 §3）

## 3. 直していないこと
- `plan` のように返事が来なくなるスキーマは、起動のたびに最初の 1 回だけ `timeout_seconds` 待つ（実機で 126 秒）。覚えるのはプロセスの間だけ
- Claude 系で最初から送らない案（送る意味が薄い）、optional を 24 個以内に収める案は、していない
- `plan` が重い原因は未調査

## 4. テスト
`test_local_llm.py`: 断られた用途だけ外れて別の用途は送り続ける、時間切れ・524 で外して聞き直す、外しても時間切れなら何も覚えずエラー。実機の LiteLLM で本物の `reply` / `plan` / `town` のスキーマを通し、`reply` と `plan` だけが外れ、`town` は送り続けた。
