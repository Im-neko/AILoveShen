# Phase 6: Jev + Minecraft Bridge Integration 詳細設計書 (Issue #4)

> この文書は `feat/phase6-minimal` に**実装済み**の構成を記述する。当初の計画（13 Goal、Reactive/Tactical の2ループなど）との違いは §11 にまとめる。

## 1. 概要

Minecraft のサバイバルで、AI 配信者が**木を集め、クラフトし、自分で設計した家を建てる**ところまでを、人の介入なしで行う。

判断は3層に分ける。

| 層 | 担当 | 決めること | 頻度 |
|----|------|-----------|------|
| LLM（Gemini, `main_model`） | System 2 | 家の設計（構造化 JSON）と、今取り組む Goal（閉じた4種から1つ） | 開始時1回 + Goal の選び直しが必要なとき |
| Jev（TypeSafe AI System One） | System 1 | Goal が許す実行可能アクションから次の1つ | 候補が2つ以上ある各ステップ |
| Minecraft Bridge（Node.js / Mineflayer） | 実行 | 今実行できるアクションの列挙、実行、ブロック設置、世界の観測 | 各ステップ |

### 確定仕様

| 項目 | 内容 |
|------|------|
| Goal | `gather_wood` / `craft` / `build_shelter` / `explore` の4種（`GoalType`） |
| 家 | 1部屋、平屋根、ドア1つ、窓0〜4。幅・奥行き 5〜7、壁の高さ 3〜4（`HouseBlueprint`） |
| 設計の検証 | Gemini の structured output（JSON Schema）→ `HouseBlueprint` で検証。検証エラーは理由をプロンプトに入れて再設計させる（計3回まで） |
| 行動選択 | Jev `system_one` に `Choice` を1問。候補が1つならモデルを呼ばない |
| 生存行動 | `attack_hostile` / `flee_hostile` / `equip_weapon` / `eat` はどの Goal でも候補に入る（前提条件を満たすときだけ） |
| アクション | ブリッジ側で有限時間（20秒）に打ち切る。前提条件を満たすものだけを列挙する |
| 完了判定 | アクションの戻り値ではなく、世界に置かれているブロックから判定する（`BuildPlan.status()`） |
| 失敗時 | フォールバックなし。LLM / Jev / ブリッジの例外はそのまま実行を終了させる |
| Minecraft | Paper 1.21.4（offline、`docker/docker-compose.minecraft.yml`） |

### スコープ外（後続）

- 実況・TTS との接続（ゲームイベントを発話にする）→ Phase 8
- 短周期の Reactive Loop（脅威を先読みして動く反射）→ §12。今あるのは被弾したときにアクションを中断する反射だけ（§6.1）
- Twitch コメントによる介入 → Phase 4 / 8

## 2. コンポーネント構成

`00_architecture_overview.md` §2.5 の4層構成に従う。Minecraft に触れるものは Node.js サイドカー `minecraft-bridge/` に閉じ込め、Python からは HTTP だけで使う。

```
src/ailoveshen/
├── domain/
│   ├── value_objects.py   # GoalType, GOAL_ACTIONS, SURVIVAL_ACTIONS, Goal, Side, BlockKind,
│   │                      # WallOpening, PlannedBlock, HouseBlueprint, BuildStatus, MaterialNeeds,
│   │                      # AvailableAction, GameObservation, ActionDecision, ActionResult
│   ├── entities.py        # HouseProject
│   ├── events.py          # HouseDesignedEvent, GoalSetEvent, GameActionExecutedEvent, HouseCompletedEvent
│   └── exceptions.py      # GameBridgeError, ActionSelectionError
├── application/
│   ├── ports/input/build_house.py       # IStartHouseProject, IAdvanceHouseProject
│   ├── ports/output/minecraft_bridge.py # IMinecraftBridge
│   ├── ports/output/action_selector.py  # IActionSelector
│   ├── ports/output/game_prompt_builder.py # IGamePromptBuilder
│   ├── ports/output/text_generator.py   # ITextGenerator.generate_json（追加）
│   ├── dto/game_dto.py                  # HouseStepReport
│   └── use_cases/build_house.py         # StartHouseProjectUseCase, AdvanceHouseProjectUseCase
├── infrastructure/
│   ├── config.py                        # JevSettings, MinecraftSettings
│   └── adapters/
│       ├── jev/jev_action_selector.py   # JevActionSelector (typesafe-sdk)
│       ├── minecraft_bridge/mineflayer_bridge_client.py # MineflayerBridgeClient (httpx)
│       ├── prompts/game_prompt_template_builder.py      # GamePromptTemplateBuilder
│       └── gemini/gemini_text_generator.py              # generate_json（追加）
├── presentation/services/game_service.py # GameService
└── factories/game.py                     # create_game_service

minecraft-bridge/            # Node.js サイドカー（mineflayer 4.39.0, minecraft-protocol 1.68.0）
├── src/index.mjs            # bot 起動 + HTTP API
├── src/observe.mjs          # 観測の要約、敵の判定
├── src/actions.mjs          # アクションの列挙と実行
├── src/build.mjs            # BuildPlan: 建設地選び、ブロック設置、進捗
├── src/craft.mjs            # レシピ本経由のクラフト
├── src/mirror.mjs           # プロトコルミラー（bot の視点を実クライアントで見る）
└── tools/                   # house-plan.mjs（固定の建築計画）, viewer-check.mjs（ミラーのヘッドレス確認）
```

