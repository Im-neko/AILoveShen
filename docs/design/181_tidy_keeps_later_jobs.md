# 181. 夜明かしの小目標でも、大目標の残りの中目標が集める物は預けない／find_blocks は洞窟の鉱石を出さない

2026-10-16、user: 「10分以上同じ状態で詰まってたら調査・修正してください。勝手にどんどんやっていいです。」

## 1. 起きたこと（`logs/stream.log`、13:30〜16:18）

- 大目標 m61「鉄の兜と鉄のレギンス」が 14:09 から約 2 時間、`have(raw_iron, 7)` から進まなかった（455 手、うち 124 手が失敗。胴と兜は済んだ）
- 手を分けると、`goto` 138・`dig` 91・チェストから出す 21・預ける約 20。夜（`through_night`）と帰宅（`at_home`）の小目標に変わるたびに、持っていた生の鉄 1〜2 個を「要らない物」として預け、次の鉄の小目標でまた出していた（14:18, 14:56, 15:14, 15:24, 15:35, 15:46, 16:13 の預ける手に `raw_iron` が入っている）
- 失敗の最多は `will not dig iron_ore … ore this deep is taken only where it shows in the wall of a mine`（洞窟の鉱石。windfall・候補・`search_memory` は除いているが、道具の `find_blocks` は除いていなかった）。LLM は `find_blocks` に出た座標を `dig` しては断られた

## 2. 原因

1. `tidyUp`（`candidates.mjs`）が残す物は `goalNeeds`＝今の小目標の材料だけ。小目標が `through_night` / `at_home` のとき材料は空で、大目標の残りの中目標（`goal.also`。Python の `_also` が全部の小目標に付けて送る）が集める生の鉄も「要らない」になった
2. `find_blocks` が `caveOre` を通していなかった（`dig` と同じ規則を掛け忘れた）

## 3. 変更

- `alsoReserve(state)`: `goal.also` の各品物（仲間を含む）を、その個数まで預けない物として数える。`tidyUp` の預ける数は `持っている数 − max(預けずに残す数, also の個数) − 余った種`。個数までなので、超えた分は預けて持ち物が埋まらない。`stashWhenNothingHeals`（回復できないときの全部預け）は、死んだときの損を減らすのが目的なので変えない
- `find_blocks`: 生きているブロックも地図から補う分も `caveOre` を除く。全部が洞窟の鉱石で何も残らないときは、`no iron_ore you may take within Nm: K lie deep in caves.` と `CAVE_ORE_REMEDY`（採掘場を掘る手を使う）を返す

## 4. 確かめたこと・確かめること

- ブリッジのテスト: `tidy.test.mjs`（also の個数まで預けない）、`tools.test.mjs`（find_blocks が洞窟の鉱石を出さない・理由を返す・浅い露頭は出す）。`npm test` 1088 件通過
- 実機は未確認: 夜をまたいで生の鉄が持ち物に残ること、`find_blocks` 後の `dig` の断りが減ること
- 直っていない: 生の鉄の供給そのもの（採掘場の壁に出る鉄が少ない）。地表の鉄鉱石（北西 5 個）へ夜明けに行くと言いながら、採掘場へ戻る決め方
