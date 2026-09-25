# A: Gemini が道具で操作し、Jev が Gemini の質問に高頻度で答える（最初の版）

作成: 2026-09-25。19 §8（A）の詳細と、19 §11（Jev の役）の最初の実装。ユーザーの指示「その方針で一度設計・実装してみて」（19 §13 の 9・11・12 を承認）。**実装済み（§10）。実機（Gemini・Jev・Minecraft）ではまだ動かしていない。**

## 1. この版でやること・やらないこと

やること:
- ブリッジに道具の API（`POST /tool`）、共通の状態（`GET /state`）、中断（`POST /abort`）
- Gemini が 1 ステップに 1 つ道具を呼ぶ（function calling）。行動の道具には「今やろうとしていること」（`intent`）が必須で、見張りの質問（`watch`）を最大 3 つ添えられる
- 道具の実行中、1 秒ごとに Jev に質問をまとめて聞く: コードの 1 問（進んでいるか）と、Gemini が添えた質問
- 用途ごとの考える深さ（19 §7）
- 設定 `minecraft.agent.control: candidates | tools`。既定は `candidates`（今の構成。比べる相手として残す）

やらないこと（次の版以降）:
- 一般の完了条件と建てる道具（`blocks` / `build`、19 §10.3）。**この版の小目標は、今の述語のまま**（Gemini は今までどおり述語で小目標を決め、その小目標のために道具を選ぶ。完了はブリッジが述語で判定する）
- B（技、設計書 22）
- 大目標の見直し（設計書 20）
- 道具の好みの引数（足場のブロックなど。19 §10.5）の大半
- コメントの仕分け
- 夜の決まりを Gemini が破れるようにすること（19 §13 の 10 は返事待ち。**この版は今の決まり（a）: 家の中にいる夜は、外に出る道具を理由つきで断る**）

## 2. 1 ステップの流れ（`control: tools`）

```
観測（/observe）→ 小目標の切れ目なら今までどおり小目標を決める（述語、中目標の編集、メモ）
  → Gemini: 道具を 1 つ選ぶ（小目標、進み具合、ソルバーの提案、まわりの形、直近の道具の記録を見る）
  → ブリッジ: 道具を実行（/tool）
      ‖ 並行: 見張り（1 秒ごとに /state → Jev にまとめて質問 → 止める・起こす・終わったかも）
  → 結果を記録（直近の道具の記録、PlaySession の意図、停滞・予算の数え方は今までどおり）
```

- Gemini の呼び出しは毎回独立（会話の履歴を持ち越さない）。プロンプトに直近の道具の記録（最大 8 件）を入れる。入力が際限なく伸びない（19 §10.6）
- 小目標の述語・停滞・予算・時間帯・中目標の判定は今のまま動く（`PlaySession.track_progress` / `needs_new_goal` / `record` / `step_counted`）

## 3. 道具（ブリッジ `tools.mjs`）

行動の道具は、候補（`/act`）と同じ実行の経路 `run()` を通る: 反射・`busy`・時間の上限・被弾での中断・家を出る処理・履歴・保存。引数はそのまま信じず、世界と知識から導き、存在と距離を確かめる。

| 道具 | 引数 | 中身 |
|---|---|---|
| `do_suggestion` | `id` | ソルバーの候補（`/observe` の candidates）をそのまま実行する。ソルバーは「従わなくてよい提案」になる |
| `goto` | `x, y, z, range?` | 経路で向かう。48m より遠ければ 1 回 48m |
| `dig` | `x, y, z` | そのブロックを掘って拾う。**今の家と建てている家は断る。前の家は掘れる**（town4d のベッド） |
| `place` | `item, x, y, z` | そこに置く（隣に面がいる）。今の家と建てている家の中は断る |
| `craft` | `item, times` | 作業台が要るかはレシピから決める |
| `pickup` | — | 一番近い落とし物 |
| `attack` / `flee` | `entity`（`/state` の mobs の id） | |
| `eat` / `equip` | `item` | 持っているものだけ |
| `smelt` | `input, count` | 覚えているかまど・燃料は持ち物から選ぶ |
| `deposit` / `withdraw` | `item, count` | 家のチェスト |
| `go_home` / `sleep` / `build_next` | — | 今の複合の行動（家に帰る、寝る、設計図のブロックを 1 つ置く） |
| `wait` | — | 10 秒ほど待つ（家の中ならドアを向いて） |
| `find_blocks` | `block, radius?` | 近い順に最大 8 か所（調べるだけ。すぐ返る） |
| `recipe_of` | `item` | レシピ（材料、作業台が要るか） |
| `how_to_get` | `item, count` | ソルバーの木（何が足りないか） |

