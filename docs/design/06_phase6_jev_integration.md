# Phase 6: Jev + Minecraft Bridge Integration 詳細設計書 (Issue #4)

> この文書は `feat/town-m1-m2` に**実装済み**の構成を記述する。最初の Phase 6 最小版（閉じた Goal 4〜7種と手書きの複合アクション）から、M1（生存）と F（根源的な行動の組み合わせ）を経て今の形になった。設計の考え方と Jev の実測は `10_agent_lifecycle.md` と `11_primitive_actions.md`、経緯は §11。

## 1. 概要

Minecraft のサバイバルで、AI 配信者が自分で設計した家を建て、夜を越し、暮らしていく。人の介入はしない。

| 層 | 担当 | 決めること | 頻度 |
|----|------|-----------|------|
| Gemini（`main_model`） | 方針 | 家の設計（構造化 JSON）と、次の目標（述語の語彙） | 開始時 + 目標の選び直しが必要なとき |
| ブリッジ（Node.js / Mineflayer） | 判定・分解・実行 | 目標を満たしたか（世界の状態から）、依存関係の分解、今できる具体的な候補、安全の制約、根源的な行動の実行、反射 | 毎ステップ（反射は 0.2 秒ごと） |
| Jev（TypeSafe AI System One） | 選択 | 候補から次の1つ | 候補が2つ以上ある各ステップ |

### 確定仕様

| 項目 | 内容 |
|------|------|
| 目標 | `have(item, count)` / `built` / `placed(bed, home)` / `at_home` / `through_night` / `explored(distance)`。達成はブリッジが世界から判定する（LLM やアクションの戻り値には頼らない） |
| 家 | 1部屋、平屋根、ドア1つ、窓なし（ガラスのない穴から家の中の bot が撃たれた）。幅・奥行き 5〜7、壁の高さ 3〜4（`HouseBlueprint`） |
| 設計・目標の検証 | 設計は `HouseBlueprint`、目標は `GoalSpec`（形）とブリッジ（中身: 存在するアイテムか、家があるか）で検証し、拒否の理由をプロンプトに入れて出し直させる（各3回まで） |
| 行動 | 根源的な行動14種を、具体的な対象つきの候補としてブリッジが列挙する。前提を満たすものだけが出る |
| 行動選択 | Jev `system_one` に `Choice` を1問。候補が1つならモデルを呼ばない |
| 目標の終了 | 達成、行き詰まり（連続失敗 3）、停滞（残りの作業量が 8 ステップ減らない）、予算（40 ステップ）、時間帯の変化 |
| 安全 | 反射（近くの敵、水中で浮く）と、家にこもる間は外の候補を出さない制約は、コードで行う |
| 失敗時 | フォールバックなし。LLM / Jev / ブリッジの例外はそのまま実行を終了させる |
| Minecraft | Paper 1.21.4（offline、`docker/docker-compose.minecraft.yml`）、難易度 easy、gamerule は既定 |

### スコープ外（後続）

- 実況・TTS との接続 → Phase 8。Twitch のコメントへの返答 → M2（`09_town_building_roadmap.md`）
- Jev による「続けるか・切り替えるか」の判定（`Noul`）→ `11_primitive_actions.md` §2
- 教訓メモリ（M1.5）、スクショを Gemini に見せる

## 2. コンポーネント構成

`00_architecture_overview.md` §2.5 の4層構成に従う。Minecraft に触れるものは Node.js サイドカー `minecraft-bridge/` に閉じ込め、Python からは HTTP だけで使う。目標の判定はブリッジだけが行う（両側に判定があるとずれる）。

