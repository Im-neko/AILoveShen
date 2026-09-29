# 目標の 4 層（最終目標・大目標・中目標・小目標）

作成: 2026-09-29。`13_goal_hierarchy.md`（目標の 3 層）と `26_steps_and_context.md`（手順 `plan_steps`）を置き換える。状態: 段階 2（仕組み）は実装済み（§11）、段階 3（改名と語彙）も実装済み（§12）。

## 1. 目的

ユーザーの依頼（2026-09-29）:
- 大目標は基本的に変わらないので、最終目標にする。代わりに大目標を直近の目指すもの（資材保管庫の建設など）にする
- 中目標を資材の確保や施設の建設などの行動の単位、小目標を「〇〇まで移動」「ベッドで寝る」のような具体的な動作を表すものにしたい
- 決めたこと: 両方（挙動の改善と、配信での分かりやすさ）が動機。大目標は最大 6 個を並べ、入れ替えられる。基盤整備は大目標の 1 つ。視聴者の頼みは大きさで層を分ける

今の問題（13・26 の実装）:
- 中目標のリストに、粒度の違うものが平らに並ぶ（「保管庫を建てる」「丸太を 64 個集める」「農場」）。手順（`plan_steps`）は中目標の内部に隠れ、進み具合・状態・停滞が中目標の単位でしか見えない
- 配信では「今なにをしていて、なぜか」が 1 本で辿れない

## 2. 4 層

| 層 | 型（改名後） | 問い | 完了の形 | 決める | 見直し |
|---|---|---|---|---|---|
| 最終目標 | `Mission`（型名は変えない） | なぜ | 終わらない。設定で決め、配信中は変わらない | 設定 | しない |
| 大目標 | `Objective`（今の `MidGoal`） | 何を成し遂げるか | 成果物が世界にある、または基盤が整っている（`/check`） | Gemini（追加・入れ替え・断念）。最大 6 個 | 進まなければ Gemini が後回しか断念 |
| 中目標 | `Job`（今の `PlannedStep`） | どの仕事で成し遂げるか | 資源・配置・整地などの状態（`/check`）。一度満たしたら済み（ラッチ） | Gemini が大目標ごとに書く | 進まなければ Gemini が書き直す |
| 小目標 | `Goal`（変えない） | 今の 1 回の遠征 | 1 回の遠征で届く状態述語 | Jev（既定）、Gemini | 詰まれば Jev が別の手 |
| （行動） | tool / primitive | 動詞 | 秒 | Jev / Gemini の道具 | 反射 |

- 最終目標は日本語の呼び名だけ変える。コードの `Mission`、設定 `minecraft.mission`、`data/mission.json` の `mission` は変えない（改名の対象を増やさない）。今の設計書・プロンプト・オーバーレイが「大目標」と呼んでいるのは最終目標のことなので、語彙の入れ替えは §9 の 1 回で行う
- 「〇〇まで移動」「ベッドで寝る」は動詞で、行動層（`goto` / `sleep_at`）にある。小目標は状態述語のまま（`at_home`、`through_night`）。オーバーレイに動作の言葉を出すのは、今の日本語 `label` と `intent` が受け持つ
- 「なぜ」のはしご: 小目標は中目標のため、中目標は大目標のため、大目標は最終目標のため。生存の小目標だけは中目標に属さない（13 §2 の例外のまま）

## 3. 大目標

- 種類（`kind`）はコードが条件から決める。Gemini は書かない（弱いモデルの書く項目を増やさない）
  | kind | 条件に使える述語 | 例 |
  |---|---|---|
  | `build` | `built(name)` だけ | 資材保管庫を建てる |
  | `facility` | `facility(name)` だけ | 小麦畑を作る |
  | `infrastructure` | `have` / `placed` / `stored` / `planted` / `farmed` / `leveled` / `lit` / `custom` | 石のツルハシと松明を持つ、食料を蓄える |
- `build` と `facility` の大目標は、資材・整地・内装を条件に書けない（中目標に置く）。混ぜた条件は差し戻す: 「材料は中目標に書く」
- 街の段階（`stage`、`prepares_town`）は大目標の属性のまま。段階の条件は段階の定義が決めるので、`kind` の検査は受けない（混ざった条件は `infrastructure` として扱う）。断念できないのも今と同じ
- 視聴者の頼みは、大きいもの（自分の名前を持つ建物・施設）を大目標として、待ち行列の後ろに入れる（13 §4 のとおり、今取り組んでいるものより後ろ。`when: now` の例外も同じ）。小さいものは §5
- 上限（コードが守る）: 大目標 6 個、視聴者が出どころのもの同時 2 人、1 人 1 件（§5 の中目標と合わせて数える）、予算 80 ステップ。助言（`suggested_by`）は数えない（49）

## 4. 中目標