安全の制約（コード。断るときは理由を返す）:
- 夜（またはドアの前に敵がいる）に家の中にいるなら、外に出る道具を断る（今の `sheltering` と同じ判定）
- 反射が動いている間、実行中の間は断る

## 4. 共通の状態（ブリッジ `GET /state`）

Jev の質問と、Gemini の道具の選択の両方が見る 1 つの書式（19 §11.2）。要約だけ。

- `action`: 実行中の道具（なければ null）: `verb`, `target`, `elapsed_s`, `limit_s`, `moved_1s_m`, `moved_5s_m`, `target_distance_m`（始めた時・今）, `path`（動いているか、経路が見つからない回数）, `position_resets`（サーバーに位置を戻された回数。当たり判定の不具合の目印）, `digging`（ブロックと進み）, `items_gained`, `health_change`
- `self`: 位置、体力、満腹度、持っている物、水の中か、家の中か
- `surroundings`: まわりの形（`look_around`）。足元から半径 3 の各列の「立てる高さ」を足元との差で表した格子と、水・溶岩・読み込まれていない所の印、頭の上がふさがっているか
- `mobs`: 近い順に 5 体（id、名前、敵か、距離、方角、高さの差）
- `needs`: 欲求（今の `needs`）
- `time`

## 5. 見張り（Python `ToolWatcher`）

- 道具を始めてから 2 秒は聞かない。そのあと 1 秒ごとに `/state` を読み、有効な質問をまとめて **1 回** Jev に聞く（前の呼び出しが終わっていなければその回は飛ばす）
- 質問:
  - コードの 1 問「この行動は進んでいるか」（`Noul`）。**記録だけ**（19 §13 の 4: 規則に勝つまで止めない）。設定で止めるようにできる
  - Gemini が添えた質問（`Noul`、`on_yes` が `stop` / `wake` / `maybe_done`）。確信度 0.7 以上の「はい」が 2 回続いたら、ブリッジに中断を頼む。結果に理由が入る（`stopped by watch: …` / `woke up: …` / `maybe done: …`）。設定で記録だけにできる
- 道具が返ったら見張りも終わる。`/abort` は、もう終わった行動には何もしない
- **Jev は完了を判定しない**。`maybe_done` は止めて次のステップに回すだけで、完了はブリッジが述語で判定する
- 質問を作れるのは Gemini のループの道具の呼び出しだけ（チャットの返答からは作れない）
- 記録: ティックごとの状態、質問、答え、確信度、入力トークン、所要時間を `logs/watch/*.jsonl` に（19 §6 の評価と費用の比較の材料）

## 6. Gemini の道具の選択

- `ITextGenerator.choose_tool(prompt, tools, system_instruction, purpose)` → `ToolCall(name, args)`。Gemini のアダプターは function calling（`mode: ANY` で必ず 1 つ呼ばせる）。Gemini の型はアダプターに閉じ込める
- 行動の道具の引数に `intent`（必須、今やろうとしていること 1 文）と `watch`（任意、最大 3、`question` 80 字まで、`on_yes`）
- `intent` は `PlaySession` に入り、`activity()` に「今やろうとしていること」として出る。実況と返答が同じものを見る（12 の一致）
- プロンプト: 小目標と進み具合（ソルバーの木）、ソルバーの提案（上位 12）、状況（今の書式）、まわりの形、直近の道具の記録、質問の書き方の決まり（19 §11.3: 中立に書く、急ぎ・優先の言葉を書かない）
- 考える深さ: 普段は `tool`（low）、前の道具が見張りに止められた・失敗した後は `tool_after_failure`（medium）

## 7. 考える深さ（19 §7）

`gemini.thinking_levels`（用途 → low / medium / high、`default` 必須）。起動時に全部の値を検査する。`ITextGenerator` の 3 つのメソッドが `purpose` を受け取る。ゲームと会話の 2 つの生成器の両方に同じ表を渡す。

| 用途 | 既定 |
|---|---|
| `default` | low |
| `town`, `site` | high |
| `house`, `goal_after_failure`, `tool_after_failure` | medium |
| `goal`, `tool`, `commentary`, `reply` | low |

## 8. 構成

