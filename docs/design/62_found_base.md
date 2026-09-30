# 62. 見つけた拠点は仮の宿: 自分で置いた家具だけを数える（2026-09-30）

ユーザー（2026-09-30）: 「勝手にそのへんにあった家を家だと思って目標達成にしちゃうのどうしよう。ワールドから目標達成可否を判定するようにした弊害だと思う」→ 「仮の宿判定はアリです。進めてほしい」。

## 何が起きていたか

- `adoptFoundBase`（`landmarks.mjs`）は、家がないとき近くのドアの奥の屋根つきの閉じた部屋を `state.home`（名前「見つけた拠点」）にする。村の小屋もこれで家になる
- 2026-09-30 13:46 のログ: `大目標を達成した: 夜に寝られるようにする (placed(bed, home))`。ボットはベッドを置いておらず、小屋にもとからあった村人のベッドで満たした。直後に街の準備（`surveyed(9)`）の大目標が始まった
- 判定を世界から取ること自体は間違いではない。足りなかったのは「自分で置いた」という来歴: `placed(bed, home)` は家の中にベッドがあるかだけを見ていた
- 保存した家に `found` の印がなく（`homeToJSON` / `homeFromJSON` が落としていた）、再起動すると見つけた拠点と自分で建てた家の区別が名前だけになっていた

## 変更

- 見つけた拠点（`home.found`）では、`placed(bed | 色つきのベッド | crafting_table | furnace | chest, home)` を**自分で置いた家具だけ**で数える（`furniture.mjs` `ownFurnitureInHome`）。自分で建てた家（`found` なし）は、これまでどおり置いてある物すべて。拾って置き直す候補（`take_placed`）の相手も、見つけた拠点では自分で置いた物だけ
- 記録（`ledger.mjs`）: `blockPlaced` は原木に加えて家具（ベッド、作業台、かまど、チェスト。`FURNITURE_BLOCK` = `furniture.mjs` の `MOVABLE`）も `state.placed` に入れる。ベッドは 2 マスで記録は足側だけなので、頭側は横のマスの記録を見る（`furniturePlacedByMe`）。置いた後に別のブロックに変わっていれば数えない
- 保存（`home.mjs`）: `found` を保存する。印のない古い保存は名前（`FOUND_BASE_NAME`）で見分ける
- LLM に見せる家の行（`stream_context._format_home`、`observation.home.found`）: 「見つけた拠点（もとからあった建物を仮の宿にしている。自分で建てた家ではない）、もとからあるベッド（自分の物ではない）」
- 夜を越す・家に入る・反射・チェスト・畑などの振る舞いは変えていない（仮の宿としては今までどおり使う）。`at_home` / `through_night` は見つけた拠点でも済む

## 変えていないもの（判断待ち）

- `stored` / `planted` / `farmed` / `lit` / `leveled(home)` / `surveyed` は、見つけた拠点でも家があるものとして使える。村の松明や畑を数えてしまう余地がある（`lit` は家のまわりの松明を世界から数える）
- 街の準備（`TownPlanner`）は、見つけた拠点でも家があれば始まる（候補地は「最初の夜を越す小屋」を中心に決める設計）
- 見つけた拠点に自分の家具を置いたら自分の家にする（引っ越し）、は入れていない。家として扱うのは、自分で建てた家（`settleHome`）だけ

## テスト

`minecraft-bridge/test/found-base.test.mjs`（村人のベッドで満たさない、自分で置いたベッドは頭側からでも数える、置いた後に変わった物は数えない、自分で建てた家は従来どおり、家具を記録する）、`test/home.test.mjs`（`found` の保存と古い保存）、`tests/unit/infrastructure/adapters/prompts/test_game_prompt_template_builder.py`（家の行）。実機は未確認（ブリッジの再起動が要る。今の `state.json` の家は印がないが、名前で見つけた拠点と分かる）。
