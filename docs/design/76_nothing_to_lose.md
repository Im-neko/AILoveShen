# 76 回復の手段がないときは、持ち物を預けて外で動く

## 1. きっかけ（2026-10-01）

user: 「空腹になってしまった際、他の行動を取れなくなり、家から出られなくなっているようです。これ持ち物がなければデス時のリスクはほぼ 0 だと捉えられるようにしたい。例えばチェストに持ち物は預けて、必要最低限の持ち物にして外で活動することも許容したい。必要であれば、デス覚悟で活動できるようにしたい」

ログ（07:31〜08:26）で見えたこと:

- 空腹ではなかった。体力 1.5、満腹度 15、食料 0。自然回復は満腹度 18 以上でしか働かないので、体力は 1.5 のまま動かない（`needs` は「health critical: it heals only while food is 18 or more」だけを出していた）
- 昼に家から出られない仕組みはない（`shelterOf` は夜か、ドアの前に敵がいるときだけ。`go home` の候補は危険があるときだけ。Python に体力で止める規則はない）。実際、外で掘り、牛やラマを倒している
- 止まって見えた原因は別: 食料の当てがない（`explore` が 4 方向 + 原木）ので Jev の確率が分かれて 0.5 を超えず、Gemini が回り、`eat pumpkin` `eat wheat` や材料のない `craft` を繰り返して小目標が stuck になる。この設計はその原因には触れない（user の依頼は、死のコストを下げて外で動けるようにすること）
- 死ねば体力・満腹度は満タンに戻る。持ち物を預けてあれば、家で動かずにいるより死んだほうが得

## 2. 決めたこと（user の答え）

- 判断はコードが自動で（LLM に選ばせると、今回の詰みを繰り返す）
- 持って出るのは、武器・防具・松明と、今の目標が使う物（目標の材料の木まで）だけ。道具・種・棒・拾い物は預ける
- 意図的に死ぬこと（RCON の `/kill`）はしない。夜の `through_night` も、そのまま（夜は家で過ごす）

## 3. 仕組み

`healing.mjs`: `cannotHeal(bot)` = 体力 ≤ 8 (`HEALTH_CRITICAL`) かつ満腹度 < 18 (`REGEN_FOOD`) かつ食べ物（腐った肉などを除く）を持っていない。

- 候補 `put everything but the weapon, armor, torches and what the goal uses in the home's chest`（`stashWhenNothingHeals`, `candidates.mjs`、動詞 `stash`、`urgent: true`）: 家の中で、チェストが分かっていて、`cannotHeal` のとき。預ける物は `KEEP_WHEN_FIGHTING` と目標が使う物（`goalNeeds`: `goalKeep` と `materialsOf` の木。`tidyUp` と同じ）以外すべて（`tidyUp` の `TIDY_KEEP` は使わない: 種・棒・道具こそ失ってよい物）。預けたあとは残りがないので出ない。目標が変わって取り出した物は、その目標の材料なので、また預けない
- Python の `StepPicker` が、`urgent` の `stash` を Jev に聞かずに選ぶ（`eat` / `dig_in` と同じ。失敗した直後は LLM が見る）。夜の戦いの前の `stash`（`urgent` なし）は、これまでどおり目標の手順
- 預けられなかった（いっぱい、など）なら `state.tidyBlockedUntil`（5 分）で出さない（`stash` の primitive が付ける。`tidy` と共有）
- `needs()` の体力の行に、回復の手段がないことと、死んで失う物の数（武器・防具・松明を除く）を数だけで足す: `health critical (2/20): it heals only while food is 18 or more (food 15); you carry no food. A death drops only what you carry: 19 items besides weapons, armor and torches`（何をすべきかは書かない: `needs()` の方針）

## 4. 弱いところ

- 狩りに出ても、満腹度 18 以上になるまで体力は戻らない。食料を取れるまでの間は、体力 1.5 のまま動く（死ぬと戻る）
- 預けたあとで目標が変わり、預けた物が要る目標になると、`withdraw` の往復ができる（解決器がチェストから取る）
- 食料の当てがないときの `explore` の割れ方と、Gemini が食べられない物を `eat` しようとする点は、この設計では直していない
- 実機では未確認

## 5. 預けては出す輪（2026-10-03、実機の記録）

体力 2・満腹度 10・食べ物なしで家にいて、目標が `have(stone_hoe) + have(food, 2) + …` のとき、15 分以上 `withdraw`（丸石・棒）→ `stash`（戻す）→ `craft` が材料なしで断られる、を繰り返した（`stash` は `urgent` で Jev に聞かないので、LLM の次の手の前に必ず割り込む）。原因は 2 つ。

1. `stashWhenNothingHeals` は、チェストから出して 5 分以内の物を見ていなかった（`tidyUp` は `recentlyTaken` で見ていた）。見るようにした
2. 「目標が使う物」(`goalNeeds` ← `materialsOf`) が、石の道具で `[品物だけ]` になっていた。材料の木は持ち物ゼロで解くが、家があると地下の石は採掘場・階段掘りで、松明 8 本と剣が先に要る。松明は石炭、石炭は地下の鉱石で輪になり「不可能」になる。不可能は木ごと空にするので、丸石も棒も出なかった。木のクワやチェストは地下を通らず、正しく出ていた（`/check {materials: true}` で確認）。`materialsOf` は持ち物のほか、周りのブロック・モブ・記憶・家・採掘場も空にして解く（`test/materials_stone.test.mjs`）

設計 67 の `plan_lint.unneeded_materials` と `tidyUp` も同じ木を使うので、石系の目標で効いていた可能性がある。

