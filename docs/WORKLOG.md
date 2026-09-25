## Current Status

**Active Phase**: つながった意思決定（`docs/design/12_coherent_decisions.md`）を実装し、実際の Gemini で確認した。次は Gemini + Jev での通しの自律実行（frun2: F の修正と 12 を合わせて検証）。ブランチ `feat/town-m1-m2`（`feat/phase6-minimal` から派生、PR #19 は未マージ）
**Last Updated**: 2026-09-25
**Test Status**: 331 unit tests passing (`pytest tests/`)、ブリッジ `npm test` 25 件成功

---

## Completed Work

### つながった意思決定: 言うこととやることを一致させる (2026-09-25)

**Commits**: `9a966d1`（設計書 12）、`2aa9f3d`（実装）、`26151d3`（実機確認で見つかった失敗の修正）。確認用スクリプト: `spikes/coherence_live.py`

- ユーザーの方針: 「コメントの指摘を反映させることより何より、全ての意思決定がつながっていることが大切。コメントに応答した事と全く違う事をしていたら視聴者はびっくりする」
- `PlaySession` だけが目標を持つ。`activity()`（目標と理由、進み具合、これまでの目標と終わった理由、視聴者の頼み）を `prompts/stream_context.py` の 1 つの書式で、目標の決定・実況・返答の 3 つが見る。目標の決定は会話も見る
- 返答と、その返答が約束する目標を 1 回の生成で出す（`reply_schema`、今出せる述語だけ）。目標は頼みとして `PlaySession` に置き、ステップのループだけが反映する（返答は `/act` と並行して走るため）
- `GoalEndedEvent` / `GoalSetEvent.requested_by` / `ViewerRequestRejectedEvent` / `ViewerRequestReplacedEvent`。`Narrator` が目標の終了と次の目標を 1 回で言い、約束した目標の開始は繰り返さず、やめた・断った・置き換えた頼みは必ず言う
- `GenerateResponseUseCase` を書き換えた（互換層なし）。`create_llm_service` / `create_game_service` は `Conversation` を受け取って共有する。`examples/integration_test_minecraft.py --comments` で台本のコメントを流せる
- 実機確認（家の中、昼 6 件 × 2 周 + 夜 2 件）: 1 回目は、頼みを黙って置き換えた、目標なしの先の約束 2 件、実況が生成中に変わった目標と食い違った、の失敗 → 修正後は目標なしの約束 0/14、「今なにしてるの？」3/3 一致、ベッドの頼みは次のステップで目標になった。遅延 1.1〜2.2 秒。詳細は設計書 12 §8
- 引き受けた頼みが保留中の「今なにしてるの？」に、やめる目標（剣の原木）を先に答えた（2/2）→ 頼みを今の目標として書くように直し 2/2 で一致。夜の「木を取ってきて」「探検して」は 4/4 で断った
- 未確認: `Narrator` の実際の発話（2 回目以降は発話の出る場面がなかった。単体テストのみ）、`ViewerRequestReplacedEvent` の実機（置き換えが起きなかった）、`examples/integration_test_minecraft.py --comments`（未実行。frun2 で初めて動く）。ゲーム開始前（家の設計中）のコメントは `session=None` で「ゲームはしていない」と書かれ、作り話になりうる
- 介入: `time set 3000 / 14000 / 1000`、tp

### F 修正: ドアの前の敵で閉じ込められない、窓の穴をなくす (2026-09-25)

**Commits**: `5fc8e9c`、`4a35e5c`（出口のドロップ）

- ユーザーの問い「ゴールを変えても行動が変わらないのはなぜ？」→ 安全の制約が Gemini の目標より下で外に出る候補を全部消し、その理由を返していなかった。Gemini にはドアの前の敵に対処する語彙もなかった。ユーザーの判断: 根本対処。素手の戦いも、壁に出口を掘るのも選択肢に残す。窓は塞ぐ
- 外した候補の件数と理由を目標の `blocked` に入れる（Gemini には「進められない理由」として見える）
- `cleared()`: 昼だけ、ドアの前の敵を外に出て倒す（素手でも。クリーパーは対象外）。夜はブリッジが理由つきで拒否する
- `exit_wall`: 敵から遠い側の壁を 2 ブロック掘って外に出て塞ぐ。掘ったブロックは `home.breach` に記録し、塞げなかった穴は欲求と「壁の穴を塞ぐ」候補になる
- 戦いは体力 8 以下でやめ、外にいれば「家に帰る」を出す
- `HouseBlueprint` から窓を削除した（ガラスがないので板で埋めるのと同じ。ガラスは精錬の対応後）
- 実機確認（介入: 窓の穴 3 つを RCON で板に、bot を家へ tp、`time set 1000`、ハスクを召喚、clear、回復）:
  - ハスク 2 体がドアの前にいる状態で `have(log)` を設定すると、`blocked` に理由が出て、4 方向の出口の候補が出た。出口から出て塞ぎ、原木を掘って達成した。壁に穴は残らない
  - 最初の実装は、掘ったブロックのドロップが家の中に落ち、外に出たあと拾いに戻って内側から壁を塞いだ（結果は ok と報告）→ 外に出る前に拾い、最後に外にいることを確かめるように直した
  - `cleared` で素手の攻撃候補が出て、ドアの前に敵がいてもドアから出て戦った。素手では 10 秒でハスクを倒せず、体力 20 → 11.8、その後は反射が逃げた
  - 夜に `cleared` を設定すると 400 と理由が返る
  - 未確認: 体力 8 での戦いの中断、中断された出口の修理、クリーパーがいるときの理由
- **Jev と Gemini の実際の選択**（実際の /observe を使い、ハスク 2 体、素手）:
  - `have(log, 1)`: 候補は出口 4 つと「wait inside」。Jev は 6/6 回「wait inside」（確信度 0.36〜0.51）
  - 停滞したあと Gemini に目標を聞くと、3/3 回 `cleared` を選んだ（`blocked` の理由が効いた）
  - `cleared`: 候補は攻撃 2 つ、出口 4 つ、「wait inside」。Jev は 6/6 回「wait inside」（確信度 0.15〜0.21）
  - → 仕組みは揃ったが、目的のない待機がある限り Jev は待つ（下の「待機に目的をつけた」で対処）
- **待機に目的をつけた**（ユーザー: 「戦いの一環として一時待機や退避はある」→ 目的のある待機だけ出す方向で試す）: 朝まで・回復・日光で燃えるのを待つ、の 3 つと、何もできないときの待機。表は設計書 11 §7
  - スパイク（NoAI のハスク 2 体、6 回ずつ）: 体力 20 では出口 6/6・攻撃 6/6、体力 16 では回復を待つ 6/6
  - 閉ループ（Jev が選び、ブリッジが実行。AI ありのハスク 2 体、体力 13.8 から）: 出口を選んだが、外に出る途中で反射に中断され、壁に穴が残った。その後、逃げる ×3、「壁の穴を塞ぐ」で修理、探索、原木を掘って 11 ステップで達成。中断された出口の修理を実機で確認できた
  - 気になった点: 候補が観測から実行までの間に消える（「flee ... is not available now」）。昼の日陰にスケルトンが残っていた
