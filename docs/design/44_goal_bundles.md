# 設計書 44: 小目標の束（同時に追う複数の小目標）

> 語彙は 50（目標の 4 層）で入れ替わった: 旧「大目標」= 最終目標（`Mission`）、旧「中目標」= 大目標（`Objective`。旧 `MidGoal`）、旧「手順」（`plan_steps`）= 中目標（`Job`。旧 `PlannedStep`）。この文書の本文は書いた時の語彙のまま。

状態: 承認（2026-09-27）。束は 4 個まで、ほかの中目標の条件も入れてよい、束の条件が 1 つ済んだら話す

## 1. きっかけ

ユーザー（2026-09-27）:「小目標は複数持てるようにして、効率的に全て達成できるように Gemini がプランを練ってほしい」。形は「同時に追う束」を選んだ（Gemini が進め方を書き、道具モードでは束全体を見て毎手選び、進みは束の残りの合計で測り、全部満たしたら終わる）。

今の仕組み:
- 小目標は常に 1 つ（`Goal.spec`）。ブリッジの `PUT /goal` も 1 つの spec を評価する
- 中目標の手順（`MidGoal.plan_steps`、1〜6 個）は Gemini が書くが、1 つずつ順に（Jev が選んで）追う
- ほかの中目標の物は `also` で、8 m 以内にあるときだけついでに取る（進みには数えない）

困ること: 「原木 16」と「丸石 8」と「苗木 4」を別々の小目標にすると、森と石の所を 2 往復する。途中で見えた石は `also` に入っていなければ取らない。1 つ終わるたびに切れ目の判断（Jev / Gemini）と実況が入る。

## 2. 束

小目標を「束」にする: 先頭の条件（lead）と、一緒に追う条件（together、0〜3 個、合わせて 4 個まで）と、進め方（approach、1 文）。

- 束に入れられるのは世界から判定できる条件（`CONDITION_PREDICATES`: have / stored / placed / built / lit / planted / farmed / leveled / custom）だけ。身を守る小目標（夜、家、戦う）と探検（explored）は今までどおり 1 つで追う（互いにぶつかる: 家にいる と 外で集める）
- 束の中身は、一番上の中目標の手順でも、ほかの中目標の条件・手順でもよい（1 回の遠出で、2 つの中目標の物を集める）。小目標の `mid_goal_id` は今の一番上の中目標のまま
- lead は束を決めたときにまだ満たしていない最初の条件。小目標の間は変えない（済んだ条件は束の中で「済み」になるだけ）。Jev が束の手順を選ぶときは、済んだ条件を外して、残りの最初を lead にする。今 lead を見ている所（失敗の分析の「同じ種類」、教訓の状況のキー、目標ボードの吹き出し）はそのまま lead を見る

`Goal` に `together: tuple[GoalSpec, ...]` と `approach: str` を足す（`spec` は lead）。リリース前なので古い形は残さない。

## 3. Gemini がプランを練る

中目標の手順を「束の並び」にする: `PlannedStep` に `together` と `approach` を足す。Gemini の目標の決定（手順を書くとき）は、手順をまとめる:
- 同じ場所・同じ道具・同じ遠出で済むものは 1 つの束に（例:「北の森へ: 原木 16・苗木 4、途中の石も」→ lead have(log, 16)、together planted/have(sapling, 4)、have(cobblestone, 8)、approach「森へ行く道で見えた石を掘り、森で木を切りながら葉を壊して苗木を集める」）
- 順番が要るもの（作業台 → ツルハシ → 石）は別の束か、束の中で approach に順番を書く
- ほかの中目標の物も、同じ遠出で取れるなら入れてよい
- `review`（設計書 31）で、今の束より無駄のないまとめ方があれば書き直す

Jev の選択（`GoalChooser`）は今までどおり、まだ済んでいない手順（＝束）と身を守る小目標から 1 つ選ぶ。束の中身は Gemini が決めたまま（Jev は束を組まない）。

## 4. ブリッジ

`PUT /goal {…lead…, together: [spec…], also, keep}`:
- 束の条件をそれぞれ `validGoal` で確かめる（1 つでも不正なら 400。NotYet は外して lines に理由）
- `evaluate` は条件ごとに評価してまとめる: met は全部、remaining は合計、lines は条件ごと（`[2/4] have(cobblestone, 8): 3/8` の形）、blocked と leaves は合わせる（同じ候補は 1 つ）
- 候補（candidates モード）は束全体の葉から出る。どの条件のための候補かを候補に付ける（`for: have(cobblestone, 8)`）
- `also` はそのまま（束に入っていない物をついでに取る）。束の条件の物は `also` から外す

## 5. Python

