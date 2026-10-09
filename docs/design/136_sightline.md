# 136. 目から先への視線: 小麦・草の「選択枠」をさえぎる物として数える

2026-10-09、user: 「小麦が邪魔して水救えないらしいです。視線の先のブロックの情報とかって渡せないんでしたっけ？レイトレ的なことできない？」→「進めてください」

## 1. 問題
- 畑のバケツ（`fill the bucket with water`、`use_item {item: bucket}`）が `nothing changed after using bucket at water 44,74,-478` で 18 分以上失敗した（`logs/bridge.log` 04:21〜04:39）。ボットは (46,74,-476)、水は (44,74,-478)、間の (45,74,-477) は畑のマス
- 視線の情報はすでに渡していた（`inspect` の `looking_at` = `bot.blockAtCursor(5)`、`inspect {x,y,z}` の `in_the_way` = `bot.world.raycast`）。ただし Mineflayer のレイは「当たり判定」にしか当たらない。小麦・草・苗木・松明は当たり判定が空（`minecraft-data`: 小麦は `boundingBox: empty`、形状 0 件）なので素通りし、「水が見える」と言う。サーバーのバケツは `ClipContext.Block.OUTLINE`（選択枠）でレイを飛ばす（Paper 1.21.4 の `Item.getPlayerPOVHitResult` を逆アセンブルして確認）ので、小麦に当たって何も起きない
- 失敗の文は何が邪魔かを言わず、LLM は「水に向けて狙え」しか読めなかった

## 2. したこと（`minecraft-bridge/src/sightline.mjs`）
- **枠の表**（`outlineOf`）: Paper 1.21.4 の `CropBlock`・`CarrotBlock`・`PotatoBlock`・`BeetrootBlock` の `SHAPE_BY_AGE`（小麦 2〜16 px の 8 段、にんじん・じゃがいも 2〜9 px、ビートルート 2〜8 px。成長段階 `age` で決まる）、`short_grass`・`fern`・`dead_bush`（2,0,2〜14,13,14）、`*_sapling`（12）、花（5,0,5〜11,10,11）、`torch`（6,0,6〜10,10,10）、`sugar_cane`（2,0,2〜14,16,14）。表にない通り抜けられるブロック（空気・水・溶岩以外）は 1 マスまるごと当たるものとして扱う（広めに言う側に倒す）
- **`firstBlockBetween(bot, from, to, ignore)`**: prismarine-world の `RaycastIterator` で線上のマスを順に見て、当たり判定、なければ枠に当たるかを数える。狙うマスに着くまでに何もなければ null。自分の立つマスは数えない
- **`lookingAt(bot, dist)`**: 正面のレイ。`inspect {}` の `looking_at` が使う（今まで `blockAtCursor`）
- **`blockedNote(bot, pos, aim?)`**: 失敗の文に足す一言。`(the wheat (age 7) at 45,74,-477 is in the line between your eyes and 44,74,-478: the server aims at its outline, so you hit it instead; goto 45,74,-478 (a cell beside it where the line is clear), then use the bucket at 44,74,-478 again)`。`standSpotsBeside` が出す、足を置いて立てる横のマス（足元が固く、足と頭が通れる。水のマスが地面の高さの穴なら、横の地面の上）のうち、そこの目から線が通る最初のもの（ボットに近い順）を勧める
- `inspect {x,y,z}` の `in_the_way`: 同じ判定（`{block, x, y, z}`、作物は `age` も）
- `cellsBeside`: `place_water` の立つマスの選び方（4 つの横の地面、上 2 マスが通れる、近い順。その 1 つ上に立つ）を共有に出した。失敗の文の「立つマス」は別の `standSpotsBeside`（足を置くマスそのもの）

