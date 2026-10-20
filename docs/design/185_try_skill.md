# 185. 保存せずに 1 回だけ試す道具（try_skill）と、成功判定の前進（at / height）

2026-10-17。user: 「直近の作業として 1,2 を進めて欲しいです」(理想までの距離の答えで挙げた (1) 使い捨ての実行、(2) 部分的な前進の成功判定)。(3) は設計書 184。

## 1. 事実

- 技を試すには `write_skill`（別の LLM 呼び出しで書き、ブリッジに保存し、すぐ 1 回走らせる）しかない。保存した版は成績（uses / failures / streak）に数えられ、同じ理由の失敗が 5 回続くと足止めされ、Python の台帳（設計書 184）でも外される。「うまくいくか分からない動きを 1 回やってみる」ことが、失敗のたびに技の履歴を汚す
- 実際の技の成績（10/15〜）: `run_skill` 188 回のうち成功 3、`write_skill` 13 回のうち試走で通ったのは 2。`descend_stair_raw` は 16 回中 14 回失敗
- 成功の判定は、技の `expects`（世界で判定）。`at` は「目的地の近くにいる」だけで、遠くへ歩く技が途中で終わっても失敗になる。`descend_stair_raw` は y48 → y30 まで 18 下りて、「高さが 18 離れている」で失敗だった（設計書 184 §2.1 で `height` を足した）

## 2. 変更

### (2) 成功判定

- `expects.at` に `min_change`（始めより目標へこれだけ近づけば、途中でも成功）。距離は水平と高さの合成。`expectsBaseline` が始めの位置を取る（`height` と同じ形）。`skills.mjs`、`skills.py` のスキーマ、技を書く指示、ブリッジのテスト
- `SkillInfo.needs_arguments` は、`expects` が `$引数名` を参照するときも真。前は `params.required` だけで、`expects` が `$dest` の技を Jev が引数なしで選び、「expects refers to $dest but the argument is missing」が続いた（22:00）

### (1) try_skill

- 道具 `try_skill {name, what}`（`write_skill` と同じ条件で出る。道具の組は固定: 設計書 177）。`write_skill` と同じ書き手（`SkillWriter`）が、API を知っている別の呼び出しで書く。道具選択のプロンプトに API を載せない（キャッシュを壊さない）。ここが inline の `code` を取る道具にしなかった理由: 道具の選択のプロンプトは `t.*` の API を持たない
- ブリッジ `POST /skills/try {definition, args, run?}`: 形と構文を確かめて（`run: false` なら確かめるだけ）、1 回走らせる。**保存も成績の記録も足止めの判定もしない**。上限は `TRY_RAW_MS`（3 分。保存した技は 10 分）。結果に、走る前後の位置と持ち物の増減（`from` / `to` / `inventory_change`）を添える。`expects` が通らなくても、何が起きたかが分かる。実行と判定は `executeSkill` を `runSkillOnce` と共有
- Python: `SkillWriter.write(unsaved=True)` は保存せず `check_skill` だけ。続けて直すとき、前のコードは保存した版ではなく直前の試し。原因（diagnosis）を書かせ、同じコードは差し戻す（保存する技と同じ）。試しの回数は 1 時間に 8 回（`tries_per_hour`。書く 3 回とは別）
- うまくいった試しは `mark_proven`。次の `write_skill`（同じ名前）は、書き直さずにそのコードを保存し、試した引数で走らせる（30 分以内）
- 失敗は `_skill_failures` に残り、次の `try_skill` / `write_skill` の「前の失敗」になる。見張りが止めたものは残さない。`HandLedger` は数えない（試しは外す手ではない）
- プロンプト: 外した手の節に、まず `try_skill`、うまくいったら `write_skill`。`abilities.yaml` の `skills`

### (2) 続き: 判定できない引数は走らせる前に断る（同日）

`descend_stair_raw` を `dest: "mine_4"` や `"new"` で呼ぶと、走り終わってから「expects at cannot be judged: where is not x,y,z」で失敗していた（数十秒〜数分の作業が無駄）。`expectsBaseline` が走らせる前に、`at` の `where` が座標か、`height` の `y` が数かを確かめ、だめなら何も走らせずに断る（記録にも数えない。`nothing was run`、座標を渡すよう添える）。

## 3. 範囲外（次）

- 道具の選択の LLM が `code` を直接渡す形（`t.*` の API を持たせる必要がある）
- `at` / `height` 以外の前進（持ち物の増加は `+N`、`progress` が既にある）
- ゲームの知識（層など）を構造化したテキストで引く道具（WORKLOG の Next Steps）

## 4. テスト

ブリッジ `skills.test.mjs`（`height`、`at` の `min_change`）。`test_skills.py`（書き手の unsaved / 保存、直前の試しから直す、試しの回数、`try_skill` の 1 ステップ、`needs_arguments`）、`test_tool_control.py`（道具の組）。Python 1857、ブリッジ 1094 通過。実機では未確認（ブリッジの `/skills/try` は再起動後に確かめる）。