- 中目標は大目標ごとのリスト（1〜6 個）。条件 1 つ + `together`（44）+ `approach`。id（`j1` …）、状態（`pending` / `done` / `dropped`）、出どころ（配信者 / 視聴者 / 助言）、理由を持つ
- **ラッチ**: `/check` で一度満たしたら `done` のまま（13 §3 の `have` と同じ扱い）。次の手順で材料を使って未達成に戻っても、済みのまま。今は `GoalChooser` が毎回 `/check` で判定し直している
  - `_check_step_order`（後の手順が済んで前が未達成なら差し戻す）は、ラッチした状態で比べる
- **書き直し**: Gemini が出す中目標のリストは「これから残す未達成のもの」。コードは既存のものと同じ条件（`same_as`）を探して id と出どころを引き継ぎ、済み（`done`）と視聴者・助言の中目標は、断念に理由が書かれない限り消さない
- 上限: 大目標ごとに未達成 8 個（Gemini の 6 個 + 視聴者・助言の分）
- 進み具合と停滞
  - 大目標の進み具合: 成果物の大目標は `/check` の数え（ブロック 30/70）、`infrastructure` は済んだ中目標 / 全体（断念を除く）。中目標を足すと分母が増えて戻って見えることがある。書き直しだけでは変わらない（テストで確かめる）
  - 停滞: 中目標が 1 つ済む、または今の中目標の `(n/m)` が変わるとき進んだとする。`mid_goal_stall_steps`（60）を中目標の単位で数える。書き直しは数え直さない
  - 停滞の文言（`MID_STALLED`）の文字列判定（`lessons.py`、`play.py` 3 か所）は同時にそろえる

## 5. 視聴者の頼みの層

返答の `request` に、頼みの層を持たせる。

| 頼み | 層 |
|---|---|
| 自分の名前を持つ建物・施設 | 大目標（`build` / `facility`） |
| 今の大目標の材料・一歩になるもの（「もっと丸太を」） | 今の大目標の中目標（`serves: current`） |
| 今の大目標と関係のない小さなもの（「松明を置いて」） | 小さな `infrastructure` の大目標（後ろに入る） |

- 13 §4 の上限（1 人 1 件、同時 2 人、今のものの後ろ）は、大目標と中目標を合わせて数える。中目標だけ数えないと、同じ人の連投が中目標に際限なく入る（13 §9 の連投の確かめが崩れる）
- 予算 80 ステップは、その頼みの小目標に使ったステップを、中目標なら頼みの中目標に、大目標なら頼みの大目標に数える
- ユーザーの決めた「小さいものは今の大目標の中目標」を、なぜ・はしごに合わせて「今の大目標に関係するものだけ」に絞った。関係のない小さな頼みを中目標にすると、なぜが辿れなくなる。異論があれば戻す

## 6. 小目標の選び方

- Jev（`GoalChooser`）は、今の大目標の未達成の中目標と生存の選択肢から選ぶ（今と同じ形。手順が中目標になる）
- 大目標が入れ替わっても、外れた大目標の中目標は保つ。戻ったときに書き直さない

## 7. 保存とオーバーレイ

- `data/mission.json` は移行しない（リリース前）。保存に `format`（版。今は 2）を持たせ、版が違う・壊れている・今の検査に通らないファイルは、例外にせず設定の初期リストで始める（今の `_load_plan` の、最終目標の文が違うときの経路と同じ）。次の保存で上書きされるので、読めなかったファイルは `mission.json.unreadable` に写して残す。今のファイルは着手前に `data/backups/` にも退避しておく
- `GET /api/goals` とオーバーレイ: 最終目標 → 大目標のリスト（進み具合、視聴者の頼み）→ 今の大目標の中目標（済み・今・待ち）→ 小目標（吹き出し）。中目標が済むとトースト
- `Narrator`: 大目標の完了・断念（今の `MidGoal*Event`）に加えて、中目標の完了を次の実況に混ぜる（`JobDoneEvent`）。小さな中目標の完了ごとには話さず、大目標の境目と長い中目標の完了だけ話す（`JobDoneEvent`: `ObjectiveKeeper` が小目標の切れ目の判定で済みにしたときに 1 回だけ出す。`Narrator` は取っておいて次の切れ目に話し、話すかどうかは間合いの判定に任せる。強制的に話すのは大目標の完了・断念だけ。視聴者の手順の断念は、大目標と同じ `ObjectiveDroppedEvent` で必ず言う）

## 8. 段階

1 つの変更で意味の変更と改名を混ぜない。

