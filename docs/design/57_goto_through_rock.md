# 57. 岩に囲まれた所からの goto（2026-09-30）

ステータス: 実装済み（実機では未確認）。ブリッジの再起動が要る。

## 問題

ログ: `[tool] goto {"x":-243,"y":54,"z":-332}: 失敗 failed: Took to long to decide path to goal!`。ボットは石の中の小さな空洞（歩いて出られない）にいて、目標は岩の向こうの約 10 m 先だった。

mineflayer-pathfinder は `thinkTimeout`（5 秒）のうちに経路を決められないと、この文言で失敗する。歩ける所が閉じていて目標が岩の向こうだと、探索は「掘って進む」道を岩の中へ広げ続け、掘る距離が長いほど探索が膨らんで間に合わない。同じ goto を繰り返しても、探索の広がりは変わらないので同じく失敗する。

## 直したこと

### 1. 私が足した経路探索の液体の掘りコスト（コスト 10000）を外す

設計書 55 で足した `exclusionAreasBreak` の行は、mineflayer-pathfinder の `dontCreateFlow`（既定で真）がもともと同じ判定をしているので、効果がなく、壊す候補の読み取りが増えるだけだった。行と `LIQUID_BREAK_COST`、使わなくなった `holdsBackLiquid` の import を消した。掘る経路の保護は、`dig` の道具・primitive・候補・`find_blocks` の側の判定（設計書 55）のまま。

### 2. `goto` の事前チェック（`reach.mjs`）

道具 `goto` は、経路探索を始める前に、歩いて行ける範囲を幅優先で調べる（3000 マスまで）:

- 足元から、東西南北へ 1 マス。同じ高さ、1 段上（頭上が空いている）、3 マスまでの段差を降りる。床は固いブロックか水。扉・フェンスゲート・トラップドア・葉は通れる
- 目標から 4 ブロック以内のマスに届けば、行ける（そこで打ち切る）
- 3000 マスを超えるか、読み込まれていないブロックに届けば、閉じているとは言えないので調べを終えて通す。水の中と船に乗っているときは調べない
- 歩ける範囲が閉じていて、目標がそこから 4 ブロックより遠ければ、`goto` を断る: `will not walk to x,y,z: you are in a closed space (N cells you can walk in) and the target is about D blocks of rock away. The path search gives up after 5 s on that much digging. Dig your way out first (dig the blocks toward it, or a stair up), or go_home`

4 ブロックは、掘って進む探索が 5 秒に収まる目安（1 マス掘るごとのコストは歩きより大きいが、数ブロックなら足りる）。

断りは `refusals.mjs` の台帳には入れない。台帳に入れると、掘って出たあとの同じ goto まで 5 分間止まる。

## 限界

- 断るのは道具の `goto` だけ。primitive の `goto_pos`（候補・ソルバー・`go_home`）は事前に調べない。`go_home` は家までの帰り道に独自の掘り出しを持つ
- 「閉じている」は、探索の範囲（3000 マス）で決める。とても広い洞窟の中の行き止まりは、閉じていないと判定して通す
- 目標の周りの高さは見ない。目標が空中にある、水没している、などの理由の失敗は今までどおり
- `thinkTimeout` は変えていない（15〜20 秒に伸ばす案は、ユーザーが採らなかった）