## 3.5. 実機で分かったこと（2026-10-09 15:13、再起動後）
- 小麦が原因だった: 畑 2 の水 (44,77,-462) にバケツを使うと `the wheat (age 5) at 44,78,-463 is in the line …`。文は実機で出る
- 初版の勧め方は間違っていた: 「stand on 44,77,-463」と地面のブロックの座標を言い、LLM は（1）そのマスにバケツを使い、（2）`goto` が `did not reach … (1 m away horizontally, height +1)` で 3 回失敗した。足を置くマス（地面の 1 つ上）で言い、「goto してから使い直す」と書くように直した

## 3. 使い方を「失敗のあとに説明する」にした理由
押す前に線を引いて断る作りにはしていない。枠の表は手で書いたもので、実機で確かめていない。押す前に断ると、表が 1 つ間違っているだけで動いていた手が失敗に変わる。押したあと、何も起きなかった（`nothing changed`、`the bucket was not filled`、`the water was not placed`）ときだけ線を引いて、理由を足す。表が間違っていても、情報が増えるだけで、成功を減らさない。
- 対象: `use_item` の水や空気を狙った押し方（固いブロックへの `activateBlock` は対象外）、`place_water` の置けなかった失敗。`place_water` の `[water]` ログの「視線の先」も `lookingAt` に

## 4. 見送り（user に決めてもらう）
- **邪魔があるときに、横のマスへ歩いてもう一度押す**: 今は文で勧めるだけ。バケツが実際に満たされるには、LLM がその手を打つか、ブリッジが `use_item` の中で行って 1 回やり直す必要がある（小さい）。この設計書では実装していない
- 小麦を壊して通す手（収穫を捨てるので最後の手）
- `door-report.mjs` の `looking_at`（診断記録のみ。`blockAtCursor` のまま）、`observe.mjs` の `canSee`（モブの視線は当たり判定で決まるので、今のレイが正しい）
- 枠の表にないブロック（背の高い草、花の一部、レール、看板、ボタンなど）は 1 マス全体として言う

## 5. 未確認
実機で、バケツが小麦に当たって失敗していたか。確かめ方: 畑の (45,74,-477) を RCON `setblock 45 74 -477 air` で空にして同じ手を試し、通れば原因は小麦。通らなければ別の原因で、`failure` の文の `in_the_way` が空なら、ブリッジの `[water]` と `inspect` で見直す。

## 6. テスト
`test/sightline.test.mjs`（12）: 熟した小麦が間にあれば名指し＋横に立つマス、低い小麦（植えたばかり）は通る、水の隣に立てば通る（立つマスの小麦は数えない）、石はこれまでどおり、表にないブロックは 1 マス、空気と水は通る、枠の高さの表、`lookingAt`、`inspect` の `in_the_way`・`looking_at`、`use_item` の文（邪魔があるとき・ないとき）。表を外す・`use_item` の一言を外すとそれぞれ落ちることを確認した。

## 7. 追記（2026-10-09 15:50 ごろ、再起動後の実機）: `place_water` の失敗の原因と直し
- 畑 2 で `place water` が 2 回、`the water ran onto the farm`（06:46、06:53）。ログの `[water] … 視線の先 wheat …` が、バケツが小麦に当たったことを示した（3 回目は `dirt 52,76,-456` で成功）
- 原因が 2 つあった: (1) 立つマスを近い順に選んでいて、そのマスの小麦（足のマスの熟した小麦。目はその上にあるが、線は横へ降りるまで小麦の中を通る）を見ていない。(2) `firstBlockBetween` が目のあるマスを数えていなかった（Mineflayer の `raycast` が最初のマスを数えないのをそのまま写した）
- 直し: `firstBlockBetween` は目のあるマスも数える。`place_water` は、立つマスを「線がさえぎられないマス」から選び（なければ近い順）、押す前に、さえぎるのが刈ってよい物（草・花・作物）なら 3 回まで刈る。作物は畑の手があとで植え直す（刈った作物は収穫になる）
- 汲む側（`use_item`）は、失敗したあとに説明するだけのまま
- テスト: `test/place-water.test.mjs`（2）、`test/sightline.test.mjs` に目のあるマスのテスト（畑 2 の形）