- `tools/goal-drive.mjs`: 素手で `cleared` の攻撃候補しかないと、逃げる候補を探して落ちた → 攻撃に戻す
- frun1 を止めたあと約 7 時間放置した bot が 2 回死んでいた（07:58 JST に Drowned、08:19 JST にスケルトン）。1 回目は窓の穴からの可能性があるが確かめていない

### F 実装: ゴールの述語・依存関係ソルバー・候補・プリミティブ (2026-09-25)

**Commits**: `c37ed59`（ソルバーと知識）、`d9a0e03`（ブリッジのゴール・候補・プリミティブ、actions.mjs を削除）、`71aa872`（Python 側の書き換え）、`e107e2e`（設計書 06・CLAUDE.md、食べるのは食料だけ）、`2443242`（待機は避難中だけ、夜の残りを分で、候補のログ）。設計: `docs/design/11_primitive_actions.md`

- Gemini はゴールを述語（`have` / `built` / `placed` / `at_home` / `through_night` / `explored`）で出し、ブリッジが世界から達成を判定する。手書きの複合アクションは互換層なしで削除した
- ブリッジ: `knowledge.mjs`（minecraft-data のレシピ・ドロップ、補正つき）、`solver.mjs`（台帳つき依存関係解決）、`goals.mjs`、`candidates.mjs`（具体的な候補と欲求、避難中は外に出る候補を外す）、`primitives.mjs`（14 個）
- Python: `PlaySession` がゴールの終わり（達成・行き詰まり・停滞・予算・時間帯の変化）を決める。Jev は候補から 1 つ選ぶ

**結果（frun1、Gemini + Jev、`logs/frun1.log`、`logs/frun1-night.log`）**
- 家（5x5x3、窓 3、柱あり）を step 103 で完成（実時間約 6 分、Jev 約 100 回）。その後 199 step まで実行して停止した
- 夜は家の中でドアを閉めたまま過ごし（time 11342〜23407 を毎分確認）、死亡なし
- 見つかった問題:
  1. **窓が穴**: `HouseBlueprint.blocks()` は窓の位置（目の高さ）を空けるだけ。夜に家の中で 4 回被弾した（step 145/167/173/178）。直後に窓のすぐ外にゾンビ・スケルトンがいた。ただし被弾ログの「(X nearby)」は最寄りの敵の名前で、攻撃者ではない（`index.mjs` の `onHurt`）ので断定はしていない
  2. **昼に閉じ込められる**: 朝になってもドアから 8m 以内にスケルトンがいて、候補が「wait inside」だけになった。step 176〜198（time 75〜5015）の間ずっと待ち、ゴール（木の剣 → 原木 3 → 原木 1）を変えても外に出られなかった。家の中から撃退する候補も、別の出口もない
  3. 昼でも wait が候補にあり Jev が 9 回続けて待った → `2443242` で修正（実行中のブリッジには未反映）
  4. through_night の remaining が 1 のままで停滞と判定された。Gemini は「朝まで残り約 1 分」と誤読した → `2443242` で修正（未反映）
- 介入（自律実行では使わない）: `time set 13000` ×2、テスト用の作業台とベッドの撤去、白いベッドの付与、`data/state.json` を `data/state.m1-house.json` に退避、`spreadplayers 400 -300`、clear・回復・満腹・`time set 1000`

### F スパイク: 根源的な行動を Jev に選ばせる (2026-09-25)

**Commits**: `7584ffe`（最初の計測）、`d9f1d96`（欲求の明示・優先順位の指示・後から足した場面）、`4bbcabf`（設計書 11）。コード: `spikes/primitive_choice_eval.py`、結果: `spikes/results/primitives/`

- 引数つきの候補 10 / 20 / 50 個から Jev に選ばせた。対象の選択（近い原木、羊と牛から羊など）は全問正解し、候補が 50 個でも崩れず、遅延は約 0.2 秒
- 目標を進める候補があると、空腹・低体力・夕方を無視した（空腹で食べる 0/9）。状態に欲求（数値と重さ、行動名なし）を書くと 9/9 になった。優先順位を指示文に書くと、遠くの敵から毎回逃げる過剰反応が出た（後から足した場面で 52%）
- 夜に家の中でベッドがないとき、欲求を明示しても外へ行く行動を 5/9 回選んだ → 安全に関わる判断は候補から外す（コード）
- ユーザーの指示「b を試して微妙なら a」に従い a（ソルバー・候補の列挙・安全の制約はコード、Jev は候補から選ぶ、欲求は状態に入れる）で設計した
- 率直な評価: この設計で Jev に残る判断は、規則も全問正解している。Jev の価値は実機で規則と比べて判断する
- 参考: `rmalde/minecraft-agent` も、候補の出し方と危険時の絞り込みはコードで、手書きの有界アクション（60 以上の場面別モジュール、シード・ルート固定、Peaceful）だった

### M1 生存基盤: 反射、剣、夜は家にこもる、食料、拠点の永続化、ベッド (2026-09-24〜25)

**Branch**: `feat/town-m1-m2`。**Commits**: `e146a2e`（M1 本体）、`c4f6f1a`（ベッド、浮く反射、待機中のカメラ、拾いの待ち、レシピ要求の間隔）。設計: `docs/design/09_town_building_roadmap.md`、`docs/design/10_agent_lifecycle.md`

**結果（m1run1）**: `time set 1000` から自律実行した。家を建て、夕方に go_home で帰宅し、時刻 13560〜23188 の間は家の中でドアが閉まっていた（RCON で毎分確認）。体力は 20 のままで、夜の間の死亡はなかった。朝に家を出て作業に戻った
- 実行を止めた後、放置中の bot が水中で溺死した（Mineflayer は止まっていると泳がない）。「死亡なし」は夜の間だけの結果。水中では経路探索していなければジャンプを押し続ける反射を足した
- それ以前の実行では、家まで追ってきたクリーパーが、朝ドアから出た瞬間に爆発して、ドアと家の角を壊した → ドアの近くに敵がいる間は家から出ない（`dangerOutside`）、敵を避ける経路コストを足した

**ベッド（c4f6f1a）**: `make_bed` Goal（hunt_sheep, craft_bed, place_bed）と、survive_night Goal の `sleep`
- 実機で確認: 作業台のクラフト → 設置 → ベッドのクラフト → 家に設置（`data/state.json` の `home.bed` と RCON で確認） → `time set 13000` で就寝 → 夜明け（サーバー時刻 24〜2429、実績 Sweet Dreams）
- `sleep` の戻り値は、起きた後の次の時刻更新で夜が明けたかを確かめる（サーバーは時刻を送る前に bot を起こすので、すぐ読むと夜の時刻のままだった）
- 自律実行で make_bed を通しで確認したことはまだない