Python の追加依存は extra `game`（`httpx`, `typesafe-sdk>=0.7.1`）。

## 3. Domain Layer

### 3.1 Goal とアクション (`domain/value_objects.py`)

`GoalType` は、ブリッジに実行手段があるものだけを並べる。実行できない Goal を LLM に見せると、エージェントが止まるため。

```python
GOAL_ACTIONS = {
    GoalType.GATHER_WOOD:   {"collect_log", "pickup_drop"},
    GoalType.CRAFT:         {"craft_planks", "craft_crafting_table", "place_crafting_table",
                             "craft_door"},
    GoalType.BUILD_SHELTER: {"build_step"},
    GoalType.EXPLORE:       {"explore", "pickup_drop"},
}
SURVIVAL_ACTIONS = {"attack_hostile", "flee_hostile", "equip_weapon", "eat"}
```

- `Goal(goal_type, reason, set_at)`: LLM が決めた現在の方針。`allows(action_id)` は `SURVIVAL_ACTIONS` か、その Goal の `GOAL_ACTIONS` に含まれるかを返す
- アクション ID はブリッジの語彙そのもの（文字列）。domain は ID の集合だけを持ち、実行の中身は知らない

### 3.2 家の設計 (`HouseBlueprint`)

- フィールド: `name`, `concept`, `width`(x), `depth`(z), `wall_height`, `door_side`, `door_offset`, `windows: tuple[WallOpening, ...]`, `corner_pillars`
- 寸法の上下限（`MIN_SIDE=5`, `MAX_SIDE=7`, `MIN_WALL_HEIGHT=3`, `MAX_WALL_HEIGHT=4`）は、ブリッジで設置の失敗なしに建てきれたことを実測した範囲（5x5x3 と 7x7x4）
- `__post_init__` の検証: 寸法の範囲、ドアと窓の offset が `1..壁の長さ-2`（角は壁の支えになるので不可）、窓がドアや他の窓と重ならない。違反は `ValueError`
- `blocks()` は設置できる順に `PlannedBlock(x, y, z, kind)` を返す
  1. 壁を1段ずつ（ドアの位置は下2段、窓は `WINDOW_Y=1` を空ける。`corner_pillars` なら四隅は `LOG`）
  2. 屋根を外周のリングから内側へ（各ブロックが壁か直前の屋根ブロックに接する）
  3. 最後にドア
- `material_counts()`: 種類ごとの必要数。5x5x3 は板材 71 + ドア 1、7x7x4 は計 144 ブロック

座標は建設地の原点（足元の最初の空気層、footprint の最小角）からの相対。北は -Z、東は +X。

### 3.3 観測と結果

| 型 | 内容 |
|----|------|
| `AvailableAction(action_id, description)` | ブリッジが今実行できるアクション |
| `BuildStatus(total, placed, complete, site_chosen, remaining)` | ブリッジが世界から読み取った建築の進捗。`remaining` は未設置ブロックの種類別の数 |
| `GameObservation` | `state`（モデルにそのまま渡す JSON 要約）と、domain が読む型付きフィールド（`inventory`, `actions`, `health`, `food`, `crafting_table_nearby`, `build`）。`count(suffix)` で `_log` 等の合計を数える |
| `MaterialNeeds(logs_short, planks_short, door_needed, table_needed)` | 家を完成させるまでに足りないもの。`wood_ready`, `crafted` プロパティ |
| `ActionDecision(action_id, confidence, probabilities)` | Jev の選択 |
| `ActionResult(action_id, ok, result, seconds)` | ブリッジでの実行結果 |