| 層 | 追加・変更 |
|---|---|
| domain | `ToolCall`、`WatchQuestion`（`on_yes`）、`FastQuestion` / `FastAnswer`、`ToolOutcome`。`PlaySession.intent`、`Activity.intent` |
| application | ポート `IFastJudge`、`IWatchRecorder`、`IMinecraftBridge.run_tool / state / abort`、`ITextGenerator.choose_tool` と `purpose`。`tool_catalog.py`（道具の定義。モデルに依存しない JSON Schema）、`ToolWatcher`、`AdvancePlayUseCase` の行動の部分を `control` で切り替える |
| infrastructure | `GeminiTextGenerator.choose_tool` と用途の表、`JevFastJudge`、`MineflayerBridgeClient` の 3 つ、`JsonlWatchRecorder`、道具のプロンプト、設定 |
| bridge | `run()`（`/act` と `/tool` の共通の実行）、`tools.mjs`、`state.mjs`（共通の状態と `look_around`、行動の進み具合）、`/tool` `/state` `/abort` |

## 9. 検証

- ここでできること: ブリッジの `npm test`（道具の検証と断り、夜の決まり、`look_around`、中断の競合）、Python の単体テスト（見張りの規則、道具のステップ、アダプターの変換、設定）
- ユーザーの環境で: `examples/integration_test_minecraft.py --control tools`。見ること: Gemini が道具をどう選ぶか、書かれる質問、Jev の答えと誤検知、遅延、1 ステップあたりの Gemini と Jev の呼び出し回数とトークン

## 10. 実装（2026-09-25）

| 場所 | 中身 |
|---|---|
| bridge `runner.mjs` | `run(c, label)`: `/act` と `/tool` の共通の実行の経路（反射・busy・時間の上限・被弾での中断・家を出る・履歴・保存）。`abortCurrent`（終わった行動には何もしない） |
| bridge `progress.mjs` | 実行中の進み具合（動いた距離 1 秒・5 秒・全体、目標までの距離、経路の更新と見つからない回数、`forcedMove` の回数、掘っているブロック、持ち物と体力の変化） |
| bridge `state.mjs` | 共通の状態と `lookAround`（半径 3 の格子。セルは足元から見た立てる高さの差、`#` 高い壁、`v` 深い穴、`~` 水、`!` 溶岩、`?` 未読み込み） |
| bridge `tools.mjs` | 道具（§3）。避難中は外に出る道具を断る（昼にドアの前の敵と戦うのは許す）。今の家・建てている家は掘らない・置かない（`protectedReason`）。前の家は掘れる。知識と合わない引数も理由つきで断る（プレイを止めない） |
| bridge `primitives.mjs` | `goto_pos`、`place_at` を追加 |
| Python domain | `ToolCall`、`WatchQuestion`、`WatchAction`、`ToolOutcome`、`FastQuestion` / `FastAnswer` / `FastVerdict`、`PlaySession.intent`、`Activity.intent` |
| Python application | `tool_catalog.py`（道具の JSON Schema、`parse_tool_call`）、`watcher.py`（`ToolWatcher`、`WatchPolicy`）、`AdvancePlayUseCase(control, tool_watcher)`、ポート `IFastJudge` / `IWatchRecorder`、`ITextGenerator.choose_tool` と `purpose` |
| Python infrastructure | `GeminiTextGenerator.choose_tool` と用途の表、`JevFastJudge`、`JsonlWatchRecorder`、`build_tool_prompt`、`stream_context` の「今やろうとしていること」、設定 |
| example | `examples/integration_test_minecraft.py --control tools` |

確かめたこと:
- `npm test` 113 件、`pytest tests/` 470 件
- 模擬サーバーで Gemini への実際のリクエスト本文を見た: `toolConfig.functionCallingConfig.mode = ANY`、用途の `thinkingConfig`（例 `tool_after_failure` → MEDIUM）、20 個の関数宣言。SDK は宣言の中を `parameters_json_schema` と snake_case で送る（`thinking_level` と同じ。実 API が受け付けるかは未確認）

まだ確かめていないこと（ユーザーの環境で）:
- Gemini が道具をどう選ぶか（まわりの格子を読めるか、ソルバーの提案とどう使い分けるか）、1 ステップの遅延
- Gemini が書く見張りの質問と、それへの Jev の答え（誤検知）。英語の質問と要約した状態で答えられるか
- 1 ステップあたりの Gemini と Jev の呼び出し回数とトークン（`logs/watch/*.jsonl`）
- `goto_pos` / `place_at` の実機での動き（穴から土を積んで出られるか）

