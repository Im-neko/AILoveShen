# 169. 道具は毎回渡すものだけ。小目標で組を足し、残りは search_abilities で引く

> 2026-10-15: 道具の集合は全部入りで固定に戻した（設計書 177。定義が 1 つでも違うと Claude のキャッシュが全部外れ、前の組の出し入れでは 3 回に 1 回、約 8K トークンを書き直していた）。組・`search_abilities` で道具を足す仕組みはやめた。以下は当時の記録。

2026-10-11、user: 「あまり賢くないモデルを使う（gemma4 や qwen3.6、haiku もそんなに賢くはない）ので、プロンプトが長すぎても逆効果。これを機に内容も整理したい」「道具はまず検索ツールだけ渡しておき、検索結果からどのツールを使うかという何回かやりとりを重ねたい。一度に複数のことを指せるとたいていポンコツになる」→「その方針でいちど進めてください」
（同じ日の SYSTEM の共通化・世界の情報のブロック分けは、案 A/B が未決のため、ここでは扱わない）

## 1. 事実（`logs/generations`、`tools/prompt_budget.py`）
- 道具を選ぶ呼び出しは 7,640 回（8,106 回の道具の呼び出し）。使われた道具は上位 10 個で 83%、15 個で 91%。1 度も使われなかった道具が 7 個（`clear_site` `deposit` `flee` `recipe_of` `find_recipes` `uses_of` `water`）
- 渡した道具は 39 個（行き詰まりは 28 個で、組が違う）。入力は中央値 20,040 トークン。回帰（1 道具あたり）は約 230 トークン、道具を除く固定の分が約 2,900。**13,700 トークン＝道具の定義、は言い過ぎだった**（`prompt_budget` の「実測 − 節の推定」は、節の推定の誤差と固定の分も含む）。道具の定義は約 9,000（39 個）
- `search_abilities` が呼ばれたのは 19 回（0.2%）。賢くないモデルは、自分からは検索しない
- 1 ターンに 2 つ以上の道具を出したのは 474 回（6%）。`choose_tool` は 1 つ目だけを使う（`calls[0]`）ので、2 つ目は捨てられている。`parallel_tool_calls` は設定していない（バックエンドが未知の引数を断る恐れがあり、効果も小さいので触らない）
- 存在しない道具名の呼び出しが約 35 回（0.4%、`explore_north_placeholder` など）

## 2. 変更
- `tool_catalog.py` の `tool_specs` は、毎回渡す道具（`CORE_TOOLS`: `do_suggestion` `goto` `dig` `place` `craft` `equip` `wait` `go_home` `find_blocks` と検索の 4 つ）だけを返す。足すもの:
  - `TOOL_GROUPS`（`farm` `build` `store` `cook` `fight` `night` `water`）: **コードが**、小目標と状態で決める（`groups_for`）。小目標の述語（farmed → farm、facility → farm + build、stored → store、cleared → fight …）、have の物が精錬・料理（`ingot` `cooked` …）なら cook、敵が近い → fight、水の中 → water、夕方・夜 → night、拾える物がある → store、空腹 → cook
  - `search_abilities` が引いた項目の道具（`abilities.yaml` の `tools`）: **その小目標のあいだ**足す（小目標が変われば空）。引いた結果にも `tools` を載せ、足した道具を 1 行で返す（`tools you can call now: ...`）
  - `run_skill` は、覚えた技があるときだけ。`write_skill` と `inspect` / `diagnostics`、素手・面・スニークの `use_item` は、行き詰まり（`experiment`）か同じ手の繰り返しのときだけ
  - `ask_advisor` / `look_screen` は、使えるときだけ（従来どおり）
- 呼び出しの検証（`parse_tool_call`）は変えない: 渡していない道具名でも、カタログにある名前なら受ける
- 道具を選ぶプロンプトの決まりに 1 行: 一覧にない道具（畑・建築・精錬・チェスト・食べ物・レシピ）は search_abilities で引くと呼べる。`find_foods` の説明は消した（食べ物の項目から引ける）
- 道具の定義の上限（`test_context_budget.py`）を、場面ごとに分けて締め直した。推定で: ふだん 3,138（技あり）、advisor 付き 3,408、行き詰まり 4,557〜4,827、畑 4,962、全部 9,923。従来は 8,920〜10,100

## 3. 予想（実機で測る）
- 1 回の入力は、道具の分で約 5,000〜6,000 トークン減る（中央値 20,040 → 約 14,000〜15,000。回帰からの見積もり）
- 道具の選び間違いは、渡す道具が減るぶん減る。ただし「必要な道具が一覧にない」ことが新しく起きる。ふだんは `do_suggestion`（ソルバーの提案）が多くをまかなう。起きたかは、`search_abilities` の呼び出し回数と、受けた「渡していない道具」の呼び出し、`explore_north_placeholder` のような存在しない名前の数で見る
- 外れの目印: 小目標が `search_abilities` を何度も呼んで進まない、`till` や `build_next` を呼べず `do_suggestion` の言い換えで失敗が続く

## 4. 作っていないもの
- 道具を選ぶ 2 段階（どの道具か → その道具だけで引数）: 往復が倍になり、道具ごとにキャッシュが分かれる。コアと組で足りなければ検討する
- 道具の説明そのものの短縮（`place` 1,007 字、`use_item` 1,303 字など。事故の再発防止が入っている）。`watch` と `intent` が行動の道具ごとに付く分（約 190 字 × 約 20 個）も同じ
- SYSTEM の共通化、世界の情報（INFO）のブロック分け、`plan` `goal` の整理

## 5. テスト
`test_tool_control.py::TestToolGroups`（ふだんはコアだけ、小目標の組、状態の組、技の道具）、`TestToolStep`（`search_abilities` が足した道具が次の手と同じ小目標の次のステップに残り、小目標が変わると消える）、`test_context_budget.py`（場面ごとの上限）、`test_skills.py`（`write_skill` は書く場面だけ）。