### 3.4 HouseProject (`domain/entities.py`)

1軒の家の建築を表すエンティティ。**Goal の達成判定は LLM ではなくここで、観測した世界から行う**。LLM は次に何を目指すかを選ぶだけ。

- `material_needs(obs)`: `obs.build.remaining`（なければ設計図全体）と持ち物から不足を計算する
  - ドアが未設置で手持ちにない → ドア用の板材 6 を加算
  - さらに作業台が近くにも手持ちにもない → 作業台用の板材 4 を加算
  - 不足する板材は原木に換算（1原木 = 4板材）し、`LOG` ブロックの分と合わせて `logs_short` にする
- `goal_met(obs)`

  | Goal | 達成条件 |
  |------|---------|
  | `gather_wood` | `needs.wood_ready`（残りを全部作れるだけの原木がある） |
  | `craft` | `needs.crafted`（板材が足りていて、ドアも手元にある） |
  | `build_shelter` | `is_complete(obs)` |
  | `explore` | その Goal で `explore_steps`（3、固定値）ステップ進んだ |

- `needs_new_goal(obs)`: 次のいずれかで Goal の選び直しが必要になる
  - Goal がまだない
  - Goal を達成した
  - 連続失敗が `max_consecutive_failures`（既定 3）に達した
  - その Goal で `max_steps_per_goal`（既定 15）ステップ進んだ
  - `block_goal()` された（Goal が許す実行可能アクションが1つもない。連続失敗を上限にして選び直させる）
- `goal_end_reason(obs)`: 選び直す理由の文。LLM のプロンプトとログに使う
- `record(result)`: ステップ数を進め、成功なら連続失敗を 0 に戻す
- `set_goal(goal)`: カウンタをリセットする

### 3.5 イベントと例外

- `HouseDesignedEvent(name, concept)`, `GoalSetEvent(goal_type, reason)`, `GameActionExecutedEvent(action_id, ok, result, confidence)`, `HouseCompletedEvent(name)`
- `GameBridgeError`: ブリッジに届かない、または非 200 を返した
- `ActionSelectionError`: Jev の呼び出しに失敗した

## 4. Application Layer

### 4.1 Output Ports

| Port | メソッド | 備考 |
|------|---------|------|
| `IMinecraftBridge` | `observe()`, `act(action_id)`, `set_build_plan(blocks, width, depth, height)`, `close()` | 世界に触れるものはすべてブリッジが持つ |
| `IActionSelector` | `select(state, actions, instructions) -> ActionDecision`, `close()` | 候補は2つ以上で呼ぶ |
| `IGamePromptBuilder` | `build_house_design_prompt(character, previous_error)`, `build_goal_prompt(...)`, `build_action_instructions(goal, blueprint)` | |
| `ITextGenerator` | `generate_json(prompt, schema, system_instruction) -> dict` を追加 | schema はモデル非依存の plain dict（JSON Schema）。JSON でない、object でない場合は `TextGenerationError` |

### 4.2 StartHouseProjectUseCase

1. `build_house_design_prompt(character, previous_error)` で設計を依頼し、`generate_json(prompt, HOUSE_SCHEMA)` を呼ぶ
2. `_parse_blueprint` → `HouseBlueprint` の検証で `ValueError` が出たら、そのメッセージを `previous_error` にして再依頼する（`max_attempts=3` は初回を含む回数）
   - `HOUSE_SCHEMA` の `door_offset` / 窓の `offset` の上限は `MAX_SIDE-2` 固定で、実際の壁の長さとは連動しない。この差は再依頼で吸収する
   - `generate_json` 自体の失敗（API エラー、不正な JSON）は再依頼せずにそのまま送出する
3. `HouseDesignedEvent` を発行し、`bridge.set_build_plan(blueprint.blocks(), width, depth, height)` で計画を渡す
4. `HouseProject` を返す

### 4.3 AdvanceHouseProjectUseCase（1ステップ）

