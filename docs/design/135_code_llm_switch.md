# 135. ローカルのまま、コードを書く用途だけ Sonnet に回す口

2026-10-09、user: 「ローカルモデルを使いつつコーディングだけsonnetにする口もおいておいてほしいです。」

## 1. 前提（127 §5）
- コードを書く用途は `skill_write`（スキルの作成・修正）と `check_write`（`custom(name)` 条件のチェック）。用途ごとのモデルは `llm.litellm.models`（`LITELLM_CODE_MODEL`）
- 同じ日の user の指示: 「`llm.routes` がローカルの場合はコードもローカルのモデルで書いてほしい」。なので `LITELLM_CODE_MODEL` を書くだけでは回らない（ローカルの振り分けのまま）。回すには `llm.routes: {skill_write: remote, check_write: remote}` を書く必要があり、`config/development.yaml` を編集する手間があった

## 2. 口
- 環境変数 `CODE_LLM`（`local` が既定、`remote`）。`config/default.yaml` の `llm.routes` の `skill_write` / `check_write` が `${CODE_LLM:-local}`
- `CODE_LLM=remote` と `LITELLM_CODE_MODEL=claude-sonnet-5-5` を置けば、ローカルのまま、この 2 つの用途だけ Sonnet（思考あり。`thinking_levels` が high / medium）。ほかの用途（目標の決定、道具の 1 手、実況、返事）はローカル。画像・長すぎる呼び出し・`fallback` のリモートは今までどおり `LITELLM_MODEL`
- `LITELLM_CODE_MODEL` が空なら、リモートの `LITELLM_MODEL` で書く。`LITELLM_MODEL` もなければ、ローカルで書き、起動のログに警告を出す（振り分けは `no remote` でローカルに落ちる）
- 個別に回すときは今までどおり `llm.routes`（`CODE_LLM` は 2 つをまとめて切り替えるだけ）
- ローカルを切っている（`LOCAL_LLM` なし）ときは、全部がリモート。`LITELLM_CODE_MODEL` だけで足りる（`CODE_LLM` は何もしない）

## 3. 注意
- 使うトークンはスキルを書くたびに Sonnet の値段。`logs/generations/` と `tools/gemini_usage.py` の `litellm:skill_write` で数える。費用の換算はしない（127）
- 実機（LiteLLM の Sonnet 5.5 で `skill_write` が通るか）は未確認。127 §5 では `claude-sonnet-5-5` が LiteLLM にあることまでは確かめた

## 4. テスト
`tests/unit/infrastructure/test_config.py` の `TestCodeLlmSwitch`（既定はどちらもローカル、`CODE_LLM=remote` で 2 用途だけ remote）。リモートに回ったときの用途ごとのモデルは `test_text_factory.py` と `test_local_llm.py` が見ている。