1. この設計書（コードは触らない）
2. **仕組み**（実装済み。§11）（今の型名のまま）: `PlannedStep` に id・状態・出どころ、ラッチ、書き直しのマージ、進み具合と停滞の単位、上限。`kind` の検査。頼みの層。保存の形。テスト
3. **改名と語彙**（実装済み。§12）（1 回で）: `MidGoal` → `Objective`、`PlannedStep` → `Job`、`MidGoalPlan` → `ObjectivePlan`、`MidGoalKeeper` → `ObjectiveKeeper`、イベント、`mid_goal_id` → `objective_id`、プロンプトと日本語の表示、ブリッジのエラー文（Gemini が読む）、`abilities.yaml`、設計書 13・26・20 と CLAUDE.md。`MidGoal.steps`（予算の数）は `plan_steps` と別物なので、名前がぶつからないように `budget_used` にする

## 9. 確かめること

- 単体テスト: `kind` の検査、ラッチと `_check_step_order`、書き直しのマージ（済みと視聴者の中目標が残る）、上限（合算）、停滞（中目標の単位）、進み具合（書き直しで戻らない）、保存の往復と読めない形の始め直し
- 台本のコメント（13 §9 の 4 つ + 49）: 連投、関係のない頼み、乗っ取り、「家ができたらやるね」、助言。中目標への頼みは 1 人 1 件を守る
- 実機: 大目標を入れ替えたあと、戻って中目標が続きから進むか。オーバーレイの階層表示

## 10. 範囲外

- 設計書 20（最終目標を Gemini が決め直す）。語彙だけ「最終目標」に合わせる
- 中目標の自動生成（ロードマップからの展開）
- 大目標の依存関係（大目標同士の順序は優先順位のみ）

## 11. 段階 2 の実装（2026-09-29）

型名は今のまま（`MidGoal`、`PlannedStep`）。改名は段階 3。この節は段階 2 の時点の名前と語彙で書いてある（今の名前は §12 の対応表）。

| 層 | 実装 |
|---|---|
| domain | `PlannedStep` に `id`（`j1` …）・`state`（`JobState`）・`requested_by` / `suggested_by`・`steps`（視聴者の手順が使った予算）・`progress`・`ended_because`。`MidGoal.kind`（`ObjectiveKind`、`objective_kind(conditions)` が混ぜた条件を `ValueError`）、`open_steps` / `done_steps`。`MidGoalPlan.set_steps`（マージ）、`add_step`、`judge_steps`（ラッチ）、`drop_step`、`viewer_step`、`viewers`、`check_room(job=)`、`charge(id, spec)`、`take_dropped_steps`、`next_step_id` |
| application | `MidGoalKeeper.judge` が一番上の中目標の未達成の手順を判定して済みにし、進み具合を保つ（`_judge_steps`）。`accept` は `proposal.job` なら `_accept_job`（今の手順のすぐ後ろで、先に頼まれた手順の後ろ。`now` は先頭。今の大目標を返す）、`withdraw` は手順も、`step_counted` は予算を超えた視聴者の手順の断念も伝える。`GoalChooser` は未達成の手順から選び、満たした手順は済みにする。`AdvancePlay`: `_check_step_order` は済みの手順を数えない、`_step_status` は済みを判定せず済みのまま見せる、決定の検証で書き直しが 8 個を超えるなら差し戻す、`_MidWatch`（停滞は手順が済む・条件の数が動く・今の手順の数が動くと進んだ。手順を書き直して今の手順が変わっただけでは数え直さない） |
| 語彙 | 返答の `level`（`objective` / `job`）→ `MidGoalProposal.job`。`parse_proposal` は大目標の混ぜた条件と、手順の束の形を、設計や判定の前に確かめる。約束（promise）の経路は `level` を持たず、いつも大目標 |
| infrastructure | `JsonMissionStore`（`format: 2`、手順の欄、`next_step_id`、読めない形は `None` にして `.unreadable` に写す）、`stream_context`（手順を 済: / 今: / 待: と出どころで）、プロンプト（手順の見方、`kind` の決まり、返答の `level`） |
| presentation | `/api/goals` の中目標に `kind`・`steps`（`id` / `label` / `state` done・current・waiting / 出どころ / `progress`）・`steps_done`・`steps_total`。2 つのオーバーレイに今の大目標の手順、条件の数がなければ済んだ手順 / 手順の数のバー |

設計書から変えたこと・決めたこと:
- 視聴者の手順の位置は「今の手順のすぐ後ろ」だと、後から頼んだ人が先の人を追い越すので、「先に頼まれた手順の後ろ」にした
- `drop_step` はコードの操作だけ（Gemini の決定のスキーマには `drop` の手順版を足していない）。視聴者と助言の手順は、大目標が終わるか、済むか、予算切れか、本人の取り下げでなくなる
- 手順の判定は `MidGoalKeeper.judge`（小目標の切れ目ごと、決定の前）が持つ。`GoalChooser` は同じ結果を見て、満たした手順は済みにする（判定できなかったときの保険）
- 返答が手順を足したとき、`GenerateResponseResponse.mid_goal` は今の大目標（手順ではない）
- 保存の版は `format` の 1 つ。手順の `id` の欠けなど、細かい形の食い違いも `KeyError` などで読めない形になり、同じ道で始め直す
- 台本のコメント（§9 の 4 つ）と実機は未確認