1. `bridge.observe()`
2. `project.is_complete(obs)` なら `HouseCompletedEvent` を発行し、`complete=True` を返す
3. `project.needs_new_goal(obs)` なら Goal を決める
   - 選択肢は `_available_goals(obs)`: `GOAL_ACTIONS[g]` と今の実行可能アクションの積が空でない Goal
   - `build_goal_prompt` に、設計図、進捗、不足、状況、直近の Goal（`goal_history=5`）、選び直す理由を入れる
   - schema の `enum` を選択肢に絞って `generate_json`。選択肢外の答えや不正な形は `TextGenerationError`
   - `project.set_goal`、`GoalSetEvent` を発行
4. 候補 = 実行可能アクションのうち `goal.allows()` を満たすもの
   - 0件 → `project.block_goal()` して行動せずに返す（次のステップで選び直し）
   - 1件 → Jev を呼ばずにそれを選ぶ（confidence 1.0）
   - 2件以上 → `build_action_instructions` と `obs.state` を渡して `action_selector.select`
5. `bridge.act(action_id)`、`project.record(result)`、`GameActionExecutedEvent` を発行
6. `HouseStepReport(goal, goal_changed, decision, result, complete)` を返す

`explore` はブリッジが常に列挙するので、`explore` Goal にだけ含める。他の Goal の予備手段にすると、その Goal が常に「選べる」ことになり、絞り込みが効かなくなる（材料がなくても `build_shelter` が選べて、`explore` だけで歩き回っていた）。木が届かない、建設地がない、といった状況では、その Goal の候補が 0 件になって選び直しになり、LLM が `explore` を選ぶ。

## 5. Infrastructure Layer (Python)

### 5.1 JevActionSelector (`adapters/jev/jev_action_selector.py`)

- `typesafe_sdk.AsyncTypeSafeClient(api_key, timeout)` を使う。API キーが空なら `ValueError`
- 1回の `system_one` に `Choice` を1問だけ投げる。`criteria` は `{action_id: description}`

  ```python
  response = await self._client.system_one(
      state=state,
      questions={"action": Choice(instructions=instructions, criteria=criteria)},
      model=self._model,
  )
  answer = response.choices["action"]  # choice, confidence, probabilities
  ```

- `TypeSafeError` は `ActionSelectionError` に変換する。レイテンシと入力トークン数をログに出す
- `jev/__init__.py` は `JevActionSelector` を遅延 import する（`typesafe-sdk` がない環境でもパッケージを import できるように）

### 5.2 MineflayerBridgeClient (`adapters/minecraft_bridge/mineflayer_bridge_client.py`)

- `httpx.AsyncClient`。タイムアウト（既定 60秒）は、ブリッジのアクション打ち切り（20秒）より長くする
- 接続失敗と非 200 は `GameBridgeError`。409（busy）と 503（bot 未 spawn）もここに入る
- `/observe` のレスポンスを `GameObservation` に変換する。`observation.build.materials_needed` を `BuildStatus.remaining`、`origin` の有無を `site_chosen` にする

### 5.3 GamePromptTemplateBuilder (`adapters/prompts/game_prompt_template_builder.py`)

- LLM 向け（設計、Goal）は日本語。他のプロンプトと揃える
  - 設計: キャラクター名と性格、建てられる家の条件（寸法の上下限は `HouseBlueprint` の定数から埋める）、前回の設計が使えなかった理由
  - Goal: 設計図と必要ブロック数、設置済み数と建設地の有無、不足、時間帯・体力・持ち物・見えている敵・作業台・切れる木の有無、直近の行動、これまでの Goal、選び直す理由、今選べる Goal
- Jev 向け（`build_action_instructions`）は英語。Jev の評価をこの言語で行ったため。優先順位は「1) 生き延びる 2) 現在の Goal を進める」

### 5.4 GeminiTextGenerator.generate_json

`response_mime_type="application/json"` と `response_json_schema=schema` を設定して生成し、`json.loads` した dict を返す。レート制限・リトライ・トークンログは `generate` と共通の `_generate_text` を通る。

## 6. Minecraft Bridge サイドカー (`minecraft-bridge/`)

1プロセスで Mineflayer bot（Minecraft 1.21.4、offline）、HTTP API、プロトコルミラーを動かす。起動: `cd minecraft-bridge && npm install && npm start`。

### 6.1 HTTP API (`src/index.mjs`, 127.0.0.1 のみ)

| メソッド | パス | 内容 |
|---------|------|------|
| GET | `/observe` | `{ observation, actions: [{id, description}], busy }` |
| POST | `/act` `{id}` | アクションを1つ完了（または失敗）まで実行。`{ ok, result, seconds }` |
| PUT | `/build-plan` | `{ blocks: [{x,y,z,block}], width, depth, height }` を建築計画として設定 |
| GET | `/build-plan` | 建築の進捗（`/observe` の `build` と同じ `status()`）。Python からは使っていない（手動確認用） |

