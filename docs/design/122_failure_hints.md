# 122. 失敗と断りに、邪魔なものの名前と次の手を添える

2026-10-08、user: 「こういうのもLLMがぱっと壊せば良いって判断出来るのが理想なんですが難しいですか？」「点検してひつようであれば修正していってください」

## 1. 考え方
草花が乗った苗木のマスで、LLM は約 2 時間「no free ground for a sapling … any more」を受け続けた。この文は何が邪魔かを言わず、同じ型の `till` は名指ししていた。LLM に「壊せばいい」と判断させるには、(1) 失敗が邪魔なものを名指しし、(2) 断りに出口があり、(3) 手の側で片付けられるものは片付ける、が要る。モデルの賢さに頼る前に、この 3 つを点検した。

## 2. 点検のやり方
直近 6 日分のステップログ（`logs/steps`）から、失敗・断り 379 件を理由の文ごとに数え、多い順に「原因を言っているか」「次の手を言っているか」を見た（98 種類）。すでに直した物（座標のマイナス落とし、チェストの記録）と、文が十分な物は除いた。

## 3. 直したもの
| 件数 | 文 | 直し |
|---|---|---|
| 77 | `could not put anything in the chest: it is full` | 知っているチェストの数と記録の空きスロット、次の手（家にチェストを置く、`toss`）。預ける物がもう手元にないときはそれを言う（`noRoomReason`） |
| 31 | `Took to long to decide path to goal!` | 先頭の文はそのまま（`refusals.mjs` が見る）、届かない目標だという見立て（ドアや壁の向こう、閉じた場所、水の向こう）と次の手を添える（`move.mjs` `explainPathFailure`） |
| 27 | `nothing to till … (the ground is air)` ほか | 穴は土で埋める、石などは整地で土に直る、上に固いブロックがあれば掘る、を添える |
| 16 | `Server refused to place oak_fence / torch …: the block is still orange_tulip` | `place_at` が、置くセルの草花を先にどける（柵・松明・`place`・`placed(...)` の手が通る） |
| 41 | `will not plant … only in the cells of a designed farm` | 次に作業するマスの座標を添える（座標を省けばそこを使う）。`till` も同じ |
| 11 | `no free ground for a sapling …` | 邪魔なブロックを名指し（設計書 33 の追記の続き）。道具 `plant` は草花の乗ったマスを受ける |
| 少数 | `no free farmland … any more`、`no ground for a torch`、`nothing to place against` | 邪魔なブロックを名指し |

## 4. 直さなかったもの
- 「整地中なので掘れない」（`will not dig … being leveled`）: 断りには土や石を街の外から取る指示はあるが、そのマス自体の出口はない。出口の中身（整地に任せる、整地の対象から外す）は設計が要る。
- `withdraw: no chest is known to hold X`: 開けたことのないチェストは中身が分からない。記録の合わせ（設計書 121）で数が増えるので、様子を見る。
- `pickup: reached the item but picked nothing up`: 原因は持ち物の空きか、既に拾われたか、拾えない物か、文から区別できない。
- 点検の対象はログに出た 98 種類。`primitives.mjs` の `throw new Error` 135、`tools.mjs` の `refuse` 103 すべてを読んだわけではない。今回のログに出ない文は未点検。

## 5. テスト
`test/failure-hints.test.mjs`（6 件）。変異 9 つ（草花をどけない、穴・上の固いブロックの案内、チェストの数、預ける物がない、時間切れの見立て、`No path` にまで添える、次のマス、道具 `plant` が草花を断る）が落ちることを確かめた。

## 6. 未確認
実機（再起動後）。
