# 191. 判断なしのプレイ（実験）

2026-10-18。「ブリッジ側の判断やロジックを完全に 0 にして、自己改善だけでどのくらいプレイできるか」を測る実験。通常のプレイ
（`control: tools`）は変えず、三つめの制御 `control: raw` を足した。ベンチマーク（設計書 83）の名前付きの課題 `raw_free`
として、配信とは別のサーバー（25566）とブリッジ（3001）で走らせる。

## 1. 外したもの・残したもの

外す（ブリッジにも Python にも、ここでは入らない）: 目標とその判定、ソルバー、候補、定石・大目標・街、`do_suggestion`、
ブリッジの行動の道具（`goto` `dig` `place` など。断りと `state.goal` に依存する）、助言入りの状態（`needs` `refusals` `in_home`
`closed_space`）、検索（`search_*`）、`find_blocks` `inspect` `diagnostics`（ブリッジが世界から導いた `tree` `quarry` `exposed` や
断る理由が入る）、判定の質問（`t.judge`）、見張り（Jev）、教訓、覚えた候補、行き詰まりの検知。反射の選択も Jev でなく規則。

残す: 生の bot（`t.bot`）の技（設計書 168）と、それを書いて試して直す仕組み（設計書 22、185）、測ったままの状態（`rawState`:
位置・体・持ち物・足元の高さの格子・モブ・時刻）、ゲームの知識（`recipe_of` `find_recipes` `uses_of`）、断面（`cross_section`、設計書 190）、
近くの敵への反射（ブリッジの中。LLM の外）。成功の条件 `expects` は、世界で判定できる 4 つ（`have` `at` `height` `out_of_closed_space`）。

**反射は溺れる・敵に対処するだけで、燃えるのを消す反射も、食べる反射もない**（調べて分かった。通常のプレイで食べるのは、候補の
`eat` を Python が実行していた）。raw では、食べる・火や溶岩を避ける・夜を越すのも LLM が技にする。飢えて死ぬのは結果。

## 2. 構成

- ブリッジ（`BRIDGE_RAW_ONLY=1`、`BRIDGE_RAW_BOT=1` と併用）:
  - `GET /state?raw=1` → `rawState()`（`state.mjs`）。反射と動いている技だけを足す。
  - 技（`skills.mjs`）: 保存は `raw: true` だけ、`expects` は 4 つだけ。技の `t` から `SKILL_ACTIONS`・`t.judge`・助言入りの
    `t.state` を消し、調べものは `RAW_ONLY_QUERIES`（`recipe_of` `find_recipes` `uses_of` `cross_section`）だけ。`t.state()` は `rawState`。
- Python:
  - `application/use_cases/raw_play.py`: `RawStartPlay`（からのセッション）、`RawAdvancePlay`（反射を待つ → 測った状態と課題から道具を 1 つ
    選ぶ → 調べものは同じステップで選び直す → 技を書いて試す・保存して動かす・動かす）。技の動かし方は通常と同じ部品（`SkillWriter`、
    `TRY_OK_NEXT` など）を使う。
  - `tool_catalog.raw_tool_specs()`: `run_skill` `try_skill` `write_skill` と `RAW_QUERY_TOOLS`。見張りの質問（`watch`）はない。
  - `GamePromptTemplateBuilder(raw_only=True)`: `RAW_TOOL_TEMPLATE`（課題・測ったままの状態・覚えた技・直近の結果だけ）、
    `SKILL_SYSTEM_RAW_ONLY`（ブリッジの道具のない API）、`SKILL_TEMPLATE_RAW`。
  - `factories/game.py`: `control == "raw"` なら `RawStartPlay` / `RawAdvancePlay`。技を書く・試す上限は大きくする
    （1 時間あたり 60 / 120 回。試して直すのが測りたいことなので）。
  - 課題の文は `minecraft.agent.raw_task`（既定は「生き延びて、できるだけ先へ進む。木・道具・食べ物・住む場所・鉄・ダイヤ。やり方は自分で」）。

## 3. 測り方

`config/benchmark.yaml` の `scenarios.raw_free`（名前を指定したときだけ走る）。`tools/benchmark.py` の raw 用の経路:
`tools/bench_milestones.py` が、LLM に見せない外側から、ボットの持ち物を 1 秒ごと・置いたブロック（作業台、かまど）を 5 秒ごとに見て、
節目に最初に着いた秒を記録する（一度着いたら戻らない）。節目は、原木、板材、作業台、木のツルハシ、丸石、石のツルハシ、かまど、石炭、
粗鉄、鉄インゴット、鉄のツルハシ、ダイヤ。ダイヤを持てば成功。家は設計も判定もないので節目に入れない。
結果の行に `milestones`、死亡、書いた技の数・版・成功（`skills_saved` `skill_versions` `skill_successes`）、LLM の呼び出しの用途ごとの数。

```
mise run bench -- --tasks raw_free --repeats 1 --cap-minutes 180 --allow-stream
```

配信が動いていても走る（`--allow-stream`。LLM はクラウドなので、時間の測定は共有で揺れない）。ワールドは毎回新しく、昼夜は進む。

## 4. 確認していないこと・限界

- 初回の実行は 2026-10-18 13:50 に始めた。結果は WORKLOG に書く。
- `have` は、ブリッジの条件の判定（`checkConditions`）を通る。持ち物の数を数えるだけだが、判断の部品を一つ使っている。
- `find_blocks` を外したので、LLM は `bot.findBlocks` を自分の技で呼ぶ。見えている範囲（読み込み済み）だけを知る。
- raw の技はメインプロセスの `vm` で動く（設計書 168）。安全の境界ではない。使い捨てのベンチのワールドだけで使う。配信側に
  `BRIDGE_RAW_ONLY` を流さない。

## 追補: 死んだ記録を見せる（2026-10-18）

ベンチ 8 回目（97 分）で 24 回死に、12 分で着いた木のつるはしから進まなかった。LLM に見えるのは「いま」と直近 8 件の道具だけで、死んだこと自体が見えなかったため、持ち物ゼロで夜の野原に戻される理由が分からず、同じ手（周りを見る技 173 回、木こりの技）を繰り返した。ブリッジが世界から導く助言ではなく、起きたことの記録なので、`rawState` に `deaths`（回数と、最後の死の時間帯・座標・何に削られたか）を足した。夜を越す手そのものは入れない（それを技にするのが実験の対象）。