```
src/ailoveshen/
├── domain/
│   ├── value_objects.py   # GoalPredicate, GoalSpec, Goal, GoalStatus, Candidate, GameObservation,
│   │                      # Side, BlockKind, PlannedBlock, HouseBlueprint,
│   │                      # ActionDecision, ActionResult
│   ├── entities.py        # PlaySession（目標のライフサイクル）
│   ├── events.py          # HouseDesignedEvent, GoalSetEvent, GameActionExecutedEvent, HouseCompletedEvent
│   └── exceptions.py      # GameBridgeError, GoalRejectedError, ActionSelectionError
├── application/
│   ├── ports/input/play.py              # IStartPlay, IAdvancePlay
│   ├── ports/output/minecraft_bridge.py # IMinecraftBridge
│   ├── ports/output/action_selector.py  # IActionSelector
│   ├── ports/output/game_prompt_builder.py # IGamePromptBuilder
│   ├── dto/game_dto.py                  # GoalOutcome, PlayStepReport
│   └── use_cases/play.py                # StartPlayUseCase, AdvancePlayUseCase
├── infrastructure/
│   ├── config.py                        # JevSettings, MinecraftSettings
│   └── adapters/
│       ├── jev/jev_action_selector.py   # JevActionSelector (typesafe-sdk)
│       ├── minecraft_bridge/mineflayer_bridge_client.py # MineflayerBridgeClient (httpx)
│       ├── prompts/game_prompt_template_builder.py      # GamePromptTemplateBuilder
│       └── gemini/gemini_text_generator.py              # generate_json
├── presentation/services/game_service.py # GameService.play
└── factories/game.py                     # create_game_service

minecraft-bridge/            # Node.js サイドカー（mineflayer 4.39.0, minecraft-protocol 1.68.0）
├── src/index.mjs            # bot 起動 + HTTP API、行動の実行（中断・時間上限）
├── src/goals.mjs            # 目標の語彙の検証と判定、欲求（needs）
├── src/knowledge.mjs        # アイテムのグループ、入手方法、データの補正
├── src/solver.mjs           # 依存関係のソルバー（純粋関数）
├── src/world.mjs            # 掘れるブロック・狩れる動物・作業台を探す
├── src/candidates.mjs       # 候補の列挙と安全の制約
├── src/primitives.mjs       # 根源的な行動、移動の規則、戦う・逃げる
├── src/reflex.mjs           # 反射（近くの敵、水中で浮く）
├── src/home.mjs             # 拠点、ドア、ベッドの位置、状態の保存（data/state.json）
├── src/build.mjs            # BuildPlan: 建設地選び、1ブロックの設置、進捗
├── src/craft.mjs            # レシピ本経由のクラフト
├── src/observe.mjs          # 観測の要約、敵の判定
├── src/mirror.mjs           # プロトコルミラー（bot の視点を実クライアントで見る）
├── test/                    # node --test（ソルバー、家の保護）
└── tools/                   # goal-drive.mjs（規則で1目標を動かす）、*-check.mjs（別 bot での再現）ほか
```

Python の追加依存は extra `game`（`httpx`, `typesafe-sdk>=0.7.1`）。

## 3. Domain Layer

### 3.1 目標 (`domain/value_objects.py`)

- `GoalPredicate`: 語彙（§1）。`GoalSpec(predicate, item, count, where, distance)` は述語に必要な引数の有無と正の値だけを検証する（アイテムが存在するかはブリッジが判定する）。`to_dict()` でブリッジに送り、`describe()` は `have(planks, 12)` の形
- `Goal(spec, reason, set_at)`: Gemini が決めた現在の目標
- `GoalStatus(met, remaining, lines, blocked)`: ブリッジの判定。`remaining` は残りの作業量（採る数・クラフト回数・置く数）、`lines` は小目標と進み具合、`blocked` は進められない理由
- `Candidate(action_id, description)`: ブリッジが今実行できる具体的な行動。`description` は JSON（verb, target, distance など）
- `GameObservation`: `state`（ブリッジの要約 JSON）、`candidates`、`needs`、`goal`、体力・満腹度・時間帯、家（計画の有無・完成・拠点・中にいるか・ベッド）、`busy`

### 3.2 家の設計 (`HouseBlueprint`)

- フィールド: `name`, `concept`, `width`(x), `depth`(z), `wall_height`, `door_side`, `door_offset`, `corner_pillars`
- 寸法の上下限（5〜7、3〜4）は、ブリッジで建てきれたことを実測した範囲
- 検証: 寸法の範囲、ドアの offset が `1..壁の長さ-2`。違反は `ValueError`
- `blocks()` は設置できる順（壁を1段ずつ → 屋根を外周から内側へ → ドア）。座標は建設地の原点からの相対

### 3.3 PlaySession (`domain/entities.py`)