- bot が未 spawn なら 503、実行中に `/act` が来たら 409
- 今列挙されていない ID の `/act` はエラーにせず、`ok:false` と `action X is not available now` を返す
- 実行は `AbortSignal` で中断できる。中断の理由は2つ
  - 20秒（`ACTION_TIMEOUT_MS`）の打ち切り
  - **被弾**（`entityHurt`）: 実行中に bot がダメージを受けたら中断して `interrupted: took damage (zombie nearby)` を返す。次のステップで脅威が候補に出て、Jev が戦うか逃げるかを選ぶ。`attack_hostile` / `flee_hostile` は被弾を前提にした行動なので中断しない
- 中断すると採掘を止め、`pathfinder.setGoal(null)`、操作状態をクリアする。実行中の関数が本当に止まるまで待ってから返すので、アクションは重ならない（ループはシグナルを見て抜け、待ちにはすべて上限がある）。以前は `Promise.race` で打ち切るだけで、`buildStep` や `craft_planks` のループが裏で動き続けていた
- 実行結果は `history` に積み、直近5件を `observation.recent_actions` に入れる。死亡も `event` として積む

### 6.2 観測 (`src/observe.mjs`)

`observation` は時間帯・天気、自分（体力、満腹度、位置、手持ち、水中か）、持ち物、周囲 48m の mob（最大16体、敵対か、距離、方角、高低差、見えているか）、24m 以内のドロップ、直近の行動に加え、`index.mjs` が足す `resources`（届く原木、作業台までの距離）、`crafting_table_nearby`、`build`（進捗 + `materials_needed`）からなる。

**脅威**（`threats`）は、16m 以内・高低差 3 未満で、視線が通るか 4m 以内にいる敵対 mob に限る。洞窟の下や壁の向こうの mob は観測には出すが脅威にはしない。4m 以内は視線に関係なく脅威にする（run5 で、葉の陰から殴ってきたゾンビが脅威に入らず、bot が倒された）。Mineflayer が hostile に分類する中立 mob（enderman、piglin など）と、昼の spider は除外する。

### 6.3 アクションの列挙と実行 (`src/actions.mjs`)

**前提条件を満たすアクションだけを列挙する**。判断側は不可能なアクションを選べない。

| ID | 列挙される条件 | 実行内容 |
|----|---------------|---------|
| `attack_hostile` / `flee_hostile` | 脅威がいる | 追って攻撃（最大10秒） / 16m 離れるまで逃げる（最大8秒） |
| `equip_weapon` | 武器（剣・斧）を持っていて手に持っていない | 装備 |
| `eat` | 満腹度 < 20 で食料がある | 食べる |
| `pickup_drop` | 24m 以内・高低差 4 未満に、まだ失敗していないドロップがある | 拾いに行く。届かない、または拾えなかったら失敗を返し、そのドロップを以後除外する |
| `collect_log` | 32m 以内に届く原木がある（幹の下 4 ブロック以内に地面） | 切って、近くのドロップを拾う |
| `craft_planks` | 家に使う原木を除いて余る原木がある | 余りを板材にする |
| `craft_crafting_table` | 作業台が手元にも近くにもなく、板材 4 以上 | 2x2 で作業台を作る |
| `place_crafting_table` | 作業台を持っていて近くにない | bot から2マス離れた空きマスに置く（建設地が決まっていればその周囲1マスを避ける） |
| `craft_door` | 近くに作業台、同じ種類の板材 6 以上、ドアを持っていない | 作業台でドアを作る |
| `build_step` | 計画が未完成で、次に置くブロックの材料があり、建設地が決まっているか、建設地探しに失敗した場所から 16m 以上離れている | 最大8ブロック置く（§6.4） |
| `explore` | 常に | ランダムな方向へ約20m。3方向試して 5m 以上進めなければ失敗 |
| `idle` | 常に | 周りを見回す。どの Goal にも生存行動にも含まれないので、Python からは選ばれない |

原因調査から入った設計上の注意:

- **経路のキャンセルは `setGoal(null)`**。`pathfinder.stop()` はフラグを立てるだけで、次の移動 tick で消える。待機中に呼ぶと、次の `goto()` がすぐ失敗する
- **Movements の制約**（`configureMovements`）
  - 建設中の構造物の上（原点から高さ +2 以上）は歩かない。途中の壁が階段になって登ってしまい、壁の上から屋根を置いたり、そこで動けなくなったりしたため
  - 葉の上を歩くコストを上げる（樹冠に乗ると降りにくい）
  - 歩行中に壊してよいのは葉だけ。家や地形は壊さない
  - 足場ブロックを使わない、1x1 の柱登りをしない（建材を消費しないため）
- **原木を切る位置は `GoalLookAtBlock`（reach 4.5）**。`GoalNear` だと、頭より高い原木には葉の上からしか「近く」にならない。空中での採掘は5倍遅いので、着地を待ってから掘る
- **`craft_planks` は原木を残す**。`corner_pillars` の柱として置く原木（`materialsNeeded().log`）は板材にしない
- **失敗は正直に返す**。`explore` はほとんど進めなかったら、`pickup_drop` は持ち物が増えなかったら失敗にする。成功扱いにすると、Goal の連続失敗による選び直しが働かない

### 6.4 建築 (`src/build.mjs`)

`BuildPlan` は Python から受けたブロックを順に置くだけで、形は決めない。

- **建設地**: 見つからなければ失敗を返し、その場所を覚える（同じ場所で探し直しても無駄なため）。最初の `build_step` で、bot の周囲を半径 2〜24 のリングで広げ、高さ ±4 の範囲から探す。footprint の地面がすべて固体で不適当なもの（葉、原木、板材、水、氷、砂、砂利など）でなく、家の高さまで空気・草・葉（掘って除く）だけの場所を選ぶ
- **設置順**: `pending()`（まだ置かれていないブロック）を計画の順に、1ステップ最大 `BLOCKS_PER_STEP=8`。材料が尽きたらそこで止める（1つも置けなければ失敗）
- **設置位置**: `GoalPlaceBlock(pos, { range: 4, LOS: false })`。サーバーが検証するのは reach とカーソルで、視線ではない。そのため最初の屋根ブロックも家の中から壁の上に置ける
- **ペース**: 設置の間隔を 200ms 空ける。Paper は use_item パケットを 300ms あたり8個を超えると、何も返さずに捨てる。バニラクライアントの右クリック連打（4 tick）に合わせた
- **スロット更新を待つ**: 手持ちのスタックを減らすのはサーバー。そのスロット更新（最大1秒）を待たないと、次の設置で使い切ったスタックを選んでしまう
- **進捗**: `status()` は毎回、計画の各座標のブロックを世界から読み直して判定する（種類が合っていれば木の種類は問わない）。完了判定はこれだけに頼る

### 6.5 クラフト (`src/craft.mjs`)

Mineflayer の `bot.craft()` はこのサーバーでは壊れている。予測したカーソル操作を待たずに連続送信するため、最初の再同期の後に在庫モデルがずれ、以降のクリックで材料がカーソルに入れ替わる。結果、クラフトのたびに残りの材料を失う（原木 5 → 板材 4、原木 0）。

そのため、バニラのレシピ本ボタンと同じ経路を使う。

1. `recipe_book_add` / `recipe_book_remove` パケットから、解放済みレシピを結果アイテムごとに保持する（`RecipeBook`）
2. `close_window` と `_syncWindow` で在庫をサーバーと同期する
3. `craft_recipe_request` を送り、サーバーにグリッドを埋めさせる（`makeAll` でスタック全部）
4. 結果スロットに目的のアイテムが来たら（最大2秒）、shift-click で取る（サーバー側の quick move で、カーソルを使わない）
5. ウィンドウを再同期して、増えた数を返す。増えなければ失敗

`RecipeBook` は `bot._client` と `bot._syncWindow` という Mineflayer の非公開 API に依存する。そのため `mineflayer` は 4.39.0 に固定している。

### 6.6 プロトコルミラー (`src/mirror.mjs`)

bot の一人称視点を、実際の Minecraft クライアント（1.21.4）で見るための偽サーバー（`127.0.0.1:25578`）。

- bot が受けた configuration / play パケットを記録し、生バイトのまま視聴クライアントへ中継する。途中で接続したクライアントには、記録したチャンク・エンティティ・状態を再生する
- 視聴側は bot と同じエンティティ ID で、入力は無視する（読み取り専用）
- **カメラ**: 約60Hz で位置パケットを送る。bot の 20Hz の物理位置を補間し、視点の回転速度に上限（150°/s）を設ける。`bot.lookAt()` の瞬間的な向き替えがカクついて見えないように
  - 採掘中は上限を 600°/s に上げる。Mineflayer は向いた直後に掘り始め、葉は約 0.35 秒で壊れるので、視点が届く前に壊れてしまうため