- 目標の決定のスキーマ: 小目標に `together`（0〜3、条件の述語だけ）と `approach`、手順（`steps`）の各要素にも同じもの
- `PlaySession`: 済んだかは束全体。進まない（stalled）は束の残りの合計で見る。予算は 40 + 束の追加 1 つにつき 20 歩
- 道具のプロンプトと `activity()`: 小目標の欄に束の条件と、それぞれの今の状態（ブリッジの lines）と approach。Jev の状態にも束の条件と進み
- 失敗の分析（設計書 27）: 条件ごとの状態（`[i/n] label: N left`）がブリッジの lines に出るので、進まない条件を名指しできる。外すのは新しい束を書くこと（remedy change / retry の決定で together を書き直す）で、別の remedy は足さない
- 行き詰まりの確認（Jev）と教訓は lead を使う
- 目標ボード: 吹き出しに lead と「ほか 2 つ」、束の各条件の進みを小さく
- 実況: 束の条件が 1 つ済んだら（lead が変わったら）切れ目の実況と同じく話す（Narrator。見どころではない）

## 6. 作業の量と順番

Python で `Goal.spec` を読む所が 31、ブリッジで `state.goal` を読む所が 48 ある。lead を `spec` のまま残すので、多くはそのまま動く。変えるのは: ブリッジの `makeGoal` / `evaluate` / `publicStatus` / 候補、Python の `Goal`・`PlannedStep`・スキーマ・`PlaySession` の終わりの判定・プロンプト・ボード・Narrator。

1. ブリッジ: 束の評価とまとめ（テスト）
2. Python の domain とスキーマ、`PlaySession` の判定（テスト）
3. プロンプト（目標の決定の決まり、道具、Jev）、ボード、実況
4. 本物の Gemini で手順の束を書かせて見る。実機（家から 300 m 以上離れた所）で 1 つの束を最後まで

## 7. 決めたこと（ユーザー、2026-09-27）

- 束の大きさ: 4 個まで（lead + 3）
- ほかの中目標の条件も束に入れてよい
- 束の条件が 1 つ済んだら話す（切れ目の実況と同じ扱い）

## 8. 実装（2026-09-27）

- ブリッジ: `goals.mjs` の `together()`（形を確かめ、NotYet は外して `togetherSkipped`）、`describeSpec`、`evaluate` が束をまとめる（`members`、`[i/n]` の lines、葉に `for`）。`candidates.mjs` は葉の `for` を候補に付ける。`index.mjs` の `publicStatus` に `members`
- Python の domain: `Goal.together` / `approach` / `specs` / `describe()`、`PlannedStep` も同じ、`check_bundle`（4 個まで、`CONDITION_PREDICATES`、重複なし）、`GoalStatus.members`（`BundleMember`）、`GoalMemberMetEvent`、`PlaySession.step_budget`（40 + 20 × together）と `newly_met_members`
- スキーマとパース: `goal_vocabulary` の `_bundle_properties`（小目標と手順の各要素）、`parse_bundle`
- `AdvancePlayUseCase`: 両方の `set_goal` に `together`、`_also` は束の物を外す、毎ステップ `_members_met` → `GoalMemberMetEvent`。手順の今の状態（31）と教訓の手がかりは束の条件ごと
- `GoalChooser`: 手順の条件を全部 `/check` し、まだの条件が残る手順だけ出す（済んだ条件は外して渡す）
- `Narrator.on_member_met`: 切れ目と同じく（gate あり）話す
- 見え方: `activity()` の小目標の欄に束の形・条件ごとの行・進め方、中目標の手順は `[a + b]`、Jev の状態に `approach`、ボードの `label` に「（ほか N つ）」と `together`
- 保存: `data/mission.json` の `plan_steps` に `together` / `approach`
- プロンプト: 目標の決定の決まりに「束」の項（`TestPromptLength` の上限を 8000 に）

## 9. 束を見直す（2026-09-27）

ユーザー:「束は見直しで不定期に見直すようにしてほしい」。Gemini の目標の決定（見直し。手順がないとき、失敗、手順が尽きたとき、中目標の不進行、20 分ごと、と不定期に来る）のたびに、束も見直す。
- 見直し（設計書 31）の決まりに「束を見直す（毎回）」: まだの手順とほかの中目標の条件で、同じ場所・同じ外出で取れるものは束にして steps を書き直す。合わなくなった束はほどく
- スキーマ: `review` の次に必須の `trips`（残りを外出ごとに分ける。手順と小目標はこれに従う）。ログの「見直し」の行に出す
- 小目標を束にしたのに手順を別々に書いたら、コードがそれらを 1 つの束の手順にまとめる（`_steps_with_the_bundle`。次に Jev が手順から選ぶとき束がばらけない）
- 実測（本物の Gemini、石が北 30 m・森が北 60 m）: 決まりだけでは 3 回中 0 回 → 1 回、4 回中 1〜3 回とばらついた（review に「まとめて」と書いても手順は別々）。`trips` を足して 5 回中 5 回。石が南 70 m・森が北 70 m では 4 回とも束にしない（原木と苗木だけ束にした回が 1 回）