目標のライフサイクルを持つエンティティ。達成の判定はしない（ブリッジの `GoalStatus` を読む）。

- `track_progress(obs)`: `remaining` が今までの最小を下回らなければ停滞として数える
- `goal_end_reason(obs)` / `needs_new_goal(obs)`: 目標なし、達成、連続失敗 `max_consecutive_failures`（3）、停滞 `max_stalled_steps`（8）、`max_steps_per_goal`（40）、時間帯の変化
- `set_goal(goal, time_phase)`、`record(result)`

## 4. Application Layer

### 4.1 Output Ports

| Port | メソッド |
|------|---------|
| `IMinecraftBridge` | `observe()`, `set_goal(spec) -> GoalStatus`（拒否は `GoalRejectedError`）, `act(action_id)`, `set_build_plan(...)`, `close()` |
| `IActionSelector` | `select(state, candidates, instructions) -> ActionDecision`, `close()` |
| `IGamePromptBuilder` | `build_house_design_prompt`, `build_goal_prompt(blueprint, observation, current_goal, goal_ended_because, recent_goals, predicates, previous_error)`, `build_action_context(goal, observation) -> (state, instructions)` |
| `ITextGenerator` | `generate_json(prompt, schema)` |

### 4.2 StartPlayUseCase

家を設計させ（検証エラーは理由を添えて最大3回）、`HouseDesignedEvent` を発行し、計画をブリッジに送って `PlaySession` を返す。

### 4.3 AdvancePlayUseCase（1ステップ）

1. `observe()`。ブリッジが busy（反射中）なら何もしない。家の完成は1回だけ `HouseCompletedEvent` で知らせる
2. `track_progress`、目標の選び直しが必要なら決める
   - 選べる述語は状況で絞る: 未完成の計画があれば `built`、拠点があれば `at_home` / `through_night`、ベッドがなければ `placed`。`have` と `explored` は常に
   - プロンプト: 語彙の説明、家と進み具合、状況（時間帯、家、体力、持ち物、気をつけること、直近の行動と失敗の理由）、今の目標の小目標と進められない理由、これまでの目標と終わり方、選び直す理由
   - 形が不正、選択肢外、ブリッジが拒否 → 理由を添えて出し直し（3回まで。尽きたら `TextGenerationError`）
   - 決まったら `set_goal`、`GoalSetEvent`、もう一度 `observe()`（新しい目標の候補）
3. 候補が1つならそれ、2つ以上なら `build_action_context` の状態と指示で Jev に選ばせる
4. `act`、`record`、`GameActionExecutedEvent`、`PlayStepReport`

Jev に渡す状態は、目標、目標の理由、小目標の進み具合、進められない理由、欲求（`needs`: 数値と重さだけで行動名は書かない）、自分（体力、満腹度、時刻、家の中か、手持ち）、持ち物、近くの mob、直近の行動。指示文に優先順位は書かない（遠くの敵から逃げる過剰反応が出た）。候補の説明に「どの小目標のためか」も書かない（Jev がそれに引っ張られた）。いずれも `spikes/primitive_choice_eval.py` の実測による。

## 5. Infrastructure Layer (Python)

- `JevActionSelector`: `Choice` 1問。`criteria` は `{候補 id: 説明の JSON}`。`TypeSafeError` は `ActionSelectionError`
- `MineflayerBridgeClient`: `/observe` → `GameObservation`、`PUT /goal`（400 は `GoalRejectedError` に理由を入れる）、`/act`、`/build-plan`。接続失敗と非 200 は `GameBridgeError`。タイムアウト 60 秒は、ブリッジの行動の上限（最長 45 秒）より長い
- `GamePromptTemplateBuilder`: Gemini 向けは日本語、Jev 向けは英語（評価した言語）

## 6. Minecraft Bridge サイドカー (`minecraft-bridge/`)

### 6.1 HTTP API (`src/index.mjs`, 127.0.0.1 のみ)

| メソッド | パス | 内容 |
|---------|------|------|
| PUT | `/goal` | `{predicate, item?, count?, where?, distance?}`。不正なら 400 と理由 |
| GET | `/observe` | `{busy, observation, needs, goal: {spec, met, remaining, lines, blocked}, candidates: [{id, verb, target, ...}]}` |
| POST | `/act` `{id}` | 候補を列挙し直し、その id を完了（または失敗）まで実行。`{ok, result, seconds}` |
| PUT / GET | `/build-plan` | 建築計画の設定 / 進捗 |