- **採掘の演出**: サーバーは自分の腕振りと採掘のひび割れを返さない。Mineflayer には採掘開始イベントもないので、`bot.targetDigBlock` を毎 tick 監視し、`animation` と `block_break_animation` を合成して送る

`tools/viewer-check.mjs` はミラーにヘッドレスで接続し、届いたパケットを集計する。`tools/house-plan.mjs` は Python を介さずに建築を試すための固定計画（`PUT /build-plan` の本文）を出力する。

## 7. Presentation Layer / Composition Root

### 7.1 GameService (`presentation/services/game_service.py`)

- `build_house(max_steps=200) -> HouseBuildOutcome(project, steps, complete)`: `StartHouseProjectUseCase` を1回、その後 `AdvanceHouseProjectUseCase` を完成するか `max_steps` に達するまで繰り返す。各ステップを1行でログに出す
- 例外は捕まえない。どの層の失敗でも実行はそこで終わる
- `close()`: ブリッジ、Gemini、Jev のクライアントを閉じる

### 7.2 create_game_service (`factories/game.py`)

```python
game = create_game_service(
    settings.gemini, settings.jev, settings.minecraft, settings.character, AsyncEventBus()
)
outcome = await game.build_house()
await game.close()
```

- Gemini は `main_model` / `main_thinking_level` の枠を使う。設計と Goal 決定の両方で同じ `GeminiTextGenerator` を共有する
- キャラクターは `factories/llm.py` の `create_character_profile` を再利用する
- `max_steps_per_goal` / `max_consecutive_failures` は `StartHouseProjectUseCase` 経由で `HouseProject` に渡る

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
    timeout_seconds: 60        # ブリッジのアクション打ち切り（20秒）より長くする
  agent:
    max_steps_per_goal: 15
    max_consecutive_failures: 3
