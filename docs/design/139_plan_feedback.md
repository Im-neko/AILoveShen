# 139. 計画の差し戻しを、全部まとめて・直せる形で言う

2026-10-09、user: 「中目標ないんだけど」→ 調査 → 「1から順番に進めてほしいです」

## 1. 何が起きていたか
- 大目標「エコー・プレインに引っ越す」(m10) が「取り組み中」なのに jobs が 0 件。計画（`JobPlanner.plan`、3 回まで）が 3 回とも通らず、あきらめて `explored(96)` などをその場で決めていた（`logs/generations/gen-20261009T074140Z.jsonl` の `plan` 5 回）
- 保存した出力をブリッジに通し直すと（読むだけ）、毎回この 3 種類の誤りが混ざっていた:
  1. `farmed(wheat, 100)` / `(…, 200)`: count は 1〜64（スキーマの上限は 256 のまま）
  2. `leveled(160,-462, 8)`: where は `home` か x,y,z。モデルには候補地が x,z（`（160,-462）`）としか見えず、y が分からない
  3. `placed(bed, 160,-462)`: 家以外の座標では `bed` は置ける物として通らない（`placed(bed, home)` は家具で通る）
- 差し戻しの理由は 1 回に最初の 1 件だけだった（ブリッジの `checkConditions` は最初の誤りで投げる）ので、モデルは直したそばから次を踏んで 3 回が尽きた

## 2. 直したこと（順番に）
1. **全部まとめて差し戻す**: `goals.mjs` `checkConditions` は、判定できない条件を集めて `farmed(wheat, 100): count must be 1-64; leveled(160,-462, 8): where must be …` と全部言う（1 件だけなら今までの文のまま）。`job_planner.py` `parse_plan` も、全部の中目標・小目標の誤りを `job 1: step 2: …; job 3: …` とまとめて言う
2. **候補地の立つ高さを見せる**: 測量（`survey.mjs` `surveySite`）が中心の `stand_y`（地面の 1 つ上）を返し、候補地の行は `（x=160 z=-462 立つ高さ y=64、家から 96m）`、決めた街の場所は `（160,64,-462）` と y 込みで出す（`stream_context.py`、`describe_site`）。y が分からない候補地（この修正の前に測ったもの）は今までどおり x,z だけ。スキーマの `where` の説明に「3 つの数字が分かるときだけ x,y,z を書く。x,z だけは差し戻す」と書いた
3. **count の上限を説明に書く**: `have` / `stored` 1〜256、`farmed` と `placed` 1〜64、`planted` 1〜32
4. **家以外の `placed(bed, …)`**: 「a bed is placed in the home only: write placed(bed, home) (the house comes first: built())」と、直し方を言って断る。ベッドを家以外に置く機能は作っていない（置くのは家具の手だけ）

## 3. 直していないこと
- 今回の `エコー・プレイン`（東）の行には、まだ `stand_y` がない（前に測ったため）。モデルは y が分からないので、`leveled` の座標は書けず、家や `build:<名前>` を使うことになる。測り直す（候補地まで行く）まで y は出ない
- `leveled(x,z)` の 2 つの数字から、ブリッジが y を補う案は見送った（整地の記録 `state.leveling` が y を使うため、変更が広い）
- 差し戻しの試行回数（3）は変えていない

## 4. テスト
`check.test.mjs`（全部まとめて言う・1 件はそのまま・ベッド）、`test_job_plan.py`（`parse_plan` の全部まとめ・スキーマの説明）、`site.test.mjs`（`stand_y`）、`test_stream_context.py`（立つ高さの表示）。