**構成（主なもの）**:
- ブリッジ: `reflex.mjs`（近くの敵に先に戦う・逃げる。実行中のアクションは中断して止まるまで待つ。水中で浮く）、`home.mjs`（拠点、ドアの開け閉め、ベッドの位置、`data/state.json` への保存）、`actions.mjs`（`GoalAwayFrom` の逃走、剣、狩り、羊、ベッド、家に帰る・こもる）
- Python: `GoalType` に SURVIVE_NIGHT / GET_FOOD / MAKE_BED、`HouseProject.pursuable_goals`（実行できて未達成の Goal だけを Gemini に出す）、時間帯の変化で Goal を選び直す、`GameService.play`（完成後も続ける、ブリッジが反射中は待つ）

**根本原因と対処**:
- Paper の `recipe-spam-limit: 20`（1 tick に1回復）で作業台のクラフトが黙って失敗 → レシピ要求を 100ms 間隔にした
- ドロップには拾えるまでの遅延があり、狩りの直後に「食料が増えない」と失敗を返していた → `playerCollect` を待って拾う
- `GoalInvert(GoalFollow)` は敵が範囲いっぱい動くまで再計画しない → 独自の `GoalAwayFrom`
- 家の完成後に Goal が行ったり来たりした → 達成済みの Goal を候補から外した
- 家の中で待つ間、1秒ごとに向きを変えてカメラが回っていた（ユーザーの指摘） → ドアを向いて静止し、ときどき横を見る

**テストでの介入**（自律実行には使っていない）:
- 別の bot（`tools/flee-check.mjs`、`home-check.mjs`、`hunt-check.mjs`）と RCON での召喚・時刻変更
- ベッドのクラフト確認のため、白い羊毛3と板材12を give した。確認のために置いた作業台 (97,73,-83) は撤去した
- 就寝の確認で `time set 13000` を2回

**積み残し**: 設計書 06 は M1 の内容（反射、拠点、ベッド、浮く、レシピの間隔）にまだ合わせていない

### Phase 6 最小版: Gemini が設計・方針、Jev が行動選択、ブリッジが実行して家を建てる (2026-09-24)

**Branch**: `feat/phase6-minimal`（spike ブランチから派生）。**Commit**: `9ef81fc`。設計: `docs/design/06_phase6_jev_integration.md`（実装に合わせて書き直した）

**結果（run6）**: Gemini が「ひだまりシェンハウス」（5x5x3、南ドア、窓3、原木の柱）を設計し、47ステップで完成。bridge の `build.complete` だけでなく、RCON の `execute if block` で全 69 ブロック（屋根25、柱の原木12、ドア、窓の穴）を確認した
- 実行中の介入: なし（/give、テレポート、手動のアクション選択なし）
- 開始前のリセット: インベントリを空に、体力・満腹度の回復、`time set 1000`、run4 で bot が置いた作業台 (155,70,-21) の撤去。テスト用に召喚したゾンビ（tag=probe）は開始前に消した
- gamerule は既定のまま（doDaylightCycle / doMobSpawning true、keepInventory false、difficulty easy）
- 過去の検証で RCON で平らにした 11x11 の草地（104..114, 77, -80..-70）がワールドに残っている。run6 の建設地 (102,77,-32) はその範囲外

**構成**:
- Python（Clean Architecture）
  - domain: `GoalType`、`GOAL_ACTIONS`、`HouseBlueprint`（検証とブロック順）、`MaterialNeeds`、`GameObservation`、`HouseProject`（Goal の達成・選び直し判定）
  - application: `StartHouseProjectUseCase`（設計、検証エラーで最大3回やり直し）、`AdvanceHouseProjectUseCase`（観測 → Goal 判定 → Jev → 実行）
  - infrastructure: `JevActionSelector`、`MineflayerBridgeClient`、`GamePromptTemplateBuilder`、`GeminiTextGenerator.generate_json`、`JevSettings` / `MinecraftSettings`
  - presentation / factories: `GameService`、`factories/game.py`。実行は `examples/integration_test_minecraft.py`
- `minecraft-bridge/`（spike の `spikes/minecraft-mirror/` から移した Node サイドカー）: `index.mjs`（HTTP API）、`observe.mjs`、`actions.mjs`、`build.mjs`、`craft.mjs`、`mirror.mjs`

**失敗から特定した根本原因と対処**（run1〜run5）:
- Paper は use_item を 300ms に8件より多く受けると黙って捨てる → 設置を 200ms 間隔にした
- `pathfinder.stop()` を待機中に呼ぶと、次の `goto` がすぐ失敗する → `setGoal(null)` を使う
- mineflayer の `bot.craft` は、このサーバーではクリック予測がずれて材料を失う（原木5 → 板材4、原木0） → レシピ本（`craft_recipe_request`）+ シフトクリック + 再同期でクラフトする
- `craft_planks` が柱用の原木まで板材にしていた → 計画の原木ブロック分を残す
- `explore` と `pickup_drop` が失敗しても成功を返していた → 失敗は正直に返す（届かないドロップは以後除外）
- 樹冠に閉じ込められた: `GoalNear` だと、高い位置の原木には葉の上からしか「近く」にならず、`canDig=false` で降りられなかった → 原木へは `GoalLookAtBlock`（reach 4.5）で近づき、葉の上を歩くコストを上げ、歩行中に壊せるブロックを葉だけにした。葉を地面とみなしていたバグも直した
- アクションの打ち切りが `Promise.race` だけで、ループが裏で動き続けていた → `AbortSignal` で本当に止め、止まるまで待つ
- run5 で「slain by Zombie」: アクション中（最長20秒）は被弾に反応できなかった。葉の陰のゾンビは視線判定で脅威から外れていた → 被弾でアクションを中断する反射を入れ、4m 以内の敵は視線に関係なく脅威にした
- `explore` が3つの Goal に含まれていて、Goal の絞り込みがほぼ効いていなかった → `explore` は explore Goal だけに入れた。建設地探しに失敗した場所では `build_step` を出さない
- Goal プロンプトに「近くに切れる木があるか」と、失敗したアクションの理由を入れた

**カメラ（ミラー）**: 60Hz の位置送信、位置の補間、回転速度の上限（150°/s、掘削中は 600°/s）。「掘っている位置より上を見ている」という指摘について、ヘッドレスのクライアントで計測した（カメラの向きと、掘っているブロック中心への向きの差）。掘削中のずれは 0.0〜0.1° で、ずれていたのは掘り始めの約0.5秒（最大約50°）と、経路探索中に葉を壊すとき。後者の対処として掘削中の回転を速めた。指摘の場面は、樹冠の上から葉越しに下の原木を掘っていた状況だった可能性が高い（このときクロスヘアは手前の葉に当たる）。この状況は上記の修正でなくなった

**既知の制約**: 反射は「被弾したら中断」だけで、ステップの合間は無防備。素手では戦闘がほぼ成立しない。LLM・Jev・ブリッジの例外で実行が終わる（フォールバックなし）。Node 側の自動テストはない

**その他**: 環境の starlette 1.3.1 と gradio<1.0 の依存衝突は以前からのもの（今回の変更とは無関係）