- bot が未 spawn なら 503、実行中の `/act` は 409、反射中は実行せず `ok:false`、今列挙されていない id は `ok:false`
- 実行は `AbortSignal` で中断する: 動詞ごとの時間上限（既定 20 秒、家に帰る・ベッド・寝るは 45 秒、探索 30 秒）と、被弾（`attack` / `flee` 以外）。中断すると採掘と経路を止め、実行中の関数が止まるまで待つ
- 家の中から外の対象へ向かう候補は、先に家を出る（`leaveHome`。ドアの前に敵がいれば出ない）
- 計画・拠点・目標は `data/state.json` に保存し、再起動しても引き継ぐ。計画が完成すると拠点になる

### 6.2 目標とソルバー (`goals.mjs`, `solver.mjs`, `knowledge.mjs`)

- `have` は、持ち物を小目標に順に割り当て（柱用の原木を先に確保してから残りを板材にする）、足りないものをクラフト・採掘・狩りのうち安い方法で手に入れる木に分解する。見えていない入手元は探索になる。3x3 のレシピは作業台（近くにある / 持っていて置く / 作る）を小目標に足す
- `built` は、計画の残りブロックの材料（原木 → ドア → 板材の順）と、次のブロックの設置
- データの補正: silk touch が要るドロップは入手元にしない、自然のブロックだけを掘る（葉のリンゴ・棒は確率が誤っている）、狩るのは許可した動物だけ（ゾンビの腐った肉を食料にしない）、羊の羊毛を足す、同じ仲間への変換（染め直し）を使わない
- 欲求（`needs`）: 体力が危険（≤8）、空腹が危険（≤6）、夕方、夜に外にいる、到達できる敵と距離、ドアの前の敵

### 6.3 候補と安全の制約 (`candidates.mjs`, `world.mjs`)

- 目標の末端から: 掘る（ブロックの種類ごとに近い3つ、家と計画の範囲は除く、原木は地面から届くものだけ）、狩る、探索（4方向）、クラフト、作業台を置く、計画の次のブロックを置く、ベッドを置く、家に帰る、寝る
- 体から: 到達できる敵への攻撃と逃走（近い2体）、食べる（食料として数えるものだけ）、武器を持つ、近くのドロップを拾う
- 常に: 待つ（家の中ならドアを向いて静止）
- **家にこもっている間（夜、またはドアの前に敵）は、外に出る候補を出さない**。Jev は夜に外の原木を掘りに行く候補を 5/9 回選んだので、選ばせない

### 6.4 根源的な行動 (`primitives.mjs`)

dig, pickup, attack, flee, eat, equip, craft, place_table, place_plan, place_bed, sleep, go_home, wait, explore。結果は世界の状態で確かめ、失敗は失敗として返す（掘ったのに拾えない、倒したのに生きている、探索で 5m 進めない、など）。

M1 までに原因を調べて入れた仕組み:

- 経路の中断は `setGoal(null)`（`stop()` は待機中に呼ぶと次の `goto()` を失敗させる）
- 移動の制約: 建設中の構造物の上は歩かない、敵の近くと葉の上はコストを上げる、歩きながら壊してよいのは葉だけ、足場を積まない
- 原木へは `GoalLookAtBlock`（reach 4.5）。空中では掘らない
- ドロップは拾えるまでの遅延があるので、`playerCollect` を待って拾う。届かないドロップは以後出さない
- 逃走は独自の `GoalAwayFrom`（`GoalInvert(GoalFollow)` は敵が大きく動くまで再計画しない）
- ベッドは、向きがサーバーに届くのを待ってから置き、頭側のマスも確かめる（すぐ置くと、ドアを閉めたときの向きで置かれ、頭側がドアの内側のマスを塞いだ）
- 就寝は、起きた後の次の時刻更新で夜が明けたかを確かめる

### 6.5 反射 (`reflex.mjs`)