## 12. 段階 3 の実装（2026-09-29）

意味は変えずに、名前と語彙を 1 回で入れ替えた。旧「大目標」は最終目標、旧「中目標」は大目標、旧「手順」は中目標。

| 種類 | 旧 → 新 |
|---|---|
| 型 | `MidGoal` → `Objective`、`MidGoalState` → `ObjectiveState`、`MidGoalPlan` → `ObjectivePlan`、`MidGoalKeeper` → `ObjectiveKeeper`、`MidGoalProposal` → `ObjectiveProposal`、`MidGoal{Added,Completed,Dropped}Event` → `Objective*Event`、`PlannedStep` → `Job`。`Mission`、`minecraft.mission` は変えない |
| フィールド・メソッド | `mid_goal_id` → `objective_id`、`GoalSetEvent.mid_goal` → `objective`、`Activity.mid_goals` → `objectives`、`MidGoal.plan_steps` → `Objective.jobs`、`GoalDecision.steps` → `jobs`、予算の使用数 `MidGoal.steps` / `PlannedStep.steps` → `budget_used`、`set_steps` / `add_step` / `judge_steps` / `drop_step` / `viewer_step` / `take_dropped_steps` → `set_jobs` / `add_job` / `judge_jobs` / `drop_job` / `viewer_job` / `take_dropped_jobs`、`open_steps` / `done_steps` → `open_jobs` / `done_jobs`、`next_step_id` → `next_job_id`、`MAX_OPEN_STEPS` / `MAX_STEPS` → `MAX_OPEN_JOBS` / `MAX_JOBS` |
| 内部 | `_check_step_order` → `_check_job_order`、`_step_status` → `_job_status`（プロンプトの引数 `step_status` → `job_status`）、`_MidWatch` → `_ObjectiveWatch`、`MID_STALLED` → `OBJECTIVE_STALLED`（文言 `has made no progress` は同じ）、`_steps_with_the_bundle` → `_jobs_with_the_bundle`、`use_cases/mid_goals.py` → `use_cases/objectives.py` |
| 設定 | `minecraft.mission.mid_goals` / `max_mid_goals` / `max_viewer_mid_goals` → `objectives` / `max_objectives` / `max_viewer_objectives`、`minecraft.agent.mid_goal_stall_steps` → `objective_stall_steps`（行動の数なので `steps` のまま） |
| Gemini の決定 | スキーマの `steps` → `jobs`（`plan_changes` は大目標リストの編集なので名前のまま）、プロンプトの `$steps` → `$jobs` |
| 保存 | `data/mission.json` は `format: 3`。キーは `plan_steps` → `jobs`、`steps` → `budget_used`（大目標と中目標）、`next_step_id` → `next_job_id`。旧形式は読まず、最初の大目標で始め直す（元は `.unreadable` に写す） |
| 盤面 | `/api/goals` の `mid_goals` → `objectives`、`steps` / `steps_done` / `steps_total` → `jobs` / `jobs_done` / `jobs_total`。オーバーレイの要素 `mid` → `objective`、クラス `.step` → `.job`（街の段階の `.town .step` は別物で残す） |
| ログの印 | `[mid+]` / `[mid✓]` / `[mid×]` → `[objective+]` / `[objective✓]` / `[objective×]` |
| テスト | `test_job_steps.py` → `test_jobs.py`、`test_goal_board_steps.py` → `test_goal_board_jobs.py`、`test_json_mission_store_steps.py` → `test_json_mission_store_jobs.py`（中目標の意味の `step(s)` を持つ名前も `job(s)` に） |
| 表示・プロンプト | 日本語の「大目標」→「最終目標」、「中目標」→「大目標」、「手順」→「中目標」。目標の説明は 4 層（最終目標・大目標・中目標・小目標）。活動の書式は `- 最終目標:` / `- 大目標（上から順に取り組む）:` / ` 中目標: 済:… → 今:… → 待:…`。ブリッジの Gemini が読むエラー文も "mid goal" → "objective" |

意図して変えなかったもの:
- 「手順」がやり方の意味のもの（技の手順、やり方の帳の `how`、ソルバーの細かい手順、`recent_tools` の手順の手本）
- 小目標の「ステップ」（行動 1 回）を指す `step` / `steps`（`step_counted`、`objective_stall_steps`、`viewer_budget_steps`、`max_steps_per_goal`、`StepPicker` など）
- 設計書 13・26・20 ほか、書いた時点の語彙で書いた設計書の本文（冒頭に対応の注記を足した）と、WORKLOG の過去の記録