```

- `JevSettings(api_key, model, timeout_seconds)`、`MinecraftSettings(bridge_host, bridge_port, request_timeout_seconds, max_steps_per_goal, max_consecutive_failures)`
- `explore_steps`（3）、`goal_history`（5）、設計の試行回数（3）はコード上の既定値で、設定には出していない
- ブリッジ側は環境変数: `MC_HOST`（localhost）、`MC_PORT`（25565）、`BOT_NAME`（AILoveShen）、`BRIDGE_PORT`（3000）、`MIRROR_PORT`（25578）
- 必要な API キー: `GEMINI_API_KEY`、`TYPESAFE_API_KEY`

## 9. テスト

| ファイル | 対象 |
|---------|------|
| `tests/unit/domain/test_house.py` | `HouseBlueprint` の検証とブロック展開（数、順序、ドア・窓・柱）、`Goal.allows`、`HouseProject` の不足計算と Goal の選び直し条件 |
| `tests/unit/application/test_build_house_use_cases.py` | 設計の送信・再依頼・打ち切り、初回の Goal 決定、選択肢の絞り込み、候補の絞り込み、候補1つで Jev を呼ばない、候補0で `block_goal`、完成で停止 |
| `tests/unit/infrastructure/adapters/jev/test_jev_action_selector.py` | Choice 1問の送信、`ActionDecision` への変換、SDK エラーの変換、close |
| `tests/unit/infrastructure/adapters/minecraft_bridge/test_mineflayer_bridge_client.py` | `/observe` の変換（計画あり・なし）、`/act`、`/build-plan` の送信順、エラー |
| `tests/unit/infrastructure/adapters/prompts/test_game_prompt_template_builder.py` | 設計・Goal プロンプトの内容、Jev 向け指示 |
| `tests/unit/infrastructure/adapters/gemini/test_gemini_text_generator.py` | `generate_json`（JSON モードの設定、object 以外の拒否）※追記 |
| `tests/unit/infrastructure/test_config.py` | `jev` / `minecraft` セクションの読み込み ※追記 |
| `tests/unit/presentation/test_game_service.py` | 完成までのループ、ステップ上限、close |
| `tests/unit/factories/test_game_factory.py` | 組み立て、API キー欠落時の `ValueError` |
| `tests/unit/test_architecture.py` | 層の依存規則（既存） |

Node.js 側（`minecraft-bridge/`）には自動テストがない。実サーバーでの確認（§10）だけで検証している。

## 10. 検証方法

```bash
docker compose -f docker/docker-compose.minecraft.yml up -d   # Paper 1.21.4
cd minecraft-bridge && npm install && npm start               # bot + HTTP API + ミラー
pip install -e ".[llm,game]"
GEMINI_API_KEY=... TYPESAFE_API_KEY=... python examples/integration_test_minecraft.py --max-steps 300
```

- bot には何も与えない。木を集め、クラフトし、建てるまでを自分で行う
- 設計・Goal・完成のイベントを表示し、最後に家の寸法、ブロック数、完成したか、ステップ数を出す。完成すれば終了コード 0
- 完成は `HouseProject.is_complete` で判定する。その元は `/observe` の `build.complete` で、ブリッジが世界のブロックを読み直した結果。アクションが「成功」と返したかどうかには頼らない
- 動きは 1.21.4 クライアントで `127.0.0.1:25578` に接続して見る

## 11. 当初計画からの変更点

| 当初の計画 | 実装 | 理由 |
|-----------|------|------|
| `GameAction` 13種の Goal | `GoalType` 4種 | 実行手段のない Goal を選ばせると止まる。ブリッジで実行できるものだけに絞った |
| Reactive Loop（~600ms、Noul/Score/Choice を並列）+ Tactical Loop（~10s） | 1ステップずつの逐次ループ。Jev は Choice 1問だけ | spike の評価で、今実行できるアクションを列挙して1問で選ばせれば、`jev-1.13.0` で約 0.2 秒、明らかな状況では妥当な選択になった。state は raw より要約のほうが正解の確率が高く、トークンも 4〜20 分の 1 |
| `GoalOverridePolicy`（危険時に Goal を上書き） | 生存行動を常に候補に入れる | Goal を上書きする仕組みを持たず、Jev の選択に任せた |
| `IJevDecisionEngine` / `JevDecision` | `IActionSelector` / `ActionDecision` | Jev 固有の型を application に出さない。行動選択という役割で名付けた |
| Goal の step 文字列を Bridge 側の語彙に変換 | ブリッジが実行可能なアクション ID を列挙し、それを選ぶ | 変換テーブルが不要になり、不可能な行動を選べない |
| Goal の完了を LLM やアクション結果で判断 | `HouseProject` が観測から判定 | LLM の自己申告や「成功」の戻り値は、世界の状態と一致しないことがある |
| `main.py` の Composition Root、`MinecraftBridgeSettings`、http/websocket の切り替え | `factories/game.py`、`MinecraftSettings`、HTTP のみ | 他の機能と同じ factories 構成に揃えた。未使用の選択肢は作らない |
| 家は固定の形 | LLM が設計し、`HouseBlueprint` で検証 | 配信者らしさを出す。範囲は実測で建てきれた寸法に限る |

## 12. 今後の課題

- **反射層（Reactive Loop）**: 今の反射は「被弾したら中断」だけで、殴られる前に動くことはできない。ステップの合間（LLM・Jev の待ち時間）も無防備。当初計画の Reactive Loop（短周期で脅威を判定して先に動く）を、コード側の反射として実装する。素手では戦闘がほぼ成立しないので、木の剣のクラフトも必要
- **実況・TTS との接続**: `GoalSetEvent` / `GameActionExecutedEvent` / `HouseCompletedEvent` を Phase 3 の実況生成と Phase 2 の TTS につなぐ（Phase 8）
- **Goal とアクションの追加**: 採掘、夜の過ごし方（ベッド、松明）、食料の確保など。追加するときは、ブリッジの実行手段と `GOAL_ACTIONS` と達成条件をセットで足す
- **Twitch**: 視聴者のコメントで Goal や設計に介入する（Phase 4 / 8）
- **エラーからの回復**: 今は例外で実行が終わる。LLM・Jev の一時的な失敗で止めない方針を決める
- **ブリッジの自動テスト**: Node 側のテストがない

## 13. 既知の問題（未修正）

- すでに達成済みの Goal も選択肢から除外していない
- 完成済みの `HouseProject` で `AdvanceHouseProjectUseCase.execute` を再び呼ぶと、`HouseCompletedEvent` がまた発行される
- `config/default.yaml` の `game:` セクションは当初計画の名残で、読むコードがない