4 tick ごとに、5m 以内の敵（クリーパーは 7m）を見て、実行中の行動を中断して止まるのを待ち、武器があり1体でクリーパーでなければ戦い、それ以外は逃げる。家の中でドアが閉まっていれば外の敵は無視する。水中では経路探索中でなければジャンプを押し続ける（止まっている bot は泳がず、溺死した）。

### 6.6 建築・クラフト・ミラー

- `build.mjs`: 建設地は周囲の平らな場所（失敗した場所の近くでは探し直さない）。設置は 200ms 間隔（Paper の use_item 制限）で、スロットの更新を待つ。進捗は毎回世界のブロックを読み直す
- `craft.mjs`: Mineflayer の `bot.craft()` はこのサーバーで材料を失うので、レシピ本（`craft_recipe_request`）+ shift-click + 再同期で作る。レシピ要求は 100ms 間隔（Paper の recipe-spam-limit）。`mineflayer` は非公開 API に依存するので 4.39.0 に固定
- `mirror.mjs`: bot の視点を実クライアント（`127.0.0.1:25578`）で見る偽サーバー。約 60Hz の補間と回転速度の上限、採掘の腕振りとひび割れの合成

## 7. Presentation Layer / Composition Root

- `GameService.play(max_steps=200) -> PlayOutcome(session, steps, house_complete)`: 開始して、`max_steps` 行動するまで進める（家の完成後も続ける）。反射中の待ちはステップに数えない
- `create_game_service(settings.gemini, settings.jev, settings.minecraft, settings.character, event_bus)`

## 8. 設定

```yaml
# config/default.yaml
jev:
  api_key: "${TYPESAFE_API_KEY:-}"
  model: "jev-latest"
  timeout_seconds: 10

minecraft:
  bridge:
    host: "localhost"
    port: 3000
    timeout_seconds: 60        # ブリッジの行動の上限（最長 45 秒）より長くする
  agent:
    max_steps_per_goal: 40
    max_consecutive_failures: 3
    max_stalled_steps: 8
```

ブリッジ側は環境変数: `MC_HOST`、`MC_PORT`、`BOT_NAME`、`BRIDGE_PORT`（3000）、`MIRROR_PORT`（25578）。

## 9. テスト

| ファイル | 対象 |
|---------|------|
| `tests/unit/domain/test_house.py` | `HouseBlueprint`、`GoalSpec` の検証、`PlaySession` の終了条件（達成、行き詰まり、停滞、予算、時間帯） |
| `tests/unit/application/test_play_use_cases.py` | 設計、目標の設定と出し直し（形の不正・選択肢外・ブリッジの拒否）、述語の絞り込み、Jev への受け渡し、候補1つ、停滞、完成の通知、busy |
| `tests/unit/infrastructure/adapters/...` | Jev（JSON の説明）、ブリッジクライアント（`/observe`・`PUT /goal`・400）、プロンプト |
| `minecraft-bridge/test/*.test.mjs`（`npm test`） | ソルバー（割り当て順、作業台、レシピの解放、ベッドの色、データの補正、道具）、家の保護 |

## 10. 検証方法

```bash
docker compose -f docker/docker-compose.minecraft.yml up -d
cd minecraft-bridge && npm install && npm test && npm start
node tools/goal-drive.mjs '{"predicate":"have","item":"planks","count":12}'   # 規則で1目標（Python なし）
GEMINI_API_KEY=... TYPESAFE_API_KEY=... python examples/integration_test_minecraft.py --max-steps 400
```

実機での確認結果は WORKLOG に記録する。

## 11. 経緯

| 段階 | 形 | 変えた理由 |
|------|----|-----------|
| 当初の計画 | 13 種の Goal、Reactive / Tactical の2ループ | spike で、今できる行動を列挙して Jev に1問で選ばせれば十分速いと分かった |
| Phase 6 最小版 | 閉じた Goal 4 種 + 手書きの複合アクション（collect_log, build_step など）。達成判定は Python の `HouseProject` | 家を1軒建てられた |
| M1 | Goal 7 種、反射、剣、夜にこもる、食料、ベッド、拠点の保存 | 夜と敵がいる既定の条件で生き延びるため |
| F（今） | 述語の語彙、ブリッジが判定・分解・候補を出す、根源的な行動 14 種 | 行動を足すたびに配信外で複合アクションを手書きしていたため（`11_primitive_actions.md`） |