### Spike: プロトコルミラー + Jev による行動選択 (2026-09-24)

**Branch**: `spike/minecraft-mirror-jev`。詳細と表は `spikes/README.md`

- `docker/docker-compose.minecraft.yml`: Paper 1.21.4、offline、127.0.0.1:25565、RCON 有効
- **ミラー**（`spikes/minecraft-mirror/mirror.mjs`）
  - 採掘のひび割れと腕振りを合成して送る。mineflayer には dig 開始イベントがないので、`targetDigBlock` を tick ごとに監視する。ヘッドレス確認では、ひび割れ17件、腕振り14件が届いた
  - bot の configuration パケットと play パケットを記録し、127.0.0.1:25578 の偽サーバーに生バイトのまま中継する
  - ヘッドレス確認: play まで到達し、チャンク、体力、20Hz の位置を受け取った。パースエラーはなかった
  - バニラ 1.21.4 クライアントで接続し、一人称視点と HUD（体力、満腹度、ホットバー）の描画を確認した
  - 視点のカクつきは目視評価待ち
  - 昼間のループで `flee_hostile` が2回選ばれて失敗した。敵の分類と逃げ先の経路を調べる必要がある
- **Jev**（`spikes/minecraft-mirror/bridge.mjs` + `spikes/jev_eval.py`）
  - typesafe-sdk 0.7.1 の `system_one` で、Choice を1問だけ投げる。`jev-1.13.0` で約 0.2s
  - 10シナリオ（state は各1つ）× 4条件（summary/raw × 手がかり付き/generic な説明）× 3回の結果
    - 同じ state への再問い合わせでは、ほぼ同じ答えが返る
    - 明らかな状況では全条件で妥当な選択になった
    - 要約と raw で選択が変わったケースはない。ただし正解の確率は、要約のほうがほぼ一貫して高い。raw はトークンが 4〜20 倍になる → 状態は要約して渡す
    - zombie_far_day は判断が割れた。説明文に距離を入れると flee、generic だと attack になった。説明文の書き方も判断を動かす
    - 初回の「raw だけ flee」は、前のシナリオのドロップが残った汚染による誤りだった。RESET を直して取り直した
  - generic な説明でも、state に応じて attack と flee を切り替えた。説明文ではなく state を読んでいる
  - confidence は、はっきりした状況で 0.8〜0.99、割れる状況で 0.1〜0.5 だった。LLM へ上げるかどうかの閾値として使える
  - クローズドループ: 昼 10/10 成功。夜にゾンビ2体を倒したが（エンティティが消えたことからの推定）、体力は 20 から 11.3 に減った。アクション実行中の被弾は Jev では防げないので、反射層をコードで持つ必要がある
  - バグ修正: 採掘の遅延。足元を掘った直後の空中で採掘を始めると、速度が 1/5 になっていた。着地を待ってから掘るようにした
- API キーは pass の `JEV_API_KEY` を、SDK が読む `TYPESAFE_API_KEY` として渡す

---

### Phase 3 実機確認: Gemini → TTS 読み上げ (2026-09-24)

**Issue**: #2（このエントリの PR マージでクローズ）

`examples/integration_test_llm.py --speak` で、Gemini 3.8 Flash が生成した3つの発話を、Style-Bert-VITS2（Docker、shen モデル）で最後まで読み上げられることを確認した。

**Changes**:
- `config/default.yaml`
  - `tts.voice.model_name` を `"default"` から `"shen"` に変更
  - `emotion_style_map` をすべて `"Neutral"` に変更。shen モデルは Neutral しか持たず、他のスタイルを指定するとサーバーが 422（`style=Happy not found`）を返す。感情表現は声ではなくアバター（Live2D）で行う方針
- `TTSService.wait_until_idle()` を追加（キューに積んだ発話の再生がすべて終わるまで待つ）
  - `_process_loop` は例外が起きても `task_done()` するようにした
  - `stop()` でキューを捨てた要素も `task_done()` するようにした
  - 以前のスクリプトは「キューが空 + 5秒」を待っていたため、10秒ある3つ目の発話が再生途中で切れていた
- `tests/unit/presentation/test_tts_service.py`（4件）

**計測値**（1〜2文の発話）: 生成 1.6〜2.0秒、合成 2.6〜3.3秒、生成開始から声が出るまで約4.2〜4.6秒

---

### Phase 3 最小版: Gemini LLM Integration (2026-09-24)

**Issue**: #2（実 API での確認が済むまでオープンのまま）

Gemini 3.8 Flash（google-genai SDK）で、実況と視聴者コメントへの返答を生成する。層別構成に合わせて実装した。

**Implemented Components**:
- Domain
  - `domain/value_objects.py`: `MessageRole`, `MessageType`, `ConversationMessage`, `CharacterProfile`, `GenerationContext`
  - `domain/entities.py`: `Conversation`（最新 max_history 件の会話履歴）
  - `domain/events.py`: `CommentaryGeneratedEvent`, `ChatResponseGeneratedEvent`
  - `domain/exceptions.py`: `TextGenerationError`
- Application
  - `ports/output/{text_generator,prompt_builder}.py`、`ports/input/{generate_commentary,generate_response}.py`
  - `dto/llm_dto.py`、`use_cases/{generate_commentary,generate_response}.py`
- Infrastructure
  - `adapters/gemini/gemini_text_generator.py`: `GeminiTextGenerator`
  - `adapters/prompts/prompt_template_builder.py`: `PromptTemplateBuilder`
  - `config.py`: `GeminiSettings` から `temperature`/`max_tokens` を削除し、`main_thinking_level`/`filter_thinking_level`/`max_output_tokens`/`retry`/`rate_limit` を追加。`CharacterSettings` を新設
- Presentation / Composition Root: `presentation/services/llm_service.py`、`factories/llm.py`
- `pyproject.toml`: `llm` extra（`google-genai>=2.25`）
- `config/default.yaml`: gemini セクションを更新し、`character` セクションを追加
- Tests: 67 件追加（計 223）
- Examples: `demo_phase3.py`（偽の生成器、キー不要）、`integration_test_llm.py [--speak]`（実 API、TTS 読み上げは任意）
- Docs: 設計書 03 を実装に合わせて全面改訂、04 のフィルタのコード例を google-genai 化、01 §6.1 は 03 への参照に置き換え、CLAUDE.md・README を更新

**Key Design Decisions**:
1. Gemini 固有の概念（thinking_level、SDK の型、ロール名）は `GeminiTextGenerator` に閉じ込めた。domain は `VIEWER`/`STREAMER` という配信の言葉で会話を表す
2. `generate_with_history` は作らない。履歴は文字列としてプロンプトに埋め込む（3.8 は prefill を推奨していないため）
3. プロンプトの文面はモデルに依存しないので `adapters/prompts/` に置いた
4. リトライは SDK の `HttpRetryOptions` を使う。ローカルの模擬サーバーで、503/429 は計3回、400 は1回で失敗することを確認した
5. `thinking_level` は low/medium/high のみ受け付ける。SDK は `minimal` を通すが 3.8 Flash は拒否するので、起動時に弾く
6. `max_output_tokens` には思考トークンが含まれ、上限に達すると出力が空になりうる。500 から 8192 に上げたが、**仮の値で未調整**
7. 実況と応答で `Conversation` を1つ共有する。応答生成に失敗しても、視聴者コメントの記録は残す
8. 依存方向のテストで、新しいファイルにも規則違反がないことを確認済み

