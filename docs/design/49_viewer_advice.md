# 設計書 49: 視聴者の助言を流さない、ブロックの状態を渡す

状態: 実装（2026-09-28）。実機では未確認

## 1. きっかけ

ユーザー（2026-09-28）:
- 「配信のコメントで指摘が合ってもその時実行中の目標に沿わない場合全て今別のことをしてるからと言ったり後でやると言って無視してしまいます」
- 「LLM が自分で判断して能力を追加するのって難しいですかね？畑には水が必要 → 水を汲む・置くスキルの開発・追加ができると良い」。気付くきっかけは配信のコメントでよい。「湿り具合の値等もブロックの情報として渡せるようにしたい」

## 2. 記録で見た原因（`data/logs/ailoveshen-dev.log`）

返答で頼み・助言を引き受けようとして（accept）断られたのは 26 回。うち 23 回はコードの視聴者の上限だった:
- 13 回「ねこかも already has a request」（同じ人の頼みは 1 つまで）
- 5 回「list already has 2 viewers' requests」
- ほかは別の視聴者の同じ上限

上限で断られると、返答は書き直しになり、「今は○○中だから後で」の none / decline になった。約束の確かめ（設計書 34 §10）は、その返答を cannot として話した。何も残らなかった。

ほかに分かったこと:
- 返事の前の仕分け（`CommentTriage`、request / advice / ...）の種類は、返答の生成に渡っていなかった
- 返答の決まりの「大目標に関係ない頼みは、断るか…」が、効率を先にする決まり（2026-09-28）と食い違っていた
- 引き受けた後に消えたのは 1 件だけ（視聴者の中目標の 80 手の予算）

つまり主な原因は「上限」と「流す返答を許していたこと」。「今の目標に沿わない」とモデルが判断したことではない。

## 3. 直し方

### 3.1 助言は配信者自身の中目標（`MidGoal.suggested_by`）

- 仕分けが advice のコメントを引き受けると、配信者の中目標になり、提案者を `suggested_by` に残す（`requested_by` は空）
- 視聴者の頼みの上限（1 人 1 つ、同時に 2 人）にも、予算（80 手）にも数えない。リストの長さの上限（6）は同じ
- 頼み（request）は今までどおり `requested_by` で、上限も予算もある
- 表示: 盤面・オーバーレイは「〜さんの提案」（vtuber は 💡）、`activity()` も（提案）。`Narrator` は頼みと同じく、足されたときに言う
- `mission.json` に保存する

### 3.2 上限は設計より先に確かめる（`MidGoalPlan.check_room`）

`MidGoalKeeper.accept` は、建物や施設を設計する前に `check_room` を呼ぶ。上限で断られるとき、設計した建物や施設だけが残ることはない。

### 3.3 流す返答を差し戻す（`generate_response.unanswered`）

仕分けの種類（`ChatResponder` → `GenerateResponseRequest.kind`。Jev が答えたときだけ）が request か advice のとき:
- none で、rethink も lesson もない → 差し戻す（accept の when=next / now、rethink、lesson、理由つきの decline のどれかにする）
- decline で `decline_reason` がない → 差し戻す。`decline_reason` は cannot / wrong / unsafe / off_mission / limit の enum（`DeclineReason`）。「今は別のことをしている」は理由にならない
- 助言を off_mission で断る → 差し戻す
- この 2 種類は 3 回まで試す。最後の 1 回は差し戻さずに話す（黙るよりよい）
- 種類が分からない（仕分けが off、Jev が答えない、台本）ときは、今までどおり確かめない

### 3.4 返答の決まり（`PLAN_TEMPLATE`）

- 大目標に直接関係なくても、あとの作業を速く・安全にするもの（道具、畑、植林、明かり、貯え）は引き受ける
- 今の小目標・中目標に沿わないことは、断る理由にならない。「後でやるね」と言うなら accept の when=next
- 助言を採ると自分の中目標になり、視聴者の上限に数えない
- decline_reason の意味

## 4. ブロックの状態（props）

- 技の `t.block_at` と道具 `find_blocks` は、ブロックの状態を `props` で返す（`blockProps` / `blockInfo`、`observe.mjs`）。値は Mineflayer（prismarine-block）のとおり文字列（`moisture: "7"`、`age: "3"`）。カスタム条件の `w.block` は前から返していた
- 湿っていない耕地（`isDry`: `moisture` < 7）の上の作物の数を `dry` として、畑の状態（`/observe` の `farm`、`cropFarms`）と施設の中身（`GET /facilities`）に添える。`farmed` の目標の行にも 1 行添える
- `dry` は知らせるだけで、目標の判定（met / blocked / impossible / dead_end）には使わない。水を引くかどうかは Gemini が決める
- `activity()` の畑の行: 「乾いた耕地 N」と、あるときだけ「乾いた耕地は水が 4 マス以内になく、育ちが遅い」
- 技を書く決まり・カスタム条件を書く決まり・`find_blocks` の説明に、props の読み方を 1 行ずつ足した
- やり方の帳に `water_farm`（バケツを作る、`use_item` で汲む・置く、4 マス、2x2 の無限水源）を足した

## 5. できないこと・確かめていないこと

- 技の `expects` と完了条件には、水を引いたことを書く述語がない。書くなら `custom(name)`（判定を自分で書く）
- `use_item` でバケツに水を汲む・置くことは、実機で試していない（`lava_bucket` と火の物だけを断る）
- 視聴者の言うことが正しいかは確かめない。確かめられるときは decline wrong で理由を言わせるだけ

## 6. 確かめ方

`examples/comments_advice.json` を台本にして `examples/integration_test_minecraft.py --comments examples/comments_advice.json` を回す。各コメントの `pass` が合否の基準。返事のあとに、足された中目標（頼み / 提案 / 自分）と考え直しが表示される。台本の `kind` は仕分けの答えの代わり。
