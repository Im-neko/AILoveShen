# 98. 池で「陸に上がれない」が 5 分続く

2026-10-07、user: 「残り順番にやってって」（再起動後の約 26 分の足止めの 1 つ）。

## 1. 起きていたこと

01:17〜01:24Z、池の近くで原木を取って木のツルハシを作ったあと、水に入って動けなくなった。「swim to the nearest land at …」が 5 回続けて `could not climb onto land: arrived; you are in the water now, not on land`（`logs/steps`、`logs/bridge.log`）。1 回 15〜30 秒かかり、合計 5 分。01:17 と 01:24 は同じ操作で 2〜3 m 泳いで上がれていた。

## 2. 原因（`swim.mjs` `swimToward` と `land.mjs` `reachLand`）

`findLand` が返す目標は、岸の**いちばん手前の陸のマス**（四角い輪を内側から探して最初に見つかった所）。`swimToward` は目標との水平距離が `ARRIVED_M`（1.2 m）未満で「着いた」にして止まる。岸のマスの中心から 1 マス手前の水のセルは 1.0 m で、岸に押し上がる前に止まる。足はまだ水の中なので `landed` にならず、`reachLand` が「上がれなかった」として別の陸を試す（3 回）。

押し上がる動き（前進 + ジャンプ）は、着く判定の前では 1 回も実行されない。成功した場合は、泳ぐ途中の揺れで岸のマスの上に乗った（偶然）。

## 3. 直したこと

`swimToward` に `climb` の指定を足し、`reachLand` から渡す。`climb` のとき、目標の近くでもまだ水の中にいれば「着いた」にせず、前進とジャンプを押し続ける。`CLIMB_MS`（4 秒）押しても上がれなければ、「arrived but could not climb onto the bank (too high or blocked)」と理由を返す（次の陸を試す）。`climb` なしの泳ぎ（水の目標へ渡る `goto` など）は今までどおり。

- テスト: `swim.test.mjs` に 2 件（岸の最初のマスへ着いたら上がる、高すぎる岸では理由を返す）。直す前は 1 件目が落ちる

## 4. 残ったこと

- `goto` 経由の泳ぎ（`move.mjs`、`primitives.mjs`）の目標が岸の手前のマスのときも、同じ形で水の中で止まりうる（今回の記録は `reachLand` だけ）
- 実機で、池の岸（水面から 1 ブロック高い岸）に上がれるかは未確認