**確認済み / 未確認**:
- 確認済み: ユニットテスト、`demo_phase3.py`、無効なキーでの実リクエスト（400 → `TextGenerationError`、即時失敗、レート制限の間隔）
- 確認済み: 模擬サーバーで実際のリクエスト本文を確認した（`systemInstruction`、`maxOutputTokens`、`thinkingConfig` あり。`temperature`/`topP`/`topK`/`tools` なし）。SDK は `thinkingConfig` の中を `thinking_level` と snake_case で送っている
- 確認済み（マージ後の 2026-09-24）: 実キーで `integration_test_llm.py` の3回の生成がすべて成功。snake_case の `thinking_level` も API に受理された。low と medium を比べて main=low に決定（下記「thinking_level の調整」）
- 未確認: `--speak` での TTS 連携（このマシンに Style-Bert-VITS2 のモデルがなく、Docker も停止しているため）

**Commit**: `2727c02` - feat: implement Phase 3 minimal LLM conversation with Gemini 3.8 Flash (#2)
**PR**: #16（base: #15）

---

### 層別構成への移行: Layer-first Clean Architecture (2026-09-24)

`core/` と `tts/` の「機能ごとに4層」構成をやめ、最上位を
`domain / application / infrastructure / presentation / factories` に分けた。
設計書 02〜07 はもともとこの構成を前提にしていたので、実装が設計書に揃った。

**AI 層の方針（決定事項）**: Flue（TypeScript のエージェントフレームワーク）は採用しない。
Python + google-genai で `ITextGenerator` ポートの内側に実装する。Flue は検討したが、
次の理由で見送った。
- 任せられるのは API 呼び出しと短期の会話履歴（要約圧縮）だけ。長期記憶・感情などのドメイン記憶は結局自前で作る
- Python ↔ Node の二重ランタイムとプロセス間通信が増える
- 最新版 2.1.1 が pi-ai `^0.83` に固定されており、`gemini-3.8-flash` を解決できない
Phase 3 の最小版を動かしたあとに、実際に困った点が Flue で解消するなら再検討する。

**移動先**:
- `core/domain/*`, `core/exceptions.py` → `domain/`（`DomainEvent` は `value_objects.py` から `events.py` へ移動）
- `tts/domain/value_objects.py` → `SpeechResult`/`SpeechStatus` は `domain/value_objects.py` へ、`VoiceConfig` は `infrastructure/adapters/tts/voice_config.py` へ
- `tts/domain/events.py` → `domain/events.py`
- `core/application/ports/output_ports.py` → `application/ports/output/event_publisher.py`
- `tts/application/*` → `application/{ports,use_cases,dto}/`
- `core/infrastructure/*` → `infrastructure/`
- `tts/infrastructure/adapters/*` → `infrastructure/adapters/{tts,audio}/`
- `tts/domain/services/emotion_style_service.py` → `infrastructure/adapters/tts/`
- `tts/presentation/*` → `presentation/services/`
- `tts/factory.py` → `factories/tts.py`
- テストも同じ構成に移動（`tests/unit/{domain,application,infrastructure/adapters/...}`）

**Key Design Decisions**:
1. エンジン固有の語彙は adapter に閉じ込める。スタイル名（"Happy" 等）への変換を `StyleBertVits2Client` 側へ移し、domain/application は `EmotionState` だけを扱う
   - `ISpeechSynthesizer.synthesize(text, emotion, ...)`、`SpeakTextRequest.emotion`、`SpeechRequest.emotion`、`SpeechStartedEvent.emotion`
   - 未使用だった `ISpeechSynthesizer.get_available_styles` をポートから削除（adapter には残す）
2. 依存の向きを `tests/unit/test_architecture.py` で検査する（AST で import を解析）
3. 設計書 03〜08 は `domain/value_objects/` をパッケージとして分割する前提だが、実装は単一ファイルのまま。各フェーズの実装時に判断する

**Files Changed**: `src/ailoveshen/**`, `tests/unit/**`, `examples/demo_phase{1,2}.py`, `examples/integration_test_tts.py`,
`CLAUDE.md`, `docs/design/00〜07`（構成・import パス・Composition Root の場所）

**Commit**: `f57d37c` - refactor: move to layer-first Clean Architecture
**PR**: #15（base: #14）

---

### Gemini モデル切り替え: → gemini-3.8-flash (2026-09-24)

`main_model` と `filter_model` の両方を `gemini-3.8-flash` に統一した（GA、2026-09-02 リリース）。

**Files Changed**:
- `src/ailoveshen/core/infrastructure/config.py` - `GeminiSettings` のデフォルト値と `.get()` のフォールバック値
- `config/default.yaml`, `tests/unit/infrastructure/test_config.py`
- `docs/design/00, 01, 03, 04, 06, 08` - モデルID・本文・図
- `README.md`, `CLAUDE.md`, `.serena/`

**Key Design Decisions**:
1. 同じモデルで 2 枠を使い、役割の差は `thinking_level` で付ける想定（filter=low, main=medium）。未実装
2. 旧設定では `config.py` のデフォルト値（`gemini-2.5-pro-preview-05-06`）と yaml（`gemini-2.5-pro`）が食い違っていたが、今回の統一で解消

**未解決（Phase 3 で対応）**:
- 3.8 Flash では `temperature` / `top_p` / `top_k` が廃止され、`thinking_level`（low/medium/high）に置き換わった。`GeminiSettings.temperature` と設計書 03/04 のコード例がまだ残っている
- 設計書のコード例は旧 SDK `google.generativeai` 前提。`google-genai`（`genai.Client`）への書き換えが必要
- Flue（TypeScript のエージェントフレームワーク）の採用を検討 → 見送り（層別構成への移行の項を参照）

**Commit**: `51e9176` - chore: switch Gemini models to gemini-3.8-flash
**PR**: #14

### Phase 6 設計改訂: NitroGen → Jev + Mineflayer (2026-09-20)

**Issue**: #4 (実装は未着手のためオープンのまま)

NitroGen (MineDojo) が設計のみで未実装のため、Phase 6 の Minecraft 統合設計を
TypeSafe AI の System One モデル **Jev** + **Mineflayer** ブリッジ構成へ改訂。

**役割分担**:
- Gemini 2.5 (System 2): 方向性 = `Goal`（13種の閉じた集合）を決定
- Jev (System 1): Goal 範囲内の高速・型付き判断
  - Reactive Loop (~600ms): 生存反射、LLM を介さない
  - Tactical Loop (~10s): Goal 内の次の一手の選択
- Mineflayer Bridge (Node.js サイドカー): 判断をゲーム操作へ変換、状態・イベントを双方へ還流

**Files Changed**:
- `docs/design/06_phase6_jev_integration.md` - 追加（全13章）
- `docs/design/06_phase6_nitrogen_integration.md` - 削除
- `docs/design/00_architecture_overview.md`, `01_phase1_core_infrastructure.md`,
  `08_phase8_orchestration.md` - 参照更新
- `README.md`, `CLAUDE.md` - プロジェクト概要更新
- `src/ailoveshen/core/infrastructure/config.py` - `NitroGenSettings` 削除

**Key Design Decisions**:
1. LLM は「何を目指すか(Goal)」、Jev は「今どう動くか」という System 2 / System 1 分担
2. Jev の型付き出力 (`Noul`/`Choice`/`Score`) により旧設計の `ACTION_KEYWORDS` による
   自由テキストのキーワードマッチを廃止
3. Mineflayer は Node.js 専用のため、Python 本体とは別プロセスのサイドカー構成とする
4. `MinecraftBridgeSettings` / `JevSettings` の追加は Phase 6 実装時に行う（設計書 11.1）

**Commit**: `2944add` - docs: replace NitroGen plan with Jev + Mineflayer bridge design (#4)
**PR**: #11

---

### Docker化: TTS Server Containerization (2026-01-09)

**Implemented Components**:
- Docker Configuration
  - `docker/Dockerfile.tts-server` - Style-Bert-VITS2 server image (python:3.10-slim, CPU inference)
  - `docker/docker-compose.yml` - Service definition with health checks and resource limits
  - `docker/config.yml` - TTS server configuration for CPU mode
  - `docker/.env.example` - Environment variable template
  - `docker/docker-compose.override.yml.windows` - Windows-specific volume path settings
  - `docker/README.md` - Quick reference guide
  - `.dockerignore` - Build optimization (excludes models, venv, etc.)
- Documentation
  - `docs/setup/docker.md` - Comprehensive Docker setup guide with architecture diagram
  - `docs/setup/obs-audio-routing.md` - OBS audio routing via virtual audio devices
- Integration Tests
  - Docker integration test: 4/4 tests passing
  - Voice synthesis verified with Docker container

**Key Design Decisions**:
1. Hybrid architecture: TTS server in Docker, client on host (Docker cannot access host audio devices)
2. Volume mounts for model files (bert/, model_assets/) - smaller image, easier model updates
3. CPU inference only (macOS/Windows Docker don't support GPU passthrough)
4. torch >= 2.6 required for CVE-2025-32434 security fix in transformers
5. pyopenjtalk dictionary downloaded during build (avoids runtime permission issues)
6. 120s health check start period for BERT model loading
7. VB-Audio Cable (Windows) / BlackHole (macOS) for OBS audio routing

**Resolved Issues**:
- pip install --index-url applying to all packages → Split into two RUN commands
- pyopenjtalk permission denied → Download dictionary during build before USER switch
- transformers CVE-2025-32434 → Upgrade to torch >= 2.6
- typing-extensions version conflict → Install typing-extensions>=4.10.0 before torch
- Missing numba module → Add numba to dependencies

**Commit**: `a857832` - feat: implement Phase 2 TTS pipeline and Docker containerization

---

### Phase 2: TTS Pipeline (2026-01-09)

**Issue**: #5 (リアルタイム音声合成パイプラインの実装)

**Implemented Components**:
- Domain Layer
  - `src/ailoveshen/tts/domain/value_objects.py` - SpeechResult, SpeechStatus, VoiceConfig
  - `src/ailoveshen/tts/domain/events.py` - SpeechStartedEvent, SpeechCompletedEvent, SpeechQueuedEvent
  - `src/ailoveshen/tts/domain/services/emotion_style_service.py` - EmotionStyleService
- Application Layer
  - `src/ailoveshen/tts/application/ports/input/speak_text.py` - ISpeakText interface
  - `src/ailoveshen/tts/application/ports/output/speech_synthesizer.py` - ISpeechSynthesizer interface
  - `src/ailoveshen/tts/application/ports/output/audio_player.py` - IAudioPlayer interface
  - `src/ailoveshen/tts/application/dto/speech_dto.py` - SpeakTextRequest, SpeakTextResponse
  - `src/ailoveshen/tts/application/use_cases/speak_text.py` - SpeakTextUseCase
- Infrastructure Layer
  - `src/ailoveshen/tts/infrastructure/adapters/tts/style_bert_vits2_client.py` - StyleBertVits2Client
  - `src/ailoveshen/tts/infrastructure/adapters/audio/sounddevice_player.py` - SounddevicePlayer
- Presentation Layer
  - `src/ailoveshen/tts/presentation/services/tts_service.py` - TTSService with priority queue
- Configuration
  - `config/default.yaml` - Extended TTS configuration
  - `src/ailoveshen/tts/factory.py` - Composition Root (create_tts_service)
- Exceptions
  - `src/ailoveshen/core/exceptions.py` - SynthesisError, AudioPlaybackError
- Tests
  - `tests/unit/tts/domain/` - 26 tests for value objects and services
  - `tests/unit/tts/application/` - 20 tests for DTOs and use cases
  - `tests/unit/tts/infrastructure/` - 11 tests for adapters
  - Total: 71 new tests (148 total passing)
- Demo
  - `examples/demo_phase2.py` - All TTS components verified working
- Integration Test
  - `examples/integration_test_tts.py` - End-to-end TTS with real server

**Key Design Decisions**:
1. Lazy imports for heavy dependencies (sounddevice, httpx) via `__getattr__`
2. Dataclass events with default field values for inheritance compatibility
3. Sync methods (stop, is_playing, get_duration_ms) vs async methods (play, synthesize)
4. Priority queue with sequence numbers for FIFO within same priority
5. Optional TTS dependencies via `pip install ailoveshen[tts]`

**Commit**: `a857832` - feat: implement Phase 2 TTS pipeline and Docker containerization

---

### Phase 1: Core Infrastructure (2026-01-09)

**Issue**: #8 (Closed)

**Implemented Components**:
- Domain Layer
  - `src/ailoveshen/core/domain/entities.py` - Entity, AggregateRoot base classes
  - `src/ailoveshen/core/domain/value_objects.py` - EmotionState, SpeechRequest, Position, Rotation, FilterResult, DomainEvent
- Application Layer
  - `src/ailoveshen/core/application/ports/output_ports.py` - IEventPublisher, IEventSubscriber interfaces
- Infrastructure Layer
  - `src/ailoveshen/core/infrastructure/config.py` - YAML config with env var expansion
  - `src/ailoveshen/core/infrastructure/logging.py` - Loguru structured logging
  - `src/ailoveshen/core/infrastructure/events.py` - AsyncEventBus (thread-safe Pub/Sub)
- Configuration Files
  - `config/default.yaml` - Default settings
  - `config/development.yaml` - Development overrides
- Tests
  - `tests/unit/domain/test_entities.py`
  - `tests/unit/domain/test_value_objects.py`
  - `tests/unit/infrastructure/test_config.py`
  - `tests/unit/infrastructure/test_events.py`
  - Total: 77 tests passing
- Demo
  - `examples/demo_phase1.py` - All components verified working

**Key Design Decisions**:
1. UTC timestamps for all datetime fields (distributed system compatibility)
2. Value Objects raise ValueError for invalid values (explicit over silent)
3. Thread safety with threading.Lock (sync) + asyncio.Lock (async)
4. diagnose flag controlled by debug parameter (security consideration)
5. TYPE_CHECKING import to avoid circular imports

**Commit**: `96f7eba` - feat: implement Phase 1 Core Infrastructure

---

## Next Steps

### 街作りロードマップの続き（09）

- **目標の階層（設計書 13、ユーザーの承認待ち → 実装）**: 大目標（設定、不変）・中目標（優先度つきのリスト、完了条件は判定できる述語、上限はコード）・小目標（今の Goal）。視聴者の頼みは小目標ではなく中目標の候補にし、今の小目標を打ち切らない。ブリッジに `POST /check`
- **frun2**: Gemini + Jev の通しの自律実行で、F の修正（`cleared`、壁の出口、目的つきの待機、窓なし）と 12（台本のコメント `--comments`）を合わせて検証。夜を被弾なしで越えるか、朝に自分で外へ出るか、頼みと実況が食い違わないか
- M2（Twitch）: 12 の返答の仕組みにコメントをつなぐ
- 被弾ログの「(X nearby)」は最寄りの敵の名前で攻撃者ではない。実際の攻撃者を出す
- F: Jev と規則（`tools/goal-drive.mjs`）の実機比較、Noul による続行・切り替え判断のスパイク、設計書 06 §10 に検証結果を反映
- スクショ（vision）と M3 は F の後
- M2（Twitch）: ユーザーのアプリ登録と `pass insert TWITCH_CLIENT_ID` 待ち
- スクショを Gemini に見せる（ミラーのクライアントのウィンドウ、後で OBS に差し替え可能に）
- 設計書 06 を M1 に合わせて更新

### Phase 6 の続き

- PR #19 のレビューとマージ（`feat/town-m1-m2` の PR をどこに向けるかは要相談）
- 例外で止めない方針（LLM・Jev の一時的な失敗）
- ゲームイベントを実況・TTS につなぐ（Phase 8）
- ブリッジ（Node）の自動テスト

### Phase 3 の積み残し

- **返答にゲーム状況を渡す**: `GenerateResponseRequest` には `game_state_summary` / `recent_events` がない。会話履歴が空だと、モデルが今やっていることを作り話で答える（計測中に「新しいお家を建てている」と答えた）。Phase 6/8 でゲーム状態を渡すときに追加する
- **声が出るまでの遅延**: 生成開始から声が出るまで約4.2〜4.6秒。内訳は生成 1.6〜2.0秒、合成 2.6〜3.3秒（Docker 内・CPU）で、合成のほうが遅い。改善案は、文ごとに分けて合成と再生を並行させる、Docker を使わずに GPU/MPS で推論する、など。Phase 8 で配信の間合いを見ながら判断する
- **`StyleBertVits2Client.get_available_styles()` の不具合**: サーバーの `/models/info` はモデル ID（`"0"`）をキーに返すが、クライアントはモデル名（`"shen"`）で引いているため、常に `["Neutral"]` を返す。現在は呼び出し元がないので実害はない

### thinking_level の調整（2026-09-24 実測）

同じプロンプトで各4回計測した結果:

| thinking_level | 実況（中央値） | 返答（中央値） | 思考トークン |
|---|---|---|---|
| low | 1.90s | 1.64s | 0〜93 |
| medium | 2.87s | 3.10s | 129〜405 |

品質に目立った差がないので、main=low に決めた。`max_output_tokens` は 8192 のまま（実測の合計は最大約1,300トークン）。

**Confirmed Specifications (2026-01-10, 2026-09-24 更新)**:
- Model: `gemini-3.8-flash`（2026-09-24 変更。main/filter とも同じモデル）
- SDK: `google-genai`（`genai.Client`）。旧 `google.generativeai` は使わない
- Generation params: `temperature`/`top_p`/`top_k` は 3.8 で廃止。`thinking_level` を枠ごとに設定（main=low, filter=low。実測にもとづき 2026-09-24 に決定）
- Authentication: API Key only (no OAuth2)
- Retry: 3 attempts including the original request, exponential backoff (base=1s, max=10s), 408/429/5xx only
- Fallback: None (no switch to another model on failure)
- Default response: None (return empty string on failure)
- Rate limit: Simple sleep (1 second interval)
- Token monitoring: Log output only (no alerts)
- GameState: Optional（Phase 6 まではテキスト要約で受け取る）

### 既知の課題（今回の作業で発見）

- **`AggregateRoot` の等価性と hash の不具合**: `@dataclass` の既定（eq=True）によって、ID ではなくフィールドで比較され、hash もできない（`unhashable type`）。サブクラスも同じ。`Conversation` は `eq=False` で回避したが、`AggregateRoot` 自体は未修正
- **TTS の設定経路のずれ**: `create_tts_service` は YAML の生の dict（voice/synthesis/queue/audio）を受け取るが、`Settings.tts`（`TTSSettings`）はその形になっていない。docstring にある `settings.get(...)` も存在しない。`integration_test_llm.py --speak` は YAML を直接読んで回避している
- **プロンプトインジェクション**: 視聴者コメントはそのままプロンプトに入る。Phase 4 のフィルタで対処する
- **設計書のパス**: 03〜08 には `domain/value_objects/` をパッケージとして分割する前提の import パスが残っている。実装は単一ファイル。各フェーズの実装時に判断する

### Phase 4: Twitch Integration (Ready for Implementation)

**Issue**: #1, #7

Refer to design document: `docs/design/04_phase4_twitch_integration.md`

**Confirmed Specifications (2026-01-10)**:
- OAuth: Browser auth flow for full permissions (BAN etc.), but initial impl uses Client Credentials
- Response rate limit: 5 seconds interval, no per-user limit
- Reconnection: Exponential backoff for server errors (infinite retry), no retry for client errors
- NG words: Delegate to Gemini Flash via prompt instructions
- Filter results: Log output + JSONL file (`data/logs/filter_results.jsonl`)

### Phase 5: MCP Server (Ready for Implementation)

**Issue**: #3

Refer to design document: `docs/design/05_phase5_mcp_server.md`

**Confirmed Specifications (2026-01-10)**:
- Short-term memory: 20 entries
- Long-term memory: Auto-save for importance >= HIGH
- Search: SQLite LIKE (vector search planned for future)
- Emotion decay: Applied when generating commentary
- VTube Studio: API v1.0, token file auth

### Phase 6: Jev + Minecraft Bridge Integration (Ready for Implementation)

**Issue**: #4

Refer to design document: `docs/design/06_phase6_jev_integration.md`

**Confirmed Specifications (2026-09-20 改訂)**:
- **Framework change**: NitroGen → Jev (TypeSafe AI System One) + Mineflayer
- Abstraction: `IGameEnvironment` interface for future games (Terraria, Factorio, etc.)
- Minecraft connection: External server (not managed by AILoveShen)
- Action timeout: 5 seconds
- Parallel actions: Enabled with queue management
- Node.js bridge: HTTP API or WebSocket
- Minecraft version: Java Edition v1.21.4

### Phase 7: OBS Integration (Ready for Implementation)

**Issue**: #6

Refer to design document: `docs/design/07_phase7_obs_integration.md`

**Confirmed Specifications (2026-01-10)**:
- OBS version: 28+ (WebSocket 5.x)
- Scene transition: Use OBS default settings
- Subtitle position/font: Configured in OBS (not AILoveShen)
- Subtitle default duration: Persistent (manual hide)

---

## How to Resume Work

1. Read this WORKLOG.md for current status
2. Check GitHub Issues for active tasks
3. Review design documents in `docs/design/`
4. Run tests to verify current state: `pytest tests/`
5. Run demo to verify functionality: `python examples/demo_phase1.py`

---

## Session Notes

### 2026-09-25 (目標の階層)
- ユーザーから「コメントの言いなりにはならないように。元の大目標から外れないように」→ 今は大丈夫ではない（頼みが小目標を直接置き換え、上限もない）。ユーザーの構想（大目標1・中目標複数を優先度順・小目標1）に沿って設計書 13 を書いた

### 2026-09-25 (つながった意思決定)
- ユーザーから「Gemini はベッドを作ることを考えないのか、コメントで指摘されたら検討できるか」→ 考えてはいた（frun1 で placed(bed) を選んだが Jev の待機で停滞）。コメントは目標に届かなかった。ユーザーの方針「全ての意思決定がつながっていることが大切」で 12 を設計・実装した。frun2 の順番は聞かれなかったので 12 を先にした

### 2026-09-25 (F の実装と自律実行 frun1)
- F を実装し、Gemini + Jev で家の建築から一晩越しまで自律実行した。窓の穴からの被弾と、朝に日陰のスケルトンで家に閉じ込められる問題が見つかった。以前 M1 で「夜を安全に越えた」としたのは、窓の横に敵が来なかっただけだった

### 2026-09-25 (M1 のベッドと、根源的な行動への方針転換)
- ベッドの設置と就寝を実機で確認し、就寝の成功判定を時刻で確かめるようにした
- ユーザーから「行動を毎回配信外で足すのが気になる。根源的な行動の組み合わせで実現できないか」。手書きアクションは①操作の仕組み ②手順 ③その場の選択が混ざっていて、②はレシピ・ドロップのデータから導ける。ただし Jev は計画役ではないので、組み合わせは達成条件＋依存関係の解決＋Gemini が担う、と整理した。スクショより先にこれを進める

### 2026-09-24 (Minecraft サーバー・ミラー・Jev spike)
- Minecraft サーバーを Docker で起動し、Mineflayer の bot で参加と移動を確認した
- bot として参加すると、カメラの動きや UI が配信向きではない。そこで、パケットを実クライアントに中継する方式（プロトコルミラー）を採用し、spike で検証した
- Jev に今実行できるアクションを列挙して選ばせる方式を、シナリオ評価とクローズドループで検証した
- 設計書 06 の書き換えは、実クライアントでの確認が済んでから行う

### 2026-09-24 (モデル切り替え・層別構成・Phase 3 最小版)
- Gemini を 3.8 Flash に統一した（PR #14）
- AI 層に Flue を使うか検討し、見送った。Python + google-genai をポートの内側に実装する方針にした
- 最上位を4層に分ける構成へ移行し、Style-Bert-VITS2 のスタイル名を adapter に閉じ込めた（PR #15）
- Phase 3 最小版を実装した（PR #16）。実キーがないため、実 API での確認は未実施
- #14 → #15 → #16 をこの順でマージした
- 実 API で確認し、thinking_level を low と medium で比較して main=low に決めた
- TTS を Docker で起動し、`--speak` で読み上げまで確認した。shen モデルは Neutral のみなので、感情表現はアバター側で行う

### 2026-09-20 (Phase 6 設計改訂)

- `jev-integration-design.patch` を適用し Phase 6 設計を Jev + Mineflayer 構成へ改訂
  - `CLAUDE.md` のみ改行コード不一致 (パッチは LF / 作業ツリーは CRLF) で自動適用に失敗したため、
    該当6行を CRLF 維持のまま手適用
- 未コミットだった Phase 2 TTS / Docker 化を先行コミット `a857832` として分離
- Phase 6 設計改訂を `2944add` としてコミット、PR #11 経由で main へマージ
- PR 本文の `Closes #4` で Issue #4 が自動クローズされたため再オープン
  (#4 は実装チケットであり、今回のマージは設計書のみ)
- NitroGen 残存を一掃: `config.py` の `NitroGenSettings` 削除、
  `.serena/` メモリ・`docs/WORKLOG.md` の記述を更新
- `pytest tests/` 148 passed で回帰なしを確認

### 2026-01-09 (Docker化)

- Docker化を実装:
  - `docker/Dockerfile.tts-server` - TTS server用Dockerfile
  - `docker/docker-compose.yml` - Compose設定
  - `docker/config.yml` - CPU用TTS設定
  - `docker/docker-compose.override.yml.windows` - Windows用設定
  - `.dockerignore` - ビルド最適化
- 解決した問題:
  - pyopenjtalk辞書: ビルド時にダウンロード
  - transformers CVE-2025-32434: torch >= 2.6必須
  - numba依存: monotonic_alignment用に追加
  - typing-extensions: バージョン競合解決
- Docker統合テスト: 全4テスト合格
- ドキュメント作成:
  - `docs/setup/docker.md` - Dockerセットアップガイド
  - `docs/setup/obs-audio-routing.md` - OBS音声ルーティング設定
  - `docker/README.md` - クイックリファレンス

### 2026-01-09 (Phase 2 Integration)

- Integrated with real Style-Bert-VITS2 server
- Changed server port from 5000 to 5001 (macOS AirPlay conflict)
- Updated config/default.yaml and Style-Bert-VITS2/config.yml
- Created integration test: `examples/integration_test_tts.py`
- Full end-to-end TTS working:
  - Server connects on port 5001
  - Speech synthesis with "shen" model
  - Audio playback via sounddevice
- All 148 unit tests passing

### 2026-01-09 (Phase 2)

- Implemented Phase 2 TTS Pipeline
- Clean Architecture structure: Domain → Application → Infrastructure → Presentation
- All 134 unit tests passing (57 new TTS tests)
- Demo script verified all TTS components working with mocks
- Lazy imports for optional dependencies (sounddevice, httpx)
- Factory pattern for dependency injection
- Ready for Issue #5 closure

### 2026-01-09 (Phase 1)

- Implemented Phase 1 Core Infrastructure
- Code review performed, all Critical/High issues fixed
- All 77 unit tests passing
- Demo script verified all components working
- Pushed to main branch
- Issue #8 closed with completion comment
- Updated CLAUDE.md and design document with completion status
