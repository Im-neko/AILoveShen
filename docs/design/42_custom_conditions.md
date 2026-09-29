# 設計書 42: 任意の条件（custom）

> 語彙は 50（目標の 4 層）で入れ替わった: 旧「大目標」= 最終目標（`Mission`）、旧「中目標」= 大目標（`Objective`。旧 `MidGoal`）、旧「手順」（`plan_steps`）= 中目標（`Job`。旧 `PlannedStep`）。この文書の本文は書いた時の語彙のまま。

## 1. きっかけ

ユーザー（2026-09-27）:「目標には任意のものを設定できるようにしたいです」。完了の判定は「Gemini が判定コードを書く」を選んだ。

中目標・小目標は、世界から判定できる条件（have / placed / built / leveled …）でしか持てない。語彙にない頼みは、断るか、約束だけして何もしないかのどちらかだった（「家の前に花を並べて」「羊を柵に集めて」「牛を増やして」）。語彙を 1 つずつ足すのでは追いつかない。

## 2. 述語

`custom(name)`: Gemini が書いた判定 `name` が成り立っている。
- 判定は JS の `function check(w) { return { met, remaining, lines } }`。ブリッジの isolated-vm で、読むだけの API `w` を使って同期に動く（`/check` と `/observe` の評価は同期なので）
  - `w.block(x, y, z)` → `{name, solid, props?}` か null（読み込まれていない）
  - `w.find(name, {near?, max_distance?, count?})` → `[[x, y, z], ...]`（64 m、256 個まで。`*_suffix` で種類）
  - `w.inventory()`、`w.me()`、`w.home()`（door / inside / min / max）、`w.chests()`（家のチェストの記憶）
  - `w.mobs(name?, radius?)` → `[{name, x, y, z, hostile}]`（ボットのまわり 64 m まで）
  - `w.time()` → `{time_of_day, day}`
- 上限: 1 回 50 ms（CPU）、ブロックの読み 20000、探す 20 回、16 MB、コード 3000 文字。async / await / Promise は受け取らない。require・fetch・時計はない
- 返り値を確かめる: met は真偽、remaining は 0 以上の数（met でないなら 1 以上。停滞の判定がこれで進む）、lines は 5 行・160 字まで
- 中目標の条件・小目標・技の expects になる。道具モードだけ（候補モードではブリッジが何も出さないので、`predicates_now` は出さない。`CheckWriter` も作らない）
- 世界のもの: `state.json` の `conditions` に保存（技と違い、ワールドを作り直せば消える）

## 3. 書く・登録する

- 中目標の条件に新しい name の `custom(name)` があると、`MidGoalKeeper`（返事の accept、Gemini の中目標の編集、街の段階）が `CheckWriter` を先に呼ぶ（`built(name)` と `BuildDesigner` と同じ形）
- `CheckWriter.write(name, brief)`: brief は中目標の題名と理由。プロンプトは今の足の位置と家（ドア、外壁の角）を座標の基準として示す（`/observe` の home に door / min / max を足した）。`purpose=check_write`（thinking medium）、3 回まで。だめなら `CheckNotWrittenError`
- ブリッジの `PUT /conditions/<name> {description, code}`: 形を確かめ、コンパイルし、1 回動かす。**今もう成り立っていれば受け取らない**（何も確かめていない判定を防ぐ）。400 の理由は Gemini に返して書き直させる。`GET /conditions` で一覧
- 判定が後で壊れたら（投げる、時間切れ）、その小目標・条件は未達で、理由を `blocked` と lines に出す

## 4. 進め方

判定からは何をすればよいかわからないので、候補（葉）は出さない。道具モードで Gemini が道具を組み合わせて進める（`blocked` に「提案はない、道具を選ぶ」と出す）。lines（判定が書く今の状態）が次の手を決める材料になる。

## 5. 実測（2026-09-27、本物の Gemini）

- 「家の前に花を並べる」: ドアの前の範囲（7x5x5）を読んで花の名前（20 種）を数える判定を書いた。サンドボックスで、花なし → 残り 4、4 本 → 達成
- 「家から 10 ブロック以内に羊を 3 匹」: `w.mobs('sheep')` と家の外壁からの距離で数える判定。1 匹 → 残り 2、3 匹 → 達成
- 弱いところ: `w.mobs` はボットのまわりしか見えない（エンティティは近くのものしか届かない）。家から離れているときは羊が数えられず未達になる（見えない所は成り立っていないとする、の決まりどおり）

## 6. 決めなかったこと

- 頼みを達成した道具の並びを技にする（「終わったら自動で技にする」）は次の作業
- 判定の書き直し（世界が変わって判定が合わなくなったとき）は、中目標を断念して新しい名前で足し直す
