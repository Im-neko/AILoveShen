## Current Status

**Active Phase**: 設計書 47（施設と検索）の段階 1〜5 を実装した: 検索の道具（search_facility / search_memory / item_info / uses_of / search_abilities）、街の理想図（区画ごとの用途）、施設の設計と `facility(name)`（畑・植林場・採掘場）、作物と植林は施設で数える、進める手のない小目標の差し戻し。どれも実機では未確認。次はユーザーの環境でブリッジと Python を再起動して、`/town`、`GET /facilities`、`logs/search`、施設の設計（`purpose=facility_design`）を見ること。残りは囲い・トラップ（別の設計書）、畑の水、採掘場の坑道を延ばす・松明。ブランチ `claude/peaceful-edison-prr51v`
**Last Updated**: 2026-09-29
**Test Status**: `python -m pytest tests/` 885 passed（`test_stream.py::test_no_channel_no_chat_and_the_board_is_served` は除く）。ブリッジ `npm test` 271 件
**実機の状態**: ブリッジは `npm run dev`（ファイルの変更で再起動）が使える。道具モードと最近の機能（植林・畑、夜、道具の作り直し、動けないとき、Jev の 5 つ）は実機で試していない

## Completed Work

### 目標の 4 層 段階 2: 手順（中目標）を第一級に（設計書 50） (2026-09-29)

**Commit**: `eba4cd9`

ユーザーの依頼: 大目標を最終目標に、中目標を大目標に、手順（`plan_steps`）を中目標に。段階 2 は仕組みだけ（型名・呼び名は今のまま。改名は段階 3）。
- domain: `PlannedStep` に id・状態・出どころ・予算・進み具合、`MidGoal.kind`（`objective_kind`。built だけ / facility だけ / それ以外。混ぜると差し戻し。街の段階と準備は除く）、`MidGoalPlan` の `set_steps`（マージ）・`add_step`・`judge_steps`（ラッチ）・`drop_step`・`charge(id, spec)`。視聴者の上限は大目標と手順を合わせて数える
- application: `MidGoalKeeper.judge` が手順を判定して済みにする、`accept` の `job`、`withdraw` と予算切れの手順の断念、`GoalChooser` は未達成の手順から、`_check_step_order` は済みを数えない、停滞は手順単位（`_MidWatch`）、書き直しが 8 個を超えると差し戻し、返答の `level`
- 保存: `JsonMissionStore` の `format: 2`。読めない形は初期リストで始め、元を `mission.json.unreadable` に残す（**今の `data/mission.json` は次の起動で初期リストに戻る。着手前に `data/backups/` へ退避すること**）
- 表示: `stream_context`（済: / 今: / 待:）、`/api/goals` の `kind` と `steps`、2 つのオーバーレイ、プロンプト
- テスト: `test_job_steps.py`（21）、`test_goal_layers.py`（24）、`test_json_mission_store_steps.py`（4）、`test_goal_board_steps.py`（2）。Python 937 passed（`test_stream_factory.py::test_no_channel_no_chat_and_the_board_is_served` は除く）。ブリッジは触っていない（`npm test` はこのワークツリーでは未実行: node_modules がない）
- 未実装（設計書 50 §11）: `JobDoneEvent` の実況、Gemini の決定で手順を断念する操作、台本のコメントと実機での確認
- 次: 段階 3（改名と語彙）

### 判断のサイクルの遅さ（設計書 51） (2026-09-29)

**Commit**: なし（未コミット）

ユーザー:「判断のサイクルが遅い。時間がかかっている箇所を特定してほしい。単純に LLM が遅いなら仕方ない」→ 調べたあと「1・2 をやって、3 を調べて、実況は頻度を下げ、スタック中に似た発言を続けず、違う方法を考えるように」。
- 測定: 1 手 24.6 秒のうち LLM が約 80%（ブリッジの行動は 16%、Jev は 0.5%）。呼び出しの時間は入力の大きさでほぼ決まる（読み込み約 3,100 トークン/秒）。道具の選択の入力 1.6 万トークンの 7 割が道具の定義。FreeToken は同じプロンプトのときだけキャッシュが効き、部分一致では全部読み直す（`cached=0` が本番の全行）。設計書 36 の「先頭のキャッシュを複数持てる」は誤読だったので追記
- 道具の定義 11,337 → 6,839 トークン: `watch` を `"<on_yes>: <質問>"` の文字列にし、説明は道具の選択のプロンプトの「決まり」に 1 回だけ（`tool_catalog.py`、`game_prompt_template_builder.py`）
- 失敗を LLM に戻す前に防ぐ: 反射が動いている間は選ばせず待つ（`_wait_out_reflex`）、`pickup_ready`（`GET /state`）で `pickup` を出さない、`around.mjs` から断られる場所（家・建物）を外す。`test/around.test.mjs`、`tests/unit/application/test_tool_control.py`
- 実況: 状況が変わったときだけ（`LiveNarration._changes`）、続けて 2 回失敗している間は黙る（`Activity.failing`）、`live_interval_seconds` 10 → 30、`max_silence_seconds` 60 → 180。`tests/unit/presentation/test_live_narration.py`
- 行き詰まり: 2 回続けて失敗すると道具の選択は `purpose=tool_stuck`（考える）でプロンプトに「行き詰まっている」の節。`config/default.yaml`、`config/development.yaml`（ユーザーの手元の `thinking_purposes: []` に `tool_stuck` だけ足した）
- FreeToken の調査（`/v1/stats`、`/v1/cache/status`。設定は変えていない）: 部分一致のキャッシュはクライアントから有効にできない
- テスト: `pytest tests/` 885 passed（`test_no_channel_no_chat_and_the_board_is_served` は配信がポート 8765 を使っているので除く）、ブリッジ `npm test` 271 件
- 未確認: 実機（ブリッジ・Python の再起動後）での 1 手の秒数、`tool_stuck` の考える時間、実況の頻度。見送り: 引数の欠けの検査（聞き直すしかない）、`find_blocks` の保護の印

### 家に入れない（ドアの前の穴）、言うことと行動を合わせる (2026-09-28)

**Commit**: 5c21699・fbee788（ブリッジ）、872cdb1（助言）、42819d2（待つ間に食べる）、f0bc03b（一言の実況）

配信で家のドアの前の床が 1 つ抜け、家に入れないまま約 20 分（体力 5.3、空腹 0、夜）。視聴者の「家の前の穴を埋めて」を受けたのに後回し、実況は「家に入れた」「パンにするね」と行動と違うことを繰り返した。ユーザーの依頼「1 から順に」「オートジャンプ」「小目標は常に今やっていること」「喋るときは行動を選ぶ、選んでいる間は繰り返さない」。
- ブリッジ: `enterHome` がドアの前の床の穴を埋めてから入る、足場の除外からドアの前の床の列を外す、穴の中を「前に着いた」としない、失敗に理由（ドアの前の様子と高さ）、`goto` は着いていなければ失敗、整地はドアの前の列も埋める、オートジャンプ（Minecraft のは画面側の設定で、ボットにはない）。`fillItem` を observe.mjs へ（循環の import を避ける）。`test/doorstep.test.mjs`
- 助言: 失敗が続く中で後回しにした助言は 1 度聞き直す、同じ完了条件の中目標を足さない
- 目標の決定を待つ間に飢えそうなら食べる。ローカルの LLM の時間切れに種類と秒数
- 一言の実況は行動を実行している間だけ、終わった行動の intent は消す
- スタック判定（Jev、6 手ごと）は動いていた: 21:13:08 に repeating と判定したが確信度 0.57 < 0.75 で考え直しは出さなかった。コードの判定（stuck / stalled / 同じ種類 2 回）も動いていた。直らなかったのは、ブリッジが失敗の理由を返さず、考え直しても同じ行動を選んだため
- 設計書 39・41・43・49 に追記、CLAUDE.md
- 未確認: 実機（穴を跳んで埋める、オートジャンプの向き、実況が黙る時間）

### Style-Bert-VITS2 の Neutral を Irodori-TTS の音声から作り直す (2026-09-28)

**Commit**: `ae1ca6e`

ユーザー「9/26 に作ったスタイルの Neutral は昔のもの？ Irodori 由来で Neutral も作りたい」「昔の Neutral は Neutral-01 として残す」「Irodori は生成が重いので SBV2 の Neutral を」。
- 確認: 9/26 の Neutral は元の録音から作ったもののまま（バックアップと一致）。配信は `tts.engine` 既定の style_bert_vits2、`emotion_style_map` は全部 Neutral（各スタイルの声が微妙だったため 9/28 に戻した）
- `irodori_from_sbv2.py emotions` に `neutral`（説明なしで、声 shen_sbv2 のまま読む）。`sbv2_add_styles.py` に `neutral` → Neutral、初めて置き換えるときは前の Neutral を `Neutral-01` として残す
- 実行: Irodori-TTS で台本のふつうの文 30 本（除外 0、5 分半）→ `data/irodori_train/shen_sbv2_neutral/`（声 `shen_sbv2_neutral` も登録）→ Neutral を置き換え（`style_vectors.npy.20260928-205345.bak`）→ TTS サーバー再起動。スタイルは Neutral, Happy, Surprised, Sad, Angry, Fear, Neutral-01
- 埋め込みの cos: 感情スタイルと元の Neutral 0.65〜0.68、新しい Neutral 0.83〜0.96。感情スタイルと元の Neutral の差の大半は「Irodori-TTS が読んだ音」の違いだった
- `sbv2_add_styles.py` は `Style-Bert-VITS2/venv` では動かない（torchaudio 2.9.1 に pyannote.audio 3.4 が要る `AudioMetaData` がない。SBV2 の要件は torchaudio<2.4）。9/26 もこのリポジトリの `.venv`（9/26 に pyannote 3.4.0 / torchaudio 2.3.1 を入れた）で動かしていた。手順書・スクリプトの案内を `.venv` に直した
- `config/development.yaml` の `emotion_style_map` を感情のスタイルに戻した（happy→Happy、surprised→Surprised、sad→Sad、angry→Angry、scared→Fear、excited→Happy。style_weight 1.0 のまま。ユーザーの選択）
- 未確認: 聞き比べ（新しい Neutral と Neutral-01、Neutral 基準での感情スタイル）

### 視聴者の助言を流さない、ブロックの状態を渡す（設計書 49） (2026-09-28)

**Commit**: 5f3478b（助言）、6ddb7d3（ブロックの状態）、8db950b（問いかけの形の助言、空の値）

ユーザーの依頼「コメントで指摘があっても今の目標に沿わないと『今別のことをしてるから』『後でやる』で無視する。改善したい」と「湿り具合などもブロックの情報として渡したい」「畑に水を引けるように」。
- 原因（`data/logs/ailoveshen-dev.log`）: 引き受けの失敗 26 回のうち 23 回がコードの視聴者の上限（同じ人 1 つ、同時 2 人）。書き直した返答が「後で」の none / decline になり、何も残らなかった。仕分けの種類は返答に渡っていなかった
- 助言は配信者の中目標（`MidGoal.suggested_by`、上限・予算に数えない）、上限の確かめを設計の前に（`check_room`）、流す返答の差し戻し（`unanswered`、`DeclineReason`）、返答の決まりを効率優先に合わせた。盤面・オーバーレイ・実況に「〜さんの提案」
- ブロックの状態: `t.block_at` / `find_blocks` に `props`、畑の状態と施設の中身に `dry`（知らせるだけ）、技・条件を書く決まりに props の読み方、やり方の帳に `water_farm`。ブリッジの devDependency に prismarine-block（本物の状態 id で moisture を確かめるテスト）
- 確かめる台本 `examples/comments_advice.json`（各コメントに合否の基準 `pass`、台本は `kind` を書ける）。`integration_test_minecraft.py` は返事のあとに足された中目標と考え直しを表示する
- 仕分けの記録では「畑拡張は？」が question、「畑広げない？」が request だった（Jev の確信度 0.5 前後）: 仕分けの定義を直し、返答の `suggestion` でも助言にする。台本は仕分けも通す（`create_comment_triage` を配信と共有）
- ローカルの LLM が使わない項目を "" で埋めると enum に合わず書き直しになる（前から 33 回）: `drop_empty` で書かなかったことにする
- 未確認: 実機（ローカルの LLM が decline_reason / suggestion を正しく選ぶか、3 回で流さない返答になるか、Jev の仕分けが直るか）、`use_item` のバケツ

### アバターの作り直しの調査（設計書 48） (2026-09-28)

**Commit**: なし（未コミット）

ユーザーの依頼「画像生成の三面図 → Tripo → Astra / Opus が Blender で仕上げる方法を調べて、アバターを作り直したい」。デザイン画像 `models/assets/avatar_design.jpeg`（前・後・上・下の 4 面、横なし）。
- `docs/design/48_avatar_remake.md`: 今のアバターが使う VRM の機能（合否の基準）、事例（ユニスポット、尾仲ゆる、Nano、manaimovie（Opus 5.5）、SushiNFTart / Tripo（GPT-6 Astra）、baku_lab）、seagetch/reference-guided-rig-parts は 2D 用で当てはまらない、Tripo と他の生成、VRM にするときの問題（表情の `textureTransformBind` の重ね方、揺れもの、MToon）、案 A〜D、試作の段階、ユーザーが決めること
- 見立て: 1 回目は「作り直しても良くならない」。manai（絵の差し替え＋あごの表情）と baku_lab（表情を作り込んだ素体の頭）で顔の解き方が見えたので、「試作で合否を見る価値はある」に改めた
- 実装・制作はまだ。§9 の決めること（Tripo を誰が回すか・プラン、4 面図の生成、合格の範囲、案）待ち

### 採掘場の見直し: 部屋を掘る順、設計の読みすぎ、階段の柱 (2026-09-28)

**Commit**: `6748eea`

アドバイザーの指摘。設計書 47 §16。
- 部屋を固定の蛇行で掘っていたので、底の段が蛇行の端にない深さ（28 段など）で最初のセルの隣に空いたセルがなく、経路探しが石を掘って自分で道を作るところだった（テストは底が y 40 = 蛇行の端でたまたま通っていた）。最後の段から幅優先に。12 通りの深さで隣を確かめるテストを足し、前のコードで落ちることを確かめた
- 輪の位置は 25 か所まで、坑道は自分のセルだけ確かめる（家のまわり ±24 だと約 2100 か所 x 縦穴を同期で読んでいた）
- らせん階段の柱と段の床を経路探しが壊さない（`inMineBody`）。`onMineCell` も索引（WeakMap）で引く（経路探しが何千回も聞く）
- ブリッジ 259

### 採掘場（設計書 47 段階 5） (2026-09-28)

**Commit**: `9953314`

設計書 47 §16。
- ブリッジ `facilities.mjs`: `mineLayout`（4 x 4 の輪のらせん階段、底の部屋、坑道。設計のときに予定のマスとまわりを全部読んで、溶岩・水・岩盤・落ちる砂利・洞窟を避ける。届かなければ一番深く安全な y を返す）、`mineStatus`、`mineDanger`、`stopMine`、`onMineCell`、`finishedMine`。`registerFacility` / `updateStatus` / 中身が mine を扱う
- `goals.mjs` の facility に採掘場（ツルハシがなければ作る、`dig_mine` の葉）、`candidates.mjs`（`dig the mine`、`go down the mine`）、`primitives.mjs` の `dig_mine`（順に空ける、危ないものに当たったら先を外して理由を残す）と、採掘場のマスに足場を置かない、`leveling.mjs`（採掘場の列を触らない）、`solver.mjs` / `world.mjs`（下りきった採掘場から深い石・鉱石へ）
- Python: `FacilityDesigner` に mine（bottom_y、tunnels、tunnel_length）、`FACILITY_TEMPLATE`、`PREDICATE_DESCRIPTIONS`、帳 `make_mine` と `mine_ores`
- テスト: `mine.test.mjs` 6 件（形、溶岩を避ける・一番深い y、済み・次のセル、途中の水で止める、整地、ソルバー）、`test_facility_design.py` に採掘場。Python 860、ブリッジ 257
- まだ: 坑道を延ばす、階段の松明、底で見つけた鉱石と採掘場の結びつけ。囲い・トラップ（§8、別の設計書）
- 事故: `node -e "import('./src/index.mjs')"` で読み込みを確かめようとして、ブリッジを本当に起動してしまった（20:10:48〜20:11 頃、ポート 3000 と 25578、サーバーにつながった）。止めた。止めるときの保存で `minecraft-bridge/data/state.json` が 20:11:02 に書かれた（読んで保存しただけ。ふつうの再起動と同じ経路）

### 施設は広げられないと言う、進める手のない小目標を差し戻す (2026-09-28)

**Commit**: `60d527d`

4B の見直し（アドバイザーの指摘）:
- 「add or enlarge」「作る・広げる」と言っていたが、施設は広げられない（同じ名前は登録を断られ、設計は知らない名前だけ）。弱いモデルは言われたとおり同じ `facility(name)` を書き、すぐ済んだとして返されて回る。すべて「別の名前で足す（例: wheat_farm_2）」に（goals / solver / プロンプト / 帳 / 設計書 / CLAUDE.md / テスト）
- 畑も植林場もない `farmed` / `planted` は最初から進める手がない（再起動後の保存した中目標によくある）。ブリッジの目標の状態に `dead_end`（進める手が 0 で `impossible` の理由がある）、Python の決定は差し戻す（`test_a_goal_that_cannot_progress_is_sent_back`）

### 作物と植林は施設で数える（設計書 47 段階 4B） (2026-09-28)

**Commit**: `a281fb2`

設計書 47 §15、設計書 33 の追記。
- ブリッジ `facilities.mjs`: `cropFarms`（設計した畑 + 家の畑の合計、最後に見た数 `state.memory.farms`）、`farmSummary`（`farming.mjs` から移動）、`workCell`、`layoutAt`、`designedFarms` / `designedTreeFarms`。`farming.mjs` から `homeFarm` / `plantSpot` / `tillSpot` / `sowSpot` / 輪を消した
- `goals.mjs` の `planted` / `farmed`（施設がない・空きがないときの理由、見えない畑へ行く）、`candidates.mjs`（plant / till / sow を 1 つに）、`tools.mjs`（施設のマス以外を断る）、`solver.mjs` の `growing`、`world.mjs` の `farm`、`index.mjs` の数の起点と観測
- Python: `PREDICATE_DESCRIPTIONS`（planted / farmed / facility）、`_format_farm`（「畑」、空き、畑の名前）、道具の説明、見直しの確かめること、中目標が進まないときの別のやり方、`abilities.yaml`
- 4A の見落としを直した: `FacilityDesigner` がブリッジにつながらないとき（`GameBridgeError`）を設計できないとする
- テスト: `crops.test.mjs`（growing）、`farming.test.mjs`（輪の代わりに workCell）、`facility-design.test.mjs`（家から 56 m の畑を数える、見えなくても数が変わらない、施設がなければ設計を促す）、`test_stream_context.py`、`test_facility_design.py`。Python 858、ブリッジ 251
- 注意: 設計できたあとで決定が差し戻されると、施設は planned のまま区画を持つ（建物も同じ）。前からある `state.memory.farm`（作物ごと）はもう読まない
- 要 ブリッジと Python の再起動。今の保存に設計した施設がなければ、植林・新しい畑は facility の中目標から始まる（前からある耕地にまくのはそのまま）

### 施設の設計と facility(name)（設計書 47 段階 4A） (2026-09-28)

**Commit**: `2391ee3`

ユーザー:「施設を作る前にレイアウトと設計図を作る」。設計書 47 §14。
- ブリッジ `facilities.mjs`: `registerFacility`（区画の中から畑の四角・植林場の格子を選ぶ。`near_home`）、`layoutStatus` / `updateStatus` / `nextCell`、`FacilityError`（400 直せる / 409 区画が読み込まれていない）。`PUT /facilities/<name>`。`farming.mjs` の `TILLABLE` / `CLEARABLE` / `SAPLING_GROUND` / `TREE_GAP` / `surfaceAt` を公開、`till` は草花を刈ってから耕す
- `goals.mjs` 述語 `facility`（条件、葉は till / sow / plant、見えていなければ施設へ行く）、`candidates.mjs`（施設の次のマス）、`tools.mjs`（x,y,z を省くと今の小目標の施設の次のマス）、`skills.mjs` の expects。テスト `facility-design.test.mjs`
- Python: `GoalPredicate.FACILITY`、`FacilityNotDesignedError` / `FacilityAreaNotReadyError`、ポートとクライアントの `set_facility`、`facility_design.py`（新規 `FacilityDesigner`）、`FACILITY_TEMPLATE` / `build_facility_prompt`、`MidGoalKeeper` の設計のフック（編集、頼み、街の段階）、`_without_build` が施設も落とす、ファクトリー（プランを 1 つ作って開始と設計に渡す）、`config/default.yaml`（facility_design: ローカルで考える、high）
- `abilities.yaml` の make_farm / plant_trees を施設の流れに
- テスト: `test_facility_design.py` 12 件。Python 858、ブリッジ 249
- まだ（4B）: `farmed` / `planted` を施設の中で数える。水はまだ置かない
- 要 ブリッジと Python の再起動。述語が増えたので、次の起動で今の段階の未解決の部分が 1 度書き直される

### 街の理想図（設計書 47 段階 3） (2026-09-28)

**Commit**: `8004636`

ユーザー:「1 枚の街の理想図を常にもっておいてマスターデータとしたい」「実際の座標も合わせて持ってると尚良い」。設計書 47 §13。
- ブリッジ `zones.mjs`（新規）+ `GET /zones`: 区画ごとの地形と重なる施設。テスト `zones.test.mjs`
- Python: `Zone` / `TownMap`（`value_objects.py`、`ZONE_USES`、`FACILITY_USE`）、`MidGoalPlan.town_map` / `restore(town_map=)`、`SavedPlan.town_map`、`JsonMissionStore`、`Activity.town_map`、`TownMapDrawnEvent`（Narrator が次の実況で話す）
- `town_map.py`（新規）: 固定の区画（施設、水・溶岩）、出力の形、当てはめと差し戻し
- `TownPlanner._keep_map` / `_draw_map`: 街を定めたら描き、段階が進んだら描き直す（引っ越しの途中は描かない）
- プロンプト: `TOWN_MAP_TEMPLATE`（1 文字 1 区画の地図、固定の区画、地形）、目標の決定の本文に `format_town_map`
- ゴールボード `/town` と `/api/goals` の `town_map`。`docs/streaming.md` の見るところ
- `config/default.yaml`: `thinking_purposes` に town_plan、`thinking_levels.town_plan: high`
- テスト: `test_town_map.py` 13 件、`test_goal_board.py`、`test_town.py`（区画を読めないブリッジ）。Python 846、ブリッジ 244
- まだ: 建物の設計の地図に区画の枠（格子がずれる）。施設の区画が足りないときの描き直しは段階 4
- 要 ブリッジと Python の再起動。今の保存（街が定まっている）なら、起動の `prepare` で最初の理想図を描く

### 目標の決定は効率を先に、システム指示を整理 (2026-09-28)

**Commit**: `7915417`

ユーザー:「肥大化もしてきたので整理も。目標の決定は大目標に関連しないものも効率化に必要なら積極的に取り入れたい」「変更内容はコミットしていっていい」。設計書 31 の追記。
- 先にユーザーの未コミットの変更をコミット（`228573a`: 一言の Jev の意味判定、`thinking_purposes` に tool、`development.yaml` の感情スタイル。`Neautral` の綴りは `Neutral` に直した）
- `game_prompt_template_builder.py` `GOAL_SYSTEM_TEMPLATE`: 「方針: 効率を先に」、中目標は大目標への段階と基盤、小目標は一番上の中目標（コードどおり。ほかは move / together）、重複をまとめ画面の話を消す。8024 → 6845 字。`TestPromptLength` が通る
- CLAUDE.md に Efficiency first

### 検索の道具を増やす: item_info、uses_of、search_abilities（設計書 47 段階 2） (2026-09-28)

**Commit**: `b38cc1c`

ユーザー:「レシピ等も含め検索ツールを充実させていく方向」「できることを検索可能にして動的に教える」。設計書 47 §12 に実装で分かったこと。
- 段階 1 の穴を先に直した: 一覧の説明が英語なので日本語の検索語（鉄、小麦の畑）が語の重なりで当たらず、覚えた場所は 16 件を超えて正解が切り捨てられ、関係ない候補の座標を返していた
  - ゲームの `ja_jp.json`（misode/mcmeta `1.21.4-assets`）から日本語名: `tools/fetch-recipes.mjs` → `data-static/names-ja-1.21.4.json`（アイテム 1552、モブ 154）
  - ブリッジ: `/memory` は種類ごとに 1 件（近い 5 か所、`ja`、`gives_ja`）、`/facilities` の作物に `ja`
  - `search.py`: 漢字 1 文字も語に、完全一致は Jev に聞かない、語が重ならない候補は名前だけ、長い一覧で重ならなければ「切り捨てた、英語で検索し直して」
- ブリッジ `items.mjs`（新規）: `GET /items`（名前と日本語名）、`GET /items/<name>`（作る・焼く・掘るブロックと要る道具・落とすモブ（まれなもの）・畑・植える、置けるか、食べ物、使うレシピの数）、道具 `uses_of {item, have?}`（逆引き、作れる物が先、20 件）
- Python: 道具 `item_info {query}`（一覧は 1 回だけ読む）、`search_abilities {query}`。やり方の帳 `infrastructure/adapters/prompts/abilities.yaml`（22 項目。`data/` は git から外れているのでここ）+ `abilities.py`（読み込み）。道具のプロンプトの決まりを検索の道具の使い分けに
- CLAUDE.md: 機能を足したら帳にも足す決まり。帳の道具と `see` を確かめるテスト
- 本物の Jev で確かめた（§12）: パン→bread、かまど→furnace、水を渡りたい→cross_water など 16 件中 14 件が正解か完全一致
- テスト: Python 831（`test_search.py` 17 件）、ブリッジ 242（`items.test.mjs` 3 件）
- 要 ブリッジと Python の再起動
- 落ちているのは `TestPromptLength::test_goal_system_length`（8024 > 8000）だけ。ユーザーの未コミットの目標のシステム指示の書き換えによるもので、どこを削るかはユーザーが決める

### 施設の登録と検索の道具（設計書 47 段階 1） (2026-09-28)

**Commit**: `44aa0aa`

ユーザー:「施設を作る前に配置図・設計図、街の理想図をマスターデータに」「search_facility でリストとLLMの検索語を Jev に渡してキーを選ばせる」「プレイするのは Qwen3.6。できることをどんどん検索可能にして動的に教える」。設計書 47 §10 の段階 1。
- ブリッジ `facilities.mjs`（新規）: `state.facilities`（kind home/build/farm/tree_farm/mine/pen/trap、box、entrance、status）。今ある物を取り込む（`adoptExisting`: 家、`state.builds`、家のまわりの耕地 → `home_farm`、植えた苗木のかたまり → `tree_farm_N`）。`GET /facilities`（近い順、中身: 作物ごとの planted/ripe、苗木/木/空き、建物の進み）
- ブリッジ `memory.mjs` `memoryList` + `GET /memory`: 覚えた場所を座標つきで（種類ごとに近い 5 件、チェストの中身、かまど、死んだ場所）
- ブリッジ `home.mjs`: `plantings`（植えた苗木）が保存されていなかったのを直した（再起動で planted の数が 0 に戻っていた）。`facilities` も保存
- Python `application/use_cases/search.py`（新規）: `KeySearch`（語の重なり（英語の語、日本語の 2-gram）で 16 件に絞り、Jev の CHOICE で A.. か none。確信度 0.5 未満・答えない → 語の上位 3 件を「確かではない候補」。`logs/search`）、`WorldSearch`（`search_facility` / `search_memory` を一覧から）
- `tool_catalog.py`: `search_facility {query}`、`search_memory {query}`（Python が扱う、ブリッジには送らない）。`play.py`: 調べものは 1 ステップ 5 回まで（3 → 5）。道具のプロンプトに使いどころの決まり
- 設定 `minecraft.agent.search_min_confidence` / `search_timeout_seconds` / `search_record_dir`
- テスト: `test_search.py` 11 件、`test_tool_control.py` 1 件、ブリッジ `facilities.test.mjs` 3 件（ブリッジ 239）
- 要 ブリッジと Python の再起動
- 未コミットのユーザーの変更（`config/development.yaml`、目標のシステム指示の見直し・小目標の決め方の書き換え、`thinking_purposes` の tool、一言の Jev）は含めていない。目標のシステム指示が 8024 字になり `TestPromptLength::test_goal_system_length`（< 8000）が落ちる
- 前のコミットで設計書 47 にユーザーの未コミットの変更（`config/development.yaml`、目標のシステム指示）を混ぜてしまったので、プッシュ前に設計書だけのコミット（`870a6e6`）に作り直した

### 画面を見るのをやめる (2026-09-27)

**Commit**: `fc108e5`

ユーザー:「画像による判断が覆したり解決したシーンはあったか」→「画像は一旦オフ」。設計書 23 の追記。
- ログ（9/25〜、画像つき 435 回）: 画面が判断を変えて困難が解けた例は見つからず、誤らせた例が 2 つ
- `config/default.yaml`: `minecraft.vision.enabled: false`（失敗の後の画像、look_screen なし）。建物の設計の地図は Gemini のまま
- 要 Python の再起動

### 家の外なのに「家の中に入った」: 家の行と今いる場所を分ける (2026-09-27)

**Commit**: `5f822c8`

ユーザー:「家の中に戻ってないのに家の中にいると思っている」「完成しているという文言が元凶」→「その 3 つで進めて」。設計書 46 の追記。
- ブリッジの判定は正しかった（390m 外で in_home false、プロンプトも「外にいる」）。実況が「新しい小目標: at_home()」を済んだことと読み、一言が私の足した「食い違わない」指示で繰り返した
- `stream_context`: 「家: …（建ててある）」と「今いる場所: 家の中 / 家の外（家まで Nm）」を別の行に（`GameObservation.home_distance_m`）
- `live_narration.py`: 正しいのは今の状態、直前の発言が違えば言い直す
- `narrator.py`: 「新しい小目標（これからやること。まだ済んでいない）: …。今いる場所: …」
- テスト: Python 812。要 Python の再起動

### 発言と行動を食い違わせない (2026-09-27)

**Commit**: `efba3c3`

ユーザー:「その順で全て進めてほしい」（目標の決定をコードで照らす → 一言を矛盾させない → 古い実況を捨てる）。設計書 46。
- `play.py`: `set_goal` で済んでいる小目標、後の手順が済んで前が残る手順（`_check_step_order`）、見直しの時間帯の取り違え（`_check_time`）を差し戻す
- `stream_context.format_time` / `format_time_en`: 夕方は夜までと朝までの両方
- `live_narration.py`: 今の小目標を渡し、直前に話したことと食い違わない指示
- `tts_service.py` + `factories/tts.py` + `config/default.yaml`: `tts.queue.max_age_seconds`（commentary 20 秒）
- テスト: `test_play_use_cases.py` 3 件（自分のテストで「夜が明けたら」の誤検出を見つけて直した）、`test_stream_context.py`、`test_live_narration.py`、`test_tts_service.py`。Python 811
- 要 Python の再起動（ブリッジは変わっていない）

### ボートが岸の角でサーバーに戻され続けて止まらない、目標の決定はローカルでも考える (2026-09-27)

**Commit**: `17025ee`

ユーザー:「ボートに乗ると何の行動選択もしなくなる」「目標の決定は多少考えて良い」。設計書 37 の追記、設計書 36 §8。
- 実機: `go_home` のボートが (21.8, -177.5) で 5 分以上。Paper 1.21.4 を逆アセンブルし、`hasNewCollision`（動いた後の箱に新しい当たり → ログなしで戻す）と特定。箱の北西の角が水面の高さの草ブロック (20, 62, -179) にかかっていた（RCON）
- `boat.mjs`: `boxFree`（1.375 x 0.5625 の箱）で `clearAhead`、20 ティック続けて戻されたら止まる。`primitives.mjs` の `legToward`: 水の上で止まったら行動を失敗にする（乗ったまま歩かない）
- `config/default.yaml`: `thinking_purposes` に goal、goal_after_failure
- テスト: `boat.test.mjs` 2 件。ブリッジ 236、Python 806
- 要ブリッジ再起動。今止まっているボットは再起動か `POST /abort` で抜ける

### 画像以外はすべてローカルの LLM (2026-09-27)

**Commit**: `bec2d05`

ユーザー:「ローカルでも 130k までコンテキストを用意してあるので、画像以外は全てローカルに」。設計書 36 §8。
- `config/default.yaml`: `llm.routes` は `default: local` だけ、`fallback: none`、`context_tokens: 130000`（サーバーは 262144）
- Gemini に行くのは画像つきの呼び出しと、130000 に入らないプロンプトだけ
- 見ること: ローカルの goal / reply / promise の schema 崩れ（§7 で多かった）と返答の遅さ

### 畑を広げる、目の前にあるものを毎回見せる、見直しで効率化 (2026-09-27)

**Commit**: `3608680`

ユーザー:「畑の拡張も選択肢に」「積極的に効率化を考える」「周囲のブロックとアイテムを常に LLM に。高さは ±2」。設計書 33 の追記と設計書 45。
- `solver.mjs`: 家の畑が育ち途中（`growing`）のとき、種があれば sow、クワがあれば till を `also` で出す（畑を広げる）。理由に `farmed(<作物>, 今の数 + 8)` の手順
- `around.mjs`（新規）`aroundMe`: 半径 8・足元 ±2 の空気に面したブロック（種類ごとに数、一番近い座標、取れる物、熟した数、24 種類まで）と落ちているアイテム → `/observe` の `around`
- `stream_context._format_around`: 「目の前のブロック」「目の前に落ちている物」。道具の決まりに「目の前にあれば遠くへ探す前に使う」
- 目標のシステム指示の見直しを「毎回確かめる」一覧に: 目の前を先に、増やす仕組み（苗木・畑を広げる）、待つ時間の使い方。8000 字に収めるため重なる文を削った（7998 字）
- テスト: `around.test.mjs`、`crops.test.mjs`、`test_game_prompt_template_builder.py`。ブリッジ 234、Python 806（ポートの 1 件を除く）
- 実機では未確認（ブリッジの再起動が要る）

### 家の畑が育ち途中なら遠出しない (2026-09-27)

**Commit**: `7760b3b`

ユーザー:「畑を畑と認識できない」→「自分の畑を使わず遠くへ行く」。設計書 33 の追記。
- 事実: 家の畑の小麦 9 本は age 1〜5（RCON で確認）、ブリッジの判定は正しい。熟していないので、ソルバーが 261 m 先の小麦への移動だけを出していた。作物は近くにいないと育たない
- `farming.mjs` の `homeFarm`（`state.memory.farm` に記憶）、`farmSummary` に ages、`world.mjs` の `farm`、`solver.mjs`（熟していれば家の畑へ、育ち途中は `growing` で遠出なし）、`candidates.mjs`（`go to the home farm`）、`stream_context`（育ち具合）
- テスト: `crops.test.mjs`（育ち途中・熟した・畑なし）、`test_stream_context.py`。ブリッジ 232、Python 805

### 整地: 穴の中から積んで埋める (2026-09-27)

**Commit**: `e00840c`

ユーザーのログ: 穴に立ったまま `level` が `standing in the hole` でその列を飛ばして失敗。ユーザー:「穴の中から積んで埋めるでいい」。設計書 41 の追記。
- `primitives.mjs` の `level`: 埋めるセルに立っていて土があれば `GoalBlock(p + 1)` で pathfinder に積ませる（埋まったか確かめる）。丸石だけなら縁に出てから置く
- テスト: `leveling.test.mjs`（2 段の穴の底から 2 段積んで埋める）。ブリッジ 231
- 1 回だけ `npm test` が 1 件落ちたが、どのテストかは出力に残らず、20 回流しても再現しなかった（配信で CPU が混んでいた時。時間に左右されるテストの疑い、未確認）

### ローカル LLM と Gemini の振り分けを見直す (2026-09-27)

**Commit**: `755215d`

Gemini が 403 Spend cap breached。ユーザー:「振り分けを見直してほしい」。設計書 36 §7。
- 今日の Gemini の入力の約 8 割が `tool` / `tool_after_failure`。ローカルで崩れた JSON は goal / reply / promise に集中、tool には出ていない
- `config/default.yaml` の `llm.routes`: 既定はローカル（tool、tool_after_failure、commentary、live）、goal・goal_after_failure・reply・promise・town・site・house・build_design・skill_write・check_write は Gemini。`fallback: gemini`
- 有効にするには `.env` の `LOCAL_LLM=1`（今は 0）

### 起動のたびに街の段階を書き直さない (2026-09-27)

**Commit**: `af18974`

ユーザー:「起動時に街の段階を毎回書き直していて起動が遅い」。ログ: 起動のたびに 5 段階を書き直し（ローカル 42〜56 秒 × 5、Gemini 約 75 秒）、未解決の部分は同じ語彙では書けないので毎回同じ答え。
- `TownStage.tried_with`（保存）、`IGamePromptBuilder.vocabulary_version()`（条件の一覧と ABILITIES のハッシュ）。`TownPlanner.prepare` は今の段階だけ、語彙が変わったときだけ書き直す。定めた直後の段階は試し済み。`advance` から毎回 `prepare`（同じ語彙なら何もしない）
- テスト: `test_town.py`（同じ語彙では書き直さない、変わったら今の段階だけ）。805 passed
- 更新後の最初の起動だけ、今の段階を 1 回書き直す（保存済みの段階は `tried_with` が空）

### 畑の小麦を収穫する、ブロックから取れる物を見せる (2026-09-27)

**Commit**: `bf91c8b`

ユーザー:「畑に生えている小麦を認識できていない。環境情報にアイテムの情報も」（取れる物を添える、植え直す）。設計書 33 の追記。
- 原因: 作物が取れる元の表にも記憶にもなく、`how_to_get(wheat)` が no way。畑は farmed のときしか数えていなかった
- ブリッジ: `farming.mjs`（`CROP_BLOCK`、`cropByBlock`、`isRipe`、`farmSummary`）、`knowledge.mjs`（作物、`blockDrops`、`dropsOf`）、`memory.mjs`（作物を覚える）、`world.mjs`（熟したものだけ）、`primitives.mjs`（`harvestNearby`、`replant`）、`tools.mjs`（育ち途中を断る、find_blocks の ripe/age）、`index.mjs`（places の gives、farm）
- Python: `stream_context`（「家のまわりの畑」、覚えている場所の「→ 取れる物」）
- テスト: `test/crops.test.mjs`、`tools.test.mjs`、`test_stream_context.py`。ブリッジ 230、Python 804

### 束を見直しのたびに見直す (2026-09-27)

**Commit**: `51dc9c9`

ユーザー:「束は見直しで不定期に見直すようにしてほしい」。設計書 44 §9。
- 見直しの決まりに「束を見直す（毎回）」。スキーマに必須の `trips`（review の次。残りを外出ごとに分ける）、`GoalDecision.trips`（ログ）。小目標が束なら別々に書かれた手順をまとめる `_steps_with_the_bundle`。goal system 7999 字（上限 8000）
- 実測: 決まりだけでは束にしたりしなかったり（0〜3/4）。`trips` で 5/5。方角が違えば 4/4 束にしない

### 同じコメントに返事を 2 回作らない、守れない約束は言わない (2026-09-27)

**Commit**: `ff80c7a`

ユーザー:「コメント 2 重で対応しちゃうの直して」「約束できないものはやるね、とか言わないで」。設計書 34 §10.3。
- 原因: Jev の約束チェックが約束でない返事を拾い、約束の判断に「約束ではない」がないので Gemini が cannot → 返事を作り直していた（13:06 は返事なし）。優先度を理由に cannot にもしていた
- `PromiseHandling.NONE`、`promise_schema` の `rewritten_reply`（必須、理由の後ろ）、`UnkeptPromise`（`_keep_promise` の cannot は理由と言い直し。言い直しを話し、返事を作り直さない）、`PROMISE_RULES`（none、「余裕ができたら」も約束、優先度は later、custom）
- テスト: 言い直しをそのまま話す（生成 2 回）、Jev の読み違いは none でそのまま、言い直しが空なら前の道。802 passed。本物の Gemini でログの 4 件と「踊って」を確認

### 小目標の束（同時に追う複数の条件） (2026-09-27)

**Commit**: `b3cc3cf`

ユーザー:「小目標は複数持てるようにして効率的に全て達成できるように Gemini がプランを練ってほしい」。形は「同時に追う束」、4 個まで、ほかの中目標の条件も可、束の条件が 1 つ済んだら話す。設計書 `docs/design/44_goal_bundles.md`（§8 に実装）。
- ブリッジ: `goals.mjs`（`together()`、`describeSpec`、`evaluate` が条件ごとに評価してまとめる、`members`）、`candidates.mjs`（候補に `for`）、`index.mjs`（`publicStatus` の `members`）
- Python: `Goal` / `PlannedStep` の `together` / `approach`、`check_bundle`、`GoalStatus.members`（`BundleMember`）、`GoalMemberMetEvent`、`PlaySession.step_budget` / `newly_met_members`、スキーマ（`_bundle_properties`、`parse_bundle`）、`AdvancePlay`（`set_goal(together=)`、`_also`、`_members_met`、手順の判定は条件ごと）、`GoalChooser`（まだの条件だけ渡す）、`Narrator.on_member_met`、`stream_context`・Jev の状態・ボード、`JsonMissionStore`、目標の決定の決まり（`TestPromptLength` 8000）
- テスト: `tests/unit/application/test_goal_bundles.py`、`placing.test.mjs` の束。Python 800 passed、ブリッジ 226
- 本物の Gemini: 森への道に石がある状態で 3 回とも束を使わなかった。次は review で束にできるかを必ず見させる

### 壁に付いた松明を「置けなかった」としていた (2026-09-27)

**Commit**: `c8f7be4`

ユーザー:「壁に松明もうあるのに置こうとして失敗してるみたい」。ログでは失敗なのに残りが 10 → 5 と減っていた。`place_at` が当たり判定で置けたかを見ていた（松明は当たり判定なし、壁では `wall_torch`）。置く前とセルが変わったかで見る。`test/place_at.test.mjs`（古いコードで落ちることを確認）。


### 行動の途中の一言（10 秒黙ったら話す、繰り返さない） (2026-09-27)

**Commit**: `3f3cda1`

ユーザー:「ローカル LLM 使う時は頻繁に考えてること、やろうとしていることを喋るように」「10 秒間隔くらい。同じ内容の繰り返しにはならないように」。設計書 `docs/design/43_live_narration.md`。
- 原因: 実況は小目標の切れ目だけ。max_silence も切れ目で見るだけ。時間で止めない行動の間は何分も黙っていた
- 新規 `presentation/services/live_narration.py`（`LiveNarration`、`SpokenLines`、`similarity`）。`TTSService.is_idle()`（合成・再生中を数える）。`GenerateCommentaryRequest` / `LLMService.generate_commentary` に `purpose` と `record`。`factories/stream.py`: `say` で直前と同じ内容を捨てる、返事も覚える、`LiveNarration` の配線。`Stream(live=)` の裏のタスク。config `stream.live_interval_seconds: 10`、`thinking_levels.live: low`。道具の intent の説明を「そのまま読み上げる口調の 1 文」に
- テスト: `test_live_narration.py`（再生の終わりから測る、60 秒の行動で繰り返さない、作っている間の発話で捨てる、プレイ外・Jev の id）、`test_tts_service.py`。本物の Gemini で 60 秒の行動を流し、繰り返しなし・1〜2 秒
- 未確認: ローカルの LLM の枠を取り合って道具の判断が遅れるか（入れた後に 1 手の秒数を比べる）

### 家の中に松明を置けなかった（place_at の二重定義）、壁に付ける (2026-09-27)

**Commit**: `b0295d9`

ユーザーのログ（12:48）: `placed(torch, 2, home)` で、提案は `Cannot read properties of undefined (reading 'x')`、道具 `place` は `it is part of the current home` で断られ続けた。ユーザー:「壁に設置できるアイテムは壁に設置できるようにもしてほしい」。設計書 21 の追記。
- 原因 1: `PRIMITIVES` の `place_at` が二重定義（提案用 `9f262b5` と道具用 `d1d1721`）で、後の道具用が勝ち、提案は `against` なしで落ちた。テストは候補ができるところまでしか見ていなかった。`standard` は検出していた（`npm test` は lint を走らせない）
- 原因 2: 道具 `place` は家の中に家具しか置かせなかった
- `placing.mjs`: `wallMountable`、`INTERACTIVE`、`supportFor`、`placeSpots` が `{pos, against}`（壁を先に、家の中は床 +1）。`goals.mjs` / `candidates.mjs`: 提案も `place_at`（面つき、id に on the wall）。`primitives.mjs`: 提案用の定義を消した。`tools.mjs`: `inRoom`（部屋の中は何でも、ドアの内側の列とベッドの場所は空ける）、`on`。`builds.mjs`: `inHomeRoom` を公開。`tool_catalog.py`: place の説明と `on`
- テスト: `placing.test.mjs`（壁の松明、提案の形、ランタンは床）、`tools.test.mjs`（部屋の中、壁、床、通り道、開く物の面）。実機ではまだ試していない（配信中のため）

### 任意の条件 custom(name): Gemini が判定コードを書く (2026-09-27)

**Commit**: `058a8be`

ユーザー:「目標には任意のものを設定できるようにしたいです」（完了は「Gemini が判定コードを書く」）。設計書 `docs/design/42_custom_conditions.md`。
- ブリッジ `conditions.mjs`（新規: isolated-vm の同期の判定、読むだけの w、上限、`registerCheck` は今成り立つ判定を断る）、`goals.mjs`（述語 custom、候補なし、壊れた判定は blocked）、`index.mjs`（`PUT/GET /conditions`、/observe の home に door / min / max）、`home.mjs`（保存）、`skills.mjs`（expects）
- Python: `GoalPredicate.CUSTOM`、`parse_spec` の name、`predicates_now(tools=)`、`CheckWriter`（`use_cases/checks.py`）、`CheckNotWrittenError`、`MidGoalKeeper(checker=)`（accept・編集・街の段階）、ブリッジのポートとクライアント（`set_condition` / `conditions`）、`build_check_prompt`、説明・できること・返事の決まり、目標ボード、技の expects、config `check_write: medium`、`TestPromptLength` 7800
- テスト: `test/conditions.test.mjs`（5）、`test_checks.py`（6）、クライアント。本物の Gemini: 花 4 本・羊 3 匹の判定を書き、サンドボックスで 0/4→4/4、1/3→3/3
- 配信中の「整地はまだできない」: 動いていたのは 11:54 起動のプロセスで、整地（12:09）が入っていなかった（再起動が要る）

### 返事の約束を中目標にする（書き直させない） (2026-09-27)

**Commit**: `cc5d432`

ユーザーのログ: 12:10「そこらに豚、牛いるから倒してきて」への返事（「ボートを作って水辺を渡って探しに行ってくるね！」、`request: none`）が 2 回とも約束だけと差し戻され、返事が出なかった。12:17 にも同じ差し戻し。ユーザー:「今ぱっとやってしまったほうが良いか確認して良ければ割り込み処理にしてちゃんと実行」「こういうのちゃんと中目標に追加してほしい」。設計書 34 §10.2。
- 原因: Jev の約束チェックの状態は `{comment, reply, handling}` だけで、今の作業（生存の小目標 `have(food, 4)`、ボートを作ったところ）を知らない。書き直しの指示には「今している」の逃げ道がなく、Gemini は同じ正しい返事を 2 回書いた
- `generate_response.py`: 約束していたら `_keep_promise`（Gemini `purpose=promise`: already / now / later / cannot）。now / later は返事の accept と同じ `_accept`（先頭に足して `request_rethink`）。cannot（条件で書けない）だけ前の書き直し。decline では now なし。チャットの指摘で切って 60 秒以内の now は later
- `goal_vocabulary.py`: `PromiseHandling`、`promise_schema`。`reply_schema` の `when` の説明をプロンプトの決まり（13 §14、今やるほうが合理的なら）にそろえた（「夜の家の中だけ」のまま残っていた）
- `prompt_template_builder.py` / `IPromptBuilder`: `build_system_prompt(purpose="promise")`、`build_promise_prompt`。config `gemini.thinking_levels.promise: low`
- テスト: `test_generate_response_use_case.py`（今やる、後で、already、cannot、決め直し、間引き、最後まで守れない）、`test_prompt_template_builder.py`。本物の Gemini（実際の中目標リスト）: 12:10 と 12:17 の返事は now → `have(beef, 3)`、繁殖の decline は cannot。最初の決まりでは 12:17 が「生存の小目標でしているから」と cannot になったので決まりを足した

### 整地を条件にする（leveled） (2026-09-27)

**Commit**: `cedbd7e`

ユーザー:「整地も条件にできるようにしてください」（範囲は場所＋半径、高さは中心の地面）。設計書 `docs/design/41_leveling.md`。
- 原因: 条件の語彙に整地がなく、中目標にできなかった（返事は「もともと平らだから」と作り話の理由で断っていた）
- ブリッジ `leveling.mjs`（新規: 列ごとの判定、TERRAIN を共通に）、`goals.mjs`（述語、ツルハシ・土が要るとき）、`candidates.mjs`（level の候補）、`primitives.mjs`（`level`: 1 回の行動で届く列を次々にそろえる）、`tools.mjs`（道具 `level`）、`skills.mjs`（expects）
- Python: `GoalPredicate.LEVELED`、語彙とスキーマ、説明、できること、道具の一覧、技の expects、目標ボード、返事の例と「本当の理由を言う」
- テスト: `test/leveling.test.mjs`（7 件）、`test_goal_vocabulary.py`。実機（家から 300m 以上離れたことを確かめてから）: 8 掘って 6 埋めた、石はツルハシがないので飛ばした
- 別件: 前の実機試験でスポーン近くに開けた穴は、ユーザーの了承を得て y63 の草地に埋め戻した

### 雑談の幅と、自分について話したことを覚える (2026-09-27)

**Commit**: `2b5d5e8`

ユーザー:「いろんなジャンルの雑談を許容、LoL の話題に答える。自分の話は記憶して一貫性を保つ」。LoL の設定は話しながら決めて覚える、ワールドを作り直しても残す（ユーザーが選んだ）。設計書 `docs/design/40_small_talk.md`。
- 原因: キャラ設定とシステム指示が Minecraft 実況者の枠だけで、「クレッド対面ってシェンだとどうしてる？」に「Minecraft実況者だから詳しくない」とかわしていた（`logs/promises`）
- `prompt_template_builder.py`: 返事の規則に「## 雑談」、reply_requests に「## 自分の話（self_facts）」、本文の先頭に「自分について話したこと」、配信スタイルに雑談。`goal_vocabulary.reply_schema` に `self_facts`
- 新規: `ports/output/self_fact_store.py`（`SelfFact`、`ISelfFactStore`）、`use_cases/self_facts.py`（`SelfFacts`）、`storage/json_self_fact_store.py`。`GenerateResponseUseCase(self_facts=)`、`create_llm_service(self_facts=)`、`factories/stream.py`、config `twitch.self_facts_path`
- テスト: `test_self_facts.py`、`test_prompt_template_builder.py`。本物の Gemini で LoL・アニメの質問に答え、前に言ったことに合わせることを確かめた

### ドアを通って歩く（pathfinder にドアを教える） (2026-09-27)

**Commit**: `0e59155`

ユーザー: 家以外の建物に一度入ると出られない。選んだ直し方は「pathfinder にドアを教える」（家のドアはコードのまま）。設計書 `docs/design/39_doors.md`。
- 原因: mineflayer-pathfinder は boundingBox が empty のセルしか通さず、ドアは開いていても 'block'（壊すもの → 壊してよいのは地形と葉だけなので通れない）。`canOpenDoors` はゲートにしか効かない。今までの `walkThroughDoors` はドアに一度も効いていなかった
- ブリッジ `doors.mjs`（新規）: `teachDoors`（まっすぐ・向きに沿うときだけドアとゲートを通れるセルに。鉄・家・前の家のドアは除く）、`doorOpener`（歩いている間: 経路の先の閉じたドアを開けて開いたのを待つ、通り抜けたら閉める、歩き終わりに届くものを閉める、ドアのノードを床に戻す: pathfinder の postProcessPath が扉の板の上にしていた）。`move.mjs`: `walkWatched` で doorOpener、`walkThroughDoors` と試し直しを消した。`primitives.mjs`: `configureMovements` で teachDoors、`sleep_at` はふつうの歩き
- テスト: `test/doors.test.mjs`（本物の Movements で。teachDoors なしでは落ちることを確かめた）。`move.test.mjs` の試し直しのテストを消した。ブリッジ 212 件
- 実機（BoatTest）: 小屋から出て閉める 1.8 秒、入って閉める 1.7 秒、家のドアは通らない
- **事故**: 最後の実機の試しで BoatTest の移動（tp / spreadplayers）が効かず、スポーン近く（-57,63,-111、家から約 40m、建物とは重ならない）で小屋を RCON の fill で作って消した。7x7 の穴（深さ 2）と、その南の 7x11 が 1 段低い。ユーザーに埋め戻すか確認する。次から試しのスクリプトは、場所を確かめてから fill する

### 行動を時間で止めない (2026-09-27)

**Commit**: `cc95272`

ユーザー:「20 秒の制限は何のため？なくても良い。移動もそうだけど基本制限は不要。必要になったら野営」。範囲はすべての行動、止めるのは考え直し・空腹と体力・見張り（Jev）・夕方と夜。設計書 `docs/design/38_no_time_limits.md`。
- ブリッジ `runner.mjs`: タイムアウトと `walkedOnTimeout` を消し、`needNow()`（0.5 秒ごと、始めたときになかった必要だけ: 暗くなった（`NIGHT_VERBS` 以外）、食料 ≤ 6（eat 以外）、体力 ≤ 8（戦う・逃げる以外）、乗っている間は見ない）で `stopped: …` にする。`stopped:` / `rethink:` は失敗に数えない（`NOT_A_FAILURE`）
- `primitives.mjs`: `legToward` は着くまで区間ごとに歩く（9 か所の呼び出しは途中で返さない）、`ride_boat` は岸まで、smelt は全部焼けるか火が消えるまで、dig_down は段数の上限なし、刈るのは 10 秒の上限なし、attack は見失う（24m）か近づけない（`STUCK_MS`）まで、flee は離れられなくなるまで（`stuckWatch`）。`TIMEOUTS_MS` などの定数を消した。`progress.mjs` の `limit_s`、`boat.mjs` の `drive` の既定の上限、`skills.mjs` の 180 秒も消した
- Python: `MineflayerBridgeClient` は行動（/act、/tool、技）の返事を待ち続ける（`read=None`。Node の `requestTimeout` は返事を待つ間は切らないことを実測）。`INTERRUPTED_PREFIXES` に `failed: stopped: ` / `failed: rethink: `。`AdvancePlayUseCase._cut_on_rethink`: 行動の間に考え直しを頼まれたら `bridge.abort("rethink: …")`（行動が始まる前なら始まるまで止め続ける）。道具の説明（goto、ride_boat）と技のプロンプトから 48m・20 秒・180 秒を消した
- テスト: `runner.test.mjs`（時間では止めない、夕方・空腹で止める）、`leg.test.mjs`（1 回で全区間、途中で止める）、`skills.test.mjs`（時間の上限なし、待ちは CPU に数えない）、`test_play_use_cases.py`（行動中の考え直しで止める、始まる前の依頼）、`test_mineflayer_bridge_client.py`（行動のリクエストは読み取りの上限なし）
- 実機では未確認（長い移動が 1 回で着くか、夕方で止まった後に夜の小目標へ移るか）

### ボートで水を渡る、アイテムを使う（use_item） (2026-09-27)

**Commit**: `5d560b3`

ユーザー:「ボートって今使えるようになってる？」→「実装して使えるように。長距離水の上を移動する予定の場合は積極的に使いたい。アイテムを使う仕組みで汎用化されてない？」→ use_item も一緒に。設計書 `docs/design/37_boats_and_items.md`。
- 実測（試験用ボット BoatTest、配信のボットから離れた海）: Mineflayer は乗ると物理を止め入力しか送らない（ボートの位置は乗る側が送る）。自分で `vehicle_move` を毎ティック 0.35 ブロック送ると Paper は修正なしに受け入れた。降りるのはスニークの `entity_action`（Mineflayer の `dismount` はジャンプを送る）。降りても `bot.vehicle` が残る。岸のすぐ隣には置けない
- ブリッジ `boat.mjs`（waterCrossing、placeBoat、mountBoat、drive: 島をよけ向こう岸で止まる、leaveBoat、breakBoat、watchVehicle）。`primitives.mjs`: `legToward` の最初に `crossWater`（12m 以上の水: ボートがあれば水際まで行って置いて乗り、岸で降りて壊して拾う。なければ `state.waterAhead`）、`ride_boat`、`use_item`、`TRAVELS`、`disembark`、`useChest` → `withOpenChest`（「使う」と紛らわしい）。`runner.mjs`: 乗ったままなら移動以外の前に降りる、乗っている間はダメージで止めない、漕いだ分は時間切れでも失敗にしない。`reflex.mjs`: 乗っている間は動かない。`goals.mjs`: 水があってボートがなく作れるなら `also`。`knowledge.mjs`: グループ `boat`。`observe.mjs`: `self.riding`。`mirror.mjs`: `vehicle_move` を視聴者へ、乗っている間は向きだけ。`tools.mjs`: `ride_boat`、`use_item`（火・爆発・スポーンエッグは使わない、専用の道具があるものは断る、守るブロックには使わない）
- Python: `tool_catalog` に use_item / ride_boat、`stream_context` のできること（ボート、アイテムを使う。「動物を増やす」をまだできないから外した: 実測で繁殖できた）
- 実測（本物のコード）: 川 16.4m を渡って拾い直し目的地まで、海 200m（136.8m + 54.9m）、牛 2 頭に小麦で子牛、バケツで水を汲む
- テスト: `test/boat.test.mjs`（水の判定、岸で止まる、島をよける、1 ティックの速さ、止める合図、道具の断り方、TRAVELS、ミラー）。`test/leg.test.mjs` の偽のボットに blockAt
- 見つけたが直していない: `PRIMITIVES` に `place_at` が 2 回定義されている（後の方が使われる）

### rethink の経路の漏れを塞ぐ (2026-09-27)

**Commit**: `075e45e`

ユーザー:「rethink のパスも潰しときたい」（前の項の残り）。同意して rethink を書いた返事が何も起こさない道が 3 つあった。設計書 34 §10.1。
- 間引き: 60 秒以内の 2 つ目の rethink は消えていた → まだ使われていない考え直しがあれば足す（切り直さないので間引かない）。切ってから 60 秒以内なら返事を書き直させる（accept か、今の小目標を続けると言う）。最後まで rethink なら返事しない。頼みを受ける前に確かめる（`GenerateResponseUseCase._rethink_wait`）
- 上書き: `PlaySession.request_rethink` は置き換えていた（画面の見直しがコメントの指摘を消す）→ 番号つきで溜める（`_rethinks`、`rethink_reason` はつないだ文）
- 決めている間の指摘: 新しい小目標の `set_goal` で消えていた → `AdvancePlayUseCase._change_goal` が切れ目で `rethink_mark` を読み、`set_goal(rethinks_seen=)` はそれまでの理由だけ消す（後の指摘は次の切れ目で使う）
- テスト: `test_house.py`（溜まる、読んだ分だけ消す）、`test_play_use_cases.py`（決めている間の指摘が残る。直す前は落ちることを確かめた）、`test_generate_response_use_case.py`（足す、書き直し、最後まで rethink なら返事しない）

### 返事で「明るくしておくね」と約束して何もしない: Jev で返事の約束を確かめる (2026-09-27)

**Commit**: `da71576`

ユーザーのログ:「部屋の中も地下も明るくしとこうね」に「しっかり松明で明るくしておくね！」と返したが、`request: none` で lesson（L17）だけ、rethink なし。何も起きず、食料の小目標を続けた。同じ助言は前に L14 になっていた（教訓帳の表示は `logs/lessons` で 155 回中 18 回、行動に届く経路がない）。ユーザー:「やっぱり言ってることとやることが不一致」→ 案 1・2 を実装。
- 原因: 「受けるなら accept」「同意するなら rethink」はプロンプトの決まりだけで、返事と扱いの一致を確かめるコードがなかった。lesson の決まりは約束する言い方を止めておらず、条件で書ける助言（`placed(torch, home, n)` はもうある）を accept にする決まりもなかった
- `application/use_cases/promise_check.py` `PromiseCheck`: none / decline で rethink なしの返事だけ、Jev の YES_NO 1 つで「具体的な行動を約束しているか」。はい（0.6 以上）なら `GenerateResponseUseCase` が理由と直し方を付けて書き直させる（`max_attempts` 2 を頼みの差し戻しと共有、最後まで約束だけなら返事しない）。Jev の失敗・2 秒超・迷いはそのまま話す。`logs/promises/*.jsonl`
- `GenerateResponseUseCase`: 読みと教訓は使う返事からだけ覚える（差し戻した返事で教訓を二重に強めない）。差し戻しの文（`previous_error`）は理由ごとに use case が書く（プロンプトの「頼みは受けられなかった」の前置きを外した）
- 返事の決まり（`PLAN_TEMPLATE` の lesson の節、`reply_schema` の lesson の説明）: 結果が条件で書ける助言は accept（部屋を明るく → `placed(torch, home, 2)`、`lit`、`farmed`）、一部だけ書けるなら書ける部分を accept、lesson だけの返事は「覚えておくね」まで
- 設定 `twitch.response.promise_check: jev|off`、`promise_record_dir`。`factories/llm.py` `create_promise_check`、`factories/stream.py`（チャットがあるとき）、`examples/integration_test_minecraft.py`（台本のコメントがあるとき）
- 実測（本物の Jev、10 文 × 2 回）: 20/20 正しい。約束 4 文は「はい」0.77〜0.96、約束しない 6 文は「いいえ」0.65〜0.97、150〜320ms
- テスト: `tests/unit/application/test_promise_check.py`、`test_generate_response_use_case.py` の `TestEmptyPromise`
- 残り（塞いでいない）: 60 秒以内の 2 回目の rethink は間引かれて何も起きない（返事は同意したまま）。教訓を行動（条件）につなぐこと（案 3、設計の相談から）。反射の最中に 1 手を Gemini / Jev に決めさせて 0.0 秒で断られる

### 上の格の道具を作る、夜も武器で倒しに出る（持ち物はチェストへ） (2026-09-27)

**Commit**: `65a7df2`

ユーザー: 「作れる上位の道具があるなら作る（石のつるはしが作れるのに木のつるはしをずっと使っている）。夜の行動の制約を緩めたい: 敵がいて眠れないなら、アイテムをチェストに預けて武器だけ持って倒しに行く等」。
- ブリッジ `wear.mjs`: `TIERS`、`upgradesToTry`（持っているつるはし・斧・シャベル・剣より上の格、上から）、`KEEP_WHEN_FIGHTING`（武器・防具・盾・食べ物・松明）。`goals.mjs`: 今の持ち物で作れる一番上の格を `also` で出す
- `goals.mjs` の `cleared`: 夜は剣か斧が要る（なければ断る）、体力 14 未満なら `blocked`、預ける物が 8 個以上で家のチェストがわかっていれば先に `stash`、それから攻撃。`through_night` の家の中で敵がそばにいれば「眠れない。武器があれば cleared も」と出す
- `candidates.mjs` / `primitives.mjs`: `stash`（チェストに入るだけ）。`tools.mjs`: 夜も今の小目標が `cleared` なら外の敵と戦える
- Python: `predicates_now` は夜も `cleared`、`GoalChooser.survival_options` は夜・武器あり・近くに敵で `cleared` を出す、`cleared` の説明
- テスト: `test/upgrade_fight.test.mjs`、`test_play_use_cases`（夜も cleared）


### 「家の中に松明を置いて」と約束して何もしない (2026-09-27)

**Commit**: `44b83c7`, `9f262b5`

ユーザーのログ: 返事で「松明を置いて明るくしておくね」→ チャットの指摘で考え直し → Gemini は `placed(torch)` の中目標を足そうとしたが、条件なしで差し戻され（`mid goal … needs conditions`）、2 回目は「朝になってから」と夜越しに戻り、そのことを言わなかった。家の中の松明を表す条件がそもそもなかった（`placed` はベッド・作業台・かまど・チェストだけ、`lit` は家の外の地面だけ）。
- ユーザー:「家の中の松明より、任意の場所に任意のアイテムを設置できる条件を」→ `placed(item, where, count)` に広げた（松明だけの家具扱いは取り消し）
- ブリッジ `placing.mjs`: `where` は目印（`home` 部屋の中 / `near_home` 家から 3〜12m / `build:<名前>` 箱から 2m / `x,y,z` のまわり 2m）、置けるブロックの物なら何でも（`placedNames`: 壁につく松明・看板も数える）、その範囲にある数で判定、`placeSpots`（空気で下が固い、家や建物の上や家のドアの内側は外す）。`goals.mjs` は家具（ベッド・作業台・かまど・チェストを家に 1 つ）は今まで通り、ほかはこの判定で、持っていれば `place_at`、なければ作る・集める。`candidates.mjs` / `primitives.mjs` の `place_at`
- Python: `parse_spec` が placed の `where`（既定 home）と `count` を読む、スキーマに `where`、ボードの表示
- `stream_context` の placed の説明に torch、`goal_vocabulary` の条件なしのエラーに例、目標の決定の決まりに「視聴者の指摘で考え直すときは、今やるか中目標に足す（条件つき）、どちらもしないなら reason に理由。黙って戻らない」
- テスト: `test/placing.test.mjs`、`test/check.test.mjs`、`tests/unit/application/test_goal_vocabulary.py`


### 建物の箱の中で閉じ込められる: 保護を設計のセルだけに、経路がなければドアを通って (2026-09-27)

**Commit**: `f8d6cb1`

ユーザーのログ: ほぼすべての移動・採掘が 0.1 秒で「No path」、ベッドを置く所も木も「it is part of a build」で断られた（ボットは建物の箱の中にいた）。
- 原因: 建物（`state.builds`）の保護が設計図の箱全体（`inBuilds`）だった。大きな建物の箱の中では木・葉・地面を掘れず（経路の途中でも）、足場も置けず、ボットがどこへも行けなかった
- `builds.mjs` `onBuildCell`: 設計のセル（置くブロックと空けるマス）だけ。margin は横の隣（足場は壁の隣にも置かない）。セルは計画ごとにキャッシュ。`home.mjs` の `inHouse` と `protectedReason` はこれを使う（`inBuilds` は箱の判定として残す）
- `move.mjs` `walkTo`: 経路がなければ、ドアを開けて通ってよい歩き方で 1 回だけやり直す（ドアのある建物の中から出られない）
- テスト: `test/builds.test.mjs`（箱の中の空いた所は守らない）、`test/move.test.mjs`


### 夜に家とベッドへ「No path」のまま寝られない (2026-09-27)

**Commit**: `63019a7`

ユーザーのログ: through_night で `go home` / `go_home` / `sleep in the bed nearby` がどれも 0.1 秒で「No path to the goal!」、goto で近くを行き来するだけで一晩中寝られなかった。
- 原因の見立て: pathfinder は既定でドアを開けない（`canOpenDoors: false`）ので、村の家などドアの向こうのベッドには経路がない。前の家の中のベッドも近くのベッドとして出ていた。家へは `GoalBlock(ドアの外のセル)` だけで、そこに立てない（床が掘られた、物がある）と行けない。行けなかった行き先を何度でも出していた
- `move.mjs`: `markNoPath` / `isNoPathTo`（5 分）、`isNoPath`、`walkThroughDoors`（ドアを開けてよい歩き方。家のドアには使わない）
- `primitives.mjs`: `sleep_at` はドアを通って歩き、行けなければ記録して理由つきで失敗。`go_home` も行けなければ記録
- `home.mjs`: `enterHome` はドアの前に立てなければ隣まで行く。それでも行けなければ `describeDoorstep`（ドアの開閉、床・足・頭のブロック）を添える
- `goals.mjs`: 夜の選択肢から、さっき行けなかった家（`blocked` に理由）とベッド、家と前の家のベッドを外す。`bed_here` は近くのベッドがあっても出す
- テスト: `test/night.test.mjs`


### ローカル LLM（FreeToken / Ollama）と Gemini の振り分け (2026-09-26)

**Commit**: `4a85c53`, `4f697eb`

ユーザー: 「費用節約のため Qwen3.6（ローカル、Vision なし）に切り替えて様子を見たい。Vision だけは Gemini。コンテキストの違い、モデルが 2 つになるところ」→ FreeToken で動かしている → 「JSON_SCHEMA がないと厳しそう。Ollama も考えている。速さはどのくらい変わる？」。設計書 `docs/design/36_local_llm.md`。
- `infrastructure/adapters/local_llm/`: `OpenAICompatTextGenerator`（スキーマをプロンプトに書き、response_format も送る、答えを確かめて 1 回書き直させる、道具は tools + required、文なら JSON で選ばせる、思考は用途の深さ、断られた指定は外して以後送らない）、`RoutingTextGenerator`（用途、画像はいつも Gemini、長さ、fallback）、`schema_check`（JSON の取り出し、スキーマの確かめ、トークンの見積もり）
- `factories/text.py`（`create_text_generator`、`create_gemini_generator`）を `factories/game.py`・`llm.py` が使う。`llm=settings.llm` を stream と examples から渡す
- 設定 `llm.local.*`・`llm.routes`・`llm.fallback`（`LOCAL_LLM`、`LOCAL_LLM_URL`、`LOCAL_LLM_MODEL`）、`config.py` の `LlmSettings`・`_truthy`
- `tools/llm_probe.py`（スキーマで縛るか、道具、最初のトークンまで、読み込み・出力の速さ、先頭のキャッシュが別の先頭を挟んで残るか）、`tools/gemini_usage.py` は `local:<用途>` を分ける。Gemini のデバッグの記録に `model`
- テスト: `tests/unit/infrastructure/adapters/test_local_llm.py`
- ユーザーの FreeToken（192.168.1.43:1919、Qwen3.6）での `llm_probe` の結果（2026-09-26）: `response_format` は 400「no constrained decoding」（→ プロンプトのスキーマと確かめで動く）、道具は呼べた（日本語の intent つき）。約 1.2 万トークンの初回は最初のトークンまで 4.6 秒（読み込み約 2,500 t/s）、同じ先頭の 2 回目 0.9 秒、別の先頭を挟んだ 3 回目も 0.8 秒（先頭のキャッシュを複数持てる）、出力約 70 t/s。Ollama に替える理由は今のところない
- ユーザー（2026-09-27）: 「thinking: on だとめちゃ遅いので普段の操作判断は off に」→ `llm.local.thinking_purposes`（既定 skill_write / house / town / site だけ思考。道具の 1 手、目標の決定（失敗の後も）、実況、返事は思考なし）
- `.env` の `KEY=   # 説明`（値が空でコメントだけ）がコメントを値にしていた（モデル名が「# 空なら…」になった）: `load_env_file` を直した


### 教訓帳: 指摘と失敗を似た状況で思い出す (2026-09-26)

**Commit**: `d2e8cb3`（テストと設計書の仕上げは次のコミット）

ユーザー: 「過去の指摘などの記憶を、似たような状況で思い出すのが難しそう。指摘とその条件をうまく覚える方法は」→ 提案に「一旦それでやってみます」。設計書 `docs/design/35_lessons.md`（`6b86f92`）。
- `domain/value_objects.py`: `Lesson`（本文、条件の文、状況のキー、explicit、出どころ、taught/used/helped/failed）、`SITUATION_VALUES`。`Activity.lessons`、`Goal.lessons`、`PlaySession.lessons`
- `application/use_cases/lessons.py`: `situation_of`（goal/item/time/place/body/pickaxe/ended）、`ended_kind`、`clean_keys`、`score`（explicit は合わない条件で外し、全部合えば +1）、`LessonBook`（learn: 同じ教訓は強める / recall: コードで絞って Jev の YES_NO、答えなし → 上位 3 / mark_used / settle / prune）。`ports/output/lesson_store.py`、`storage/json_lesson_store.py`
- 入り口: 返事の `lesson` + `lesson_when`（教訓帳があればメモではなくこちら）、失敗の後の決定の任意の `lesson` + `lesson_when`（スキーマの最後）。キーがなければ状況のスナップショット
- `play.py`: `_change_goal` で実績を数え（settle）、Gemini が決めるときは終わった小目標の状況と一番上の中目標の手順で思い出して `Activity.lessons` に。`_start_goal` で新しい小目標で思い出し、`Goal.lessons`（Jev の `remember`）と `session.lessons`
- プロンプト: `stream_context._format_lessons`（「思い出したこと」、確かめていない）、失敗の後のルールと返事のルールに lesson / lesson_when、Jev の指示に remember
- 設定 `minecraft.agent.lessons*`、`factories/game.py`・`llm.py`・`stream.py`、`GameService.lessons`
- テスト: `tests/unit/application/test_lessons.py`、返事と play の結合テスト

### アバターを既定で全身表示 (2026-09-26)

**Commit**: `bfc9830`

ユーザー: 「avatar ページのアバター、全身収まるように表示してほしい。OBS 側で任意の部分を表示するので」。`avatar.html` の既定を全身・中央に（両手を挙げても切れる高さ、両腕を広げた幅も収まる距離、縦長でも）。`?view=bust` で前の胸から上。Playwright で 1920x1080 と 1080x1920 を確かめた。

### 建物の設計の 400 の続き: スキーマが原因、同じ建物を足し続ける (2026-09-27)

**Commit**: `bc53095`

- ユーザーのログ: 画像も解像度も外しても 400（プロンプト 1299 字、スキーマつき）→ 建物の設計のスキーマ（`BUILD_SCHEMA`: 形の配列 1〜40、各形に enum と点の入れ子）が原因の見込み。ほかの用途のスキーマは通っている。さらに、建物を落とした後「中目標がない」で差し戻され、Gemini は 3 回とも同じ storehouse を足して、決定が通らなかった
- `GeminiTextGenerator.generate_json`: スキーマつきで 400 なら、スキーマをプロンプトの終わりに書いて（response_json_schema を外し、JSON で答えさせるのはそのまま）1 回やり直し、通ればその用途では以後そうする（ログに「スキーマをプロンプトに書くと通った」）
- `BUILD_SCHEMA`: minItems / maxItems を外した（数は BuildDesign が確かめる）
- 目標の決定: 設計できなかった建物は 10 分間足さない（`_undesignable`）。落としたことを差し戻しの理由に書いて Gemini に伝える（「別の中目標を」）
- テスト 2 件。pytest 700

### 建物の設計がいつも 400、そのせいで空腹の決定まで差し戻された (2026-09-27)

**Commit**: `2b37749`

- ユーザーのログ: `build_design`（地図の画像つき、thinking high → medium）が毎回 400「Request contains an invalid argument」→ 3 回とも設計できず、`storehouse` の中目標の追加だけでなく、満腹度 0 で食料を取る決定ごと差し戻された
- 原因はまだ確かめられていない（この環境に API キーがない）。画像つきの用途は build_design だけが `media_resolution: medium`（ほかは low）。候補: 解像度の指定、地図の画像、スキーマ
- `GeminiTextGenerator`: 400 のログに画像の種類・大きさ・先頭 8 バイトと media_resolution を出す。画像つきの 400 は media_resolution を外して 1 回やり直し、通ればその用途では以後外す（ログに「media_resolution を外すと通った」）
- `BuildDesigner`: 画像つきで生成できなかったら、次の試行から地図なしで設計する（置き場所は家の横か近く）。3 回だめなら `BuildNotDesignedError(name)`
- 目標の決定: 建物を設計できなければ、その建物の中目標の追加と手順だけ落とし、残り（食べるなど）は通す（`_without_build`）
- テスト 4 件。pytest 698。原因の切り分けはユーザーの環境のログで（地図なしで通るか、解像度を外して通るか）

### 行動の記録から行き詰まりを Jev が定期的に確かめる (2026-09-26)

**Commit**: `a73a8b1`

- ユーザー「行動履歴から行動がスタックしていないかも Jev で定期的にチェックしたい。その場合も Gemini に判断し直してもらいたい」
- `application/use_cases/stuck_check.py` `StuckCheck`: 小目標の行動 6 回ごとに、行動の記録（最新 12、結果と位置）と進み具合を Jev に見せ、fine / repeating / failing / back_and_forth / no_progress を選ばせる。fine 以外を確信度 0.75 以上なら `session.request_rethink`（理由に「is stuck」: Gemini は行動の記録で原因を分析する）。小目標が変わったら記録を消す
- `AdvancePlayUseCase(stuck_check=)`（候補モードでも道具モードでも）。設定 `minecraft.agent.stuck_check_steps`（0 で切る）、`stuck_min_confidence`、`stuck_record_dir`（`logs/stuck`）。設計書 34 §9
- テスト 7 件。pytest 694

### 設計書 34 の見直しの直し (2026-09-26)

**Commit**: `166e35d`

- 読み上げの感情: モデルにないスタイル（`development.yaml` の `emotion_style_map` が Happy などを指すが、スタイルをまだ足していない）は 422 で文が読まれなかった → 接続のときに `/models/info` からスタイルを読み（`styles_of`: 番号ごとの答えは model_path で探す）、ないものは Neutral で読む（スタイルごとに 1 回警告）
- コメントの仕分け: 仕分けを待つ間に届いたコメントで KeyError / 空の列で IndexError になりえた → 仕分けていないものがなくなるまで繰り返し、空なら何もしない
- 実況の間合い: イベントの発行はハンドラーを待つので、Jev に聞くのを待つとプレイのループが止まった → 聞くところからバックグラウンドに
- 失敗の後の振り分け: 種類が交互の失敗（A→B→A）は Gemini に回らなかった → 種類を問わず 3 回続けば Gemini
- 1 手: 断られた `do_suggestion`（観測のあとで候補が消えた）を Jev の失敗・道具の失敗に数えない
- 設計書 34 の設定の名前（`stream.commentary_judge`）と上の決まりを直した。Current Status を今に。pytest 687

### Jev でできることは Jev に: 5. 失敗の後の振り分け (2026-09-26)

**Commit**: `7b94f9e`

- 行き詰まった・進まなかった小目標の後、まず Jev（`GoalChooser.choose(avoid=失敗した小目標)`）が、一番上の中目標のまだ済んでいない別の手順（同じ種類は外す: 同じやり方を繰り返すには Gemini の分析と助言が要る）か `rethink`（Gemini が原因を分析する: 27）を選ぶ。Gemini に回す: 同じ種類の失敗が 2 回続いた、死んだ、中目標が進まない、画面で考え直す、別の手順がない、Jev が迷う
- `AdvancePlayUseCase(failure_routing=)`、`_needs_gemini`。設定 `minecraft.agent.failure_routing: jev | gemini`
- streaming.md に新しい記録（logs/steps、chat、commentary）。テスト 2 件。pytest 684
- これで設計書 34 の 5 つ（1 手、声の感情、コメントの仕分け、実況の間合い、失敗の振り分け）が入った。どれも実機では未確認。閾値は記録を見て直す

### Jev でできることは Jev に: 4. 実況の間合い (2026-09-26)

**Commit**: `10a9d07`

- `application/use_cases/commentary_gate.py` `CommentaryGate`: 小目標の切れ目で「今話す価値があるか」（yes/no、出来事と黙っている秒数と今の小目標）。「話さない」は確信度 0.6 以上のときだけ。Jev が答えなければ話す。`logs/commentary/*.jsonl`
- `Narrator(gate=, max_silence_seconds=)`: 見どころ（中目標の完了・やめた、家、街の場所・完成）と、60 秒黙っていたとき・最初は聞かずに話す。話さなかった出来事は取っておき（8 まで）、次に一緒に話す。技を覚えたときは前と同じくすぐ話す
- 設定 `stream.commentary_judge: jev | always`、`max_silence_seconds`、`commentary_record_dir`
- テスト 2 件。pytest 682

### Jev でできることは Jev に: 3. コメントの仕分け (2026-09-26)

**Commit**: `0f6d307`

- `application/use_cases/comment_triage.py` `CommentTriage`: 1 回の Jev で「返事をするか」と種類（request / advice / withdraw / question / chat / noise）。頼み・助言・取り下げ・質問は必ず返事。返事をしないのは chat / noise で「要らない」、両方の確信度 0.7 以上のときだけ。Jev が答えなければ返事をする。`logs/chat/*.jsonl`（コメントの文ごと）
- `ChatResponder(triage=)`: 待っているコメントを仕分けて、取り下げ・頼みを先に。返事をしないものは捨てて `skipped` に数える
- 設定 `twitch.response.triage: jev | off`、`skip_min_confidence`、`triage_record_dir`。factory は Twitch を読むときで TypeSafe のキーがあるとき
- テスト 8 件。pytest 680

### Jev でできることは Jev に: 2. 読み上げの感情 (2026-09-26)

**Commit**: `d2a029a`

- それまで声はいつも中立だった（`LLMService.update_emotion` を呼ぶところがなかった）
- 読む前に `AvatarDirector.react("speaking", 文, 規則)` を 1 回呼び、`voice_emotion` で声の感情（happy/sad/angry/surprised、relaxed と中立は中立、強さはそのまま）にして `TTSService.speak(emotion=)` に渡す（`factories/stream.py` の say / say_reply）。同じ反応は `LineReactions` に覚え、読み上げが始まったらアバターがそのまま使う（同じ文で Jev をもう一度呼ばない）。Jev が答えなければ前と同じ（中立、アバターは規則）
- `factories/avatar.py` `create_avatar_director` を分けた。設定 `tts.emotion_judge: jev | off`
- テスト 2 件追加。pytest 672

### Jev でできることは Jev に（設計書 34）: 1. 道具モードの 1 手をまず Jev が選ぶ (2026-09-26)

**Commit**: `fd68f31`

- ユーザー「Jev の役割を増やせないか。Jev でも可能な部分はなるべく Jev にしたい」→ 5 案を「順に実装して」。設計書 34（共通の決まり: 設定で切り替え、時間切れ・失敗・自信なしは前のやり方、記録、済んだかは判定させない、選択肢に順位をつけない）
- `application/use_cases/step_picker.py` `StepPicker`: 候補モードと同じ `IActionSelector`（Jev）に、ブリッジの候補（16 まで）、成功していて引数の要らない技（5 まで）、`ask_gemini` を見せる。選んだら `do_suggestion(id)` / `run_skill(name)` を Gemini が選んだときと同じ経路（`ToolWatcher.run` / `_run_skill`）で実行し、`_recent_tools` に残す。Gemini に回す: ask_gemini、確信度 < 0.5、3 秒、失敗、直前の道具の失敗、Jev の手が 2 回続けて失敗、Jev が 8 回続けた
- `AdvancePlayUseCase(step_picker=)`、`_jev_step`。設定 `minecraft.agent.step_picker` など、`logs/steps/*.jsonl`。factory は道具モードで TypeSafe のキーがあるとき
- テスト: `tests/unit/application/test_step_picker.py`（11）。pytest 670。実機では未確認

### 道具モードを既定に (2026-09-26)

**Commit**: `9dd74a4`

- ユーザー「最終的に操作を決めているのは誰？」→（候補モードではブリッジが座標まで決め、Jev が選ぶ。道具モードでは Gemini が座標まで指定できる）→「道具モードだけにしていい」
- `minecraft.agent.control` の既定を `tools` に（`config/default.yaml`、`MinecraftSettings`）。候補モードのコードは残す（`--control candidates` で比べられる。道具モードの `do_suggestion` もブリッジの候補を使う）。CLAUDE.md、streaming.md を更新。pytest 659

### ブリッジの自動再起動（npm run dev） (2026-09-26)

**Commit**: `5ae209b`

- ユーザー「ブリッジに修正を入れるたびに再起動するのが面倒。ホットリロードにできる？」
- 本当のホットリロード（ボットとミラーの接続を保ったまま、目標・候補・行動のモジュールだけ差し替える）は、ESM のモジュールのキャッシュ、ボットに付けたリスナー、pathfinder の設定がモジュールの中にあるため大きな作り替えになる。やらない
- `npm run dev`: `node --watch-path=src`（`src/` が変わると再起動）。`index.mjs` に SIGINT / SIGTERM の片付け（実行中の行動を止め、`state.json` を保存し、`bot.quit`）。ミラーにつないだクライアントは切れる（本番は `npm start`）。Node 22 で再起動と SIGTERM の片付けが動くのを確認。npm test 194

### 感情の声: Irodori-TTS で読ませて、Style-Bert-VITS2 のスタイルに (2026-09-26)

**Commit**: `0a9550c`

- ユーザー「shen_sbv2 はそれっぽくなった」→「Irodori-TTS に shen_sbv2 の声と話し方の説明で感情をつけて読ませたものを、Style-Bert-VITS2 の各スタイルの参照音声にしたい」
- `irodori_from_sbv2.py emotions`（`--source irodori|sbv2|auto`）: 台本の感情の文を Irodori-TTS（声 shen_sbv2 + `emotion_captions`、seed なし）で読ませ、`data/irodori_train/shen_sbv2_<感情>/audio/`、声 `shen_sbv2_<感情>` として登録。`--register-only`
- `tools/sbv2_add_styles.py`（Style-Bert-VITS2 の学習環境の Python で）: 感情ごとの埋め込み（`style_gen.get_style_vector`）の平均を `style_vectors.npy` / `config.json` に足す・置き換える。Neutral は残す（公式 WebUI の方法0 は Neutral を作り直すので使わない）。バックアップ付き。偽の style_gen で Neutral が変わらないことを確認
- `IrodoriTtsClient(emotion_voices=)`、`tts.irodori.emotion_voices`。`StyleBertVits2Client(style_weight=)`、`tts.synthesis.style_weight`
- 手順: `docs/setup/irodori_tts.md` §8。pytest 659。実機（本物の Irodori-TTS と pyannote）では未確認

### Irodori-TTS がどの参照音声で読んでいるかを確かめる道具 (2026-09-26)

**Commit**: `d5ace23`

- ユーザー「Style-Bert-VITS2 で作った音声を参照音声にできていないかも」
- 疑い: 配信と tts_compare は `tts.irodori.voice`（`IRODORI_VOICE`、既定 `shen`: 生の学習データから選んだ、別の人の声が混ざりうる参照音声）を使う。`shen_sbv2` を作っても .env を変えなければ使われない。サーバーが起動前の voices.json のまま、でも同じ
- `tools/irodori_check.py`: 設定の声、サーバーの /health・声の一覧・その声の中身、手元の voices.json とファイル、`--say` でその声 / 参照音声なし / 設定の声の WAV（`logs/irodori_check/`）

### Irodori-TTS で途中から別の男性の声になる (2026-09-26)

**Commit**: `a64836b`

- ユーザー「途中で全然関係ない男の人の声が入る」「半分くらいは参照音声に近い声」
- 見立て: 参照音声が長い語り 3 本（各 10 秒）だった。Irodori-TTS の docs/parameters.md は、v4-Small は短い発話をつないで学習していて、同じ話者の短いきれいなクリップを合計 30 秒ほどを勧める。似せ方が弱いと、生成の途中で学習データの別の話者に流れる。私が勧めた `cfg_scale_speaker: 5.0` はサーバーの既定と同じで、変化はなかった（誤り）
- `irodori_from_sbv2.py reference`: ふつうの文の 2.5〜6 秒のクリップを台本の順に合計 30 秒（`--clip-min` / `--clip-max`）。偽のサーバーで 9 個 28 秒を確認。実機の効果は未確認

### irodori_from_sbv2 generate が全部を「音割れ」で捨てる (2026-09-26)

**Commit**: `44bf28c`

- ユーザーの実行: `generate --for-reference` で「使う 0 文、除いた 64 文」
- 原因: Style-Bert-VITS2 のサーバーは出力を最大値ちょうどに揃えて返す（`convert_to_16_bit_wav`: `data / abs(data).max() * 32767`）ので、どの音声も最大振幅が 0.99997。判定が「最大振幅 0.999 以上は音割れ」だった
- 直し: 音割れは最大値（32767）そのものに 3 サンプル以上続けて張り付いたときだけ（なめらかな山は最大値に 1 サンプルしか届かない）。テストに「最大値に揃えただけは音割れではない」を追加。pytest 657

### Style-Bert-VITS2 の音声から Irodori-TTS の声を作る (2026-09-26)

**Commit**: `4640675`

- ユーザー「生の学習データは雑音が多い。Style-Bert-VITS2 の音声はイントネーション以外はよいので、それを元に Irodori-TTS を学習させたい」
- `tools/irodori_from_sbv2.py`: `generate`（台本 673 文を Style-Bert-VITS2 で読ませ、長さ・音割れ・無音・読みの速さで外れたものを除き、audiofolder の `metadata.csv`）、`reference`（語りとふつうの文から 60 秒を参照音声に: 学習なし、イントネーションは Irodori-TTS のまま。まずこれ）、`base` / `prepare` / `train`（Irodori-TTS の学習用リポジトリの `prepare_manifest.py` と `train.py` を LoRA の設定で。少データ向けに batch 4 × 4、3000 歩、500 歩ごとに保存）
- `scripts/irodori/setup_train_mac.sh`（~/Irodori-TTS）。`IrodoriTtsClient(lora_adapter=)`、`tts.irodori.lora_adapter`（`IRODORI_LORA`）
- 手順: `docs/setup/irodori_tts.md` §6。学習は SBV2 のイントネーションも覚えうること、MPS での学習は上流で確かめられていないこと（だめなら CUDA のマシンで）を明記
- テスト: pytest 656（判定、台本の読み込み、LoRA の要求）。generate と reference は偽の Style-Bert-VITS2 サーバーで確認。prepare / train は実行していない（学習用リポジトリと重みがこの環境にない）

### ITA コーパスを台本に、行ごとの録音ボタンのページ (2026-09-26)

**Commit**: `7c4af33`

- ユーザー「ITA コーパスも入っている？」→「調べてこれない？」: 公式（github.com/mmorise/ita-corpus）でパブリックドメインと確認。`docs/voice/ita_corpus.tsv`（EMOTION100 + RECITATION324、ふりがなと読みつき）。`tools/voice_dataset.py` が両方の台本を読む（ITA は Neutral、足りない録音は区分ごとに）
- ユーザー「台本を web で見られるようにして、1 文ごとに録音ボタン、その行のファイル名で保存」: Artifact のページはマイクもファイルの保存もできないので、ローカルのページにした。`tools/voice_recorder.py`（標準ライブラリの HTTP サーバー、127.0.0.1:8770、`PUT /api/recordings/<番号>` で `data/voice_recordings/<番号>.wav`、前のものは `_previous/`）+ `tools/voice_recorder.html`（AudioWorklet でマイクから 16bit モノラル WAV、自動音量・雑音除去なし、0.2 秒の前取り、入力の大きさと音割れの印、Space / ↑↓ / P、録ったら次の行へ）。Chromium の偽のマイクで録音・保存・上書きを確認
- 読むだけのページ（Artifact）も ITA を足して 673 文に更新

### 声の録音の台本と、録音から学習データを作る道具 (2026-09-26)

**Commit**: `cfee64b`

- ユーザー「モデルにする人に好きな言葉を喋ってもらえる場合の選択肢」→「台本を作ってほしい」
- `docs/voice/recording_script.tsv`（249 文: ふつう 60、語り 4（Irodori-TTS の参照音声）、楽しい 25、驚き 25、悲しい 25、むっ 20、怖い 20、実況 40（アイテム名、数字、座標、英字、数え方）、相づち 30。キャラクター設定の話し方（私、だよ・だね））。ITA コーパスなどの文は写していない（すべて書き下ろし）
- `docs/voice/README.md`（区分、録り方、録音後の流れ、感情とスタイルの対応、同意）
- `tools/voice_dataset.py`: 録音（<番号>.wav）を Style-Bert-VITS2 の `Data/<model>/raw/<スタイル>/` に写し、台本の文で `esd.list` を書く（文字起こし不要。2.5.0 以降はサブフォルダーごとにスタイル: Neutral / Happy / Surprised / Sad / Angry / Fear）。足りない録音を表示、`--dry-run`
- 読む人のための台本のページ（Artifact、録音済みの印はその端末だけに残る）: https://claude.ai/artifact/UG76jN4QpA2ukaXvZZ1xX3

### Irodori-TTS を Mac で動かせるようにする（読み上げの方式の切り替え） (2026-09-26)

**Commit**: `ec644be`

- ユーザー「自然な日本語の発音と安定性を重視」「Mac（M5、32GB）で動かしたい、セットアップしてほしい」。候補から Irodori-TTS（日本語専用、MPS に公式対応）を選んだ（設計書 32 §4.1）
- `infrastructure/adapters/tts/irodori_tts_client.py` `IrodoriTtsClient`（`POST /v1/audio/speech`、WAV、`/health` で接続確認、声がなければ警告、seed 固定、感情は caption（既定は切る））
- `factories/tts.py`: `tts.engine`（`TTS_ENGINE`、style_bert_vits2 / irodori）、`create_synthesizer`、`describe_engine`。sounddevice は読み上げるときだけ読み込む。`factories/stream.py` のつながらないときの直し方を方式ごとに
- `config/default.yaml` `tts.engine`、`tts.irodori`。`.env.example`
- `scripts/irodori/setup_mac.sh`（~/Irodori-TTS-Server に取得、`uv sync --extra cpu`、.env に mps / fp32 / preload / 参照音声の置き場）、`start_mac.sh`（`PYTORCH_ENABLE_MPS_FALLBACK=1`）
- `tools/irodori_voice.py`（学習データから 3〜12 秒のクリップを合計 60 秒まで選び、`data/irodori_voices/voices.json` に）、`tools/tts_compare.py`（同じ 20 文を方式ごとに読ませ、WAV と合成時間の report.md）
- 手順書 `docs/setup/irodori_tts.md`、`docs/streaming.md` に切り替えの案内
- テスト: pytest 652（アダプターと切り替え）。2 つの道具は偽のサーバーで動作を確認。実機（Mac）ではまだ動かしていない

### 同じ土を掘っては置く無限ループ (2026-09-26)

**Commit**: `4bfd2f6`

- ユーザーのログ: `(-78, 39, -116) の dirt を掘る` が延々と続く。掘るとすぐ土を置いていた
- 原因（見立て）: mineflayer-pathfinder は経路のそばのブロックが変わるたびに経路を作り直す（`blockUpdate` → `resetPath`）。掘ると次の経路がそこに足場の土（`scafoldingBlocks = dirt`、`allow1by1towers`）を置き、置くとまた掘る経路になる。前のコミットの「動けない」見張りは掘る・置く間を数えていなかったので止まらなかった
- `move.mjs` `guardDigLoops`: 同じブロックを 1 分に 3 回掘ったら、そのセルを 3 分間 掘らない・置かない（Movements の除外、`state.loopCells`）にして経路を作り直させる。動けない見張りは、掘る・置く間も 5 倍（30 秒）進まなければ動けないとみなす
- テスト: npm 194

### 道具が壊れたらすぐ気づいて作り直す、歩いて動けないときは歩き方を変える (2026-09-26)

**Commit**: `3ec7af2`

- ユーザー「ツルハシが折れたら作り直してほしい。指摘してもしばらく持っていると勘違いしている」「移動しようとしているのに位置がしばらく変わらないときは移動方針を変えてほしい（操作の精度で脱出できなくなる）」
- ブリッジ `src/wear.mjs`: 持ち物のスロットの更新で、耐久を使い切った道具が消えたら壊れたと記録（`state.brokenTools`）。観測に `broken_tools`（何分前、作り直したか）。行動の結果に「the X broke」を添える（runner）。同じ種類をもう持っていなければ、作るだけでできるとき作り直す候補を `also` で出す（goals.evaluate）。道具が要るブロック（石など）を道具なしでは掘らず、「no tool that can mine stone (the stone_pickaxe broke): make one first」で失敗にする（dig、dig_down。素手で掘り続けていた）
- Python: 状況に「壊れた道具: stone_pickaxe（1分前、もう持っていない）」。チャットの返事の前に `GameService.refresh()` で様子を取り直す（行動の途中の変化が返事に入る）
- ブリッジ `src/move.mjs`: 移動（walkTo）の間、掘る・足場を置く以外で 6 秒 0.5 m 未満しか動かなければ止めて、跳んで抜け出す → 走り・飛び越えなしの慎重な歩き方 → 数ブロック下がる、の順に試してやり直す。それでもだめなら「stuck: did not move for 6s while walking (tried: …)」で失敗
- テスト: npm（wear.test.mjs、move.test.mjs に追加）、pytest 646

### 夜の越し方を柔軟に: 近くのベッド、持っているベッド、地下 (2026-09-26)

**Commit**: `b1bf030`

- ユーザー「日暮れに必ず家に戻るのも絶対ルールではない。近くの村のベッドで寝たり、地下にいる時は寝なくても良いし、ベッドを地下に持っていっても良い」
- ブリッジ: `through_night` は家がなくても出せる。家の中ならベッドで寝る。外なら家に帰る（家があれば）、寝られる時間なら 32 m 以内の家以外のベッドで寝る（`sleep_at`）か、持っているベッドをその場に置いて寝る（`bed_here`、置いたベッドは残り、復活地点になる）、地下（y < 60 で屋根の下、`observation.underground`）ならそのまま過ごす（`stay_underground`）。どれを選ぶかは Jev
- Python: `GameObservation.underground`。GoalChooser の夜・夕方の生き残りの選択肢は地下なら出さない。夕方は家が遠ければその場で夜を越す選択肢も。目標のシステム指示・ABILITIES・述語の説明を「越し方は状況で選ぶ（決まりではない）」に
- テスト: npm（night.test.mjs 追加）、pytest 644

### 階段掘りの深さの上限をなくす (2026-09-26)

**Commit**: `925aec3`

- ユーザー「深さの制限で階段掘りが止まったと言われた。特に制限なくて良い」
- 今までは、小目標に Gemini が dig_depth を付けたときだけ、その深さまで下りた（付けなければ下りない、最大 64）
- 既定を上限なしに: 埋まった石・鉱石まで下りる（掘るか探すかはソルバーの費用で決まる）。止まるのは溶岩・水・砂利・岩盤のそば、見えていない所、家。dig_depth は任意の上限（0 で下りない、最大 384）として残す。目標のシステム指示とスキーマの説明も書き換えた
- テスト: npm 188、pytest 644

### 視点の中継が「74 bytes extra whilst reading packet set_equipment」で切れる (2026-09-26)

**Commit**: `f9f75e5`

- ミラーは set_equipment（モブ・プレイヤーの手と防具）を生のバイト列で中継しているだけだが、部品（附呪、名前、NBT など）つきのアイテムで本物のクライアントが切れた（原因のずれは特定できていない）
- `mirror.mjs sanitizedEquipment`: 部品のある装備だけ、持ち物と同じく数だけの部品（damage など）に絞って 1.21.4 の形で書き直して送る（部品がなければ今までどおり生で中継。書き直せなければ送らない）。記録（後から来た視聴者への再生）も同じもの
- テスト: npm 188（mirror 1）

### 焼いた物を先に食べる・肉はなるべく焼く、草刈りの打率 (2026-09-26)

**Commit**: `93cbd2c`

- ユーザー「焼いたものを優先。手持ちの肉はなるべく焼く。草刈り（小麦の種集め）の打率が悪くハマることが多い」、Gemini の失敗の分析「段差の窪みに立っていて、上の段の草へ回り込む経路探しが時間切れ。頭上の草が刈れる」
- 食べる（`candidates.mjs forNeeds`）: 焼いた物・生肉でない物を先に。生肉しかなければ、焼けるなら焼くまで待つ。生で食べるのは飢えそう（満腹度 6 以下）か、焼く手段がなく 12 以下のとき
- 焼く（`cooking.mjs`）: 見えるかまどを覚え（`state.lastFurnace`）、見えなければ生肉が 3 個以上でそこへ焼きに行く候補
- 草刈り（`dig`）: 手が届く草はその場で刈る（回り込まない）。1 回の行動で、何か落ちるまでまわり 5 m の草・シダを続けて刈る（12 本・10 秒まで）。葉から苗木も同じ
- テスト: npm 187

### コメントで教わったことを覚える、もっともなアドバイスを断らない (2026-09-26)

**Commit**: `112c03f`

- ユーザー「コメントで得られた教訓はちゃんと覚えて。何度も肉は焼いてから食べるほうが良いと言われている。作業台をその場で作ったほうが良いとも言われているが、今は別の作業してるからと拒否している」
- 原因: メモ（設計書 18）は目標の決定だけが書き、返答からは書かない決まりだった。返答はやり方のアドバイスを none / decline にしていた
- 返答の `lesson` → `NoteKeeper.learn_from_viewer`（lesson のメモ、根拠「コメントで教わった（〇〇さん）」、同じ教訓は寿命を延ばす）。メモは目標の決定・実況・返答が見る。返答の決まりに「視聴者のアドバイス」の節。`GameService.notes` を返答に渡す。設計書 18 に追記
- 前の変更で消していた返答の決まりの見出し「視聴者の名前の読み」を戻した
- テスト: pytest 644

### 種集め（have(wheat_seeds)）が毎回失敗して行き詰まる (2026-09-26)

**Commit**: `2e983ba`

- ユーザーのログ: `goal have(wheat_seeds, 9) is stuck (actions keep failing)`
- 原因: 掘る行動は「ブロックの面が見える位置」（GoalLookAtBlock）へ歩くが、草は当たり判定がなくレイが通り抜けるので、その位置が決して見つからず毎回失敗した
- 当たり判定のないブロック（草、花、作物、苗木）は届く距離（2 m）まで近づいて掘る。種が落ちなかった掘り（8 回に 1 回ほどしか落ちない）は「進まない」「失敗」に数えない（`ActionResult.missed_by_chance`）
- テスト: npm 186、pytest 642

### 狩りは剣で、なければ先に作る (2026-09-26)

**Commit**: `d3b9f89`

- ユーザー「羊や牛を狩るときは剣を使ってほしい。なければ積極的に作ってほしい」
- `attack` は始めに一番よい武器（剣 → 斧）を持つが、追う途中で掘ると道具に持ち替わり、そのまま殴っていた: `fight` で殴るたびに武器に戻す
- `goals.mjs`: 目標の候補に狩り（kill）があって剣を持っていなければ、剣を作る手順を足す（`also: a sword for hunting`、「進まない」に数えない）。作るだけでできる（板材と棒がある）なら狩りの候補を外して先に作る。材料集めからなら狩りと並べる。`candidates.mjs`: 葉の also 印をその葉から出た候補すべてに付ける
- テスト: npm 185（food 1）

### 掘るときの道具の選び方（原木をツルハシで、なんでも素手で） (2026-09-26)

**Commit**: `a61446d`

- ユーザー「採取時のアイテム選択も Jev にやらせてみたい。なんでも素手、木をツルハシで取っていて微妙」
- 原因: mineflayer-pathfinder の `bestHarvestTool` は、素手より速い道具がないと持ち物の最初のもの（ツルハシ、土…）を返す。掘る行動（`equipToolFor`）も歩きながら掘るのもこれを使っていた
- `chooseTool`（primitives.mjs）: 採るのに要る道具（石にツルハシ）か、素手より速い道具のうち一番速いもの（同じ速さなら安い方）。壊れかけ（残り 5%）は要るときだけ。なければ素手（道具を持っていれば外す）。`bot.pathfinder.bestHarvestTool` も差し替えた
- Jev にはしなかった: どれが要る・速いかは計算で決まり、掘るたびに Jev を呼ぶと 0.2〜0.6 秒遅れる。判断が要る場面（良いツルハシを取っておく等）があれば Jev に回せる
- テスト: npm 184（tools_choice 2）

### 死んで遠くに戻された後、家のドアやチェストへの行動が全部時間切れで止まる (2026-09-26)

**Commit**: `13747d2`

- ユーザーのログ: スポーンが (204, 64, -114)、家は (-76, 65, -151)（約 280 m）。`place door`（20 秒）、`take 2 beef from the chest`（45 秒）が毎回 timeout で失敗し、操作が止まったように見えた
- 遠い所への行動は区間（48 m）ごとに歩くが、区間の途中で時間切れになると、進んでいても「失敗」になり、続けて失敗すると小目標が行き詰まり扱いになっていた
- `runner.mjs`: 時間切れの理由に、そのときの段階と進んだ距離（`timeout (while walking toward home (280m away); moved 12m)`）。歩いている段階（`bot.actionPhase` が walking…、`legToward` が書く）で 8 m 以上進んでいれば、成功（`walked toward …: 20m this time, not there yet`）として返す
- テスト: npm 182（runner 1）
- 残り: 死んだ後に家から遠い所で復活する（ベッドで寝ればスポーン地点が家になる）

### ドアの置き直しの時間切れ（続き）: どこで止まったかをログに出す (2026-09-26)

**Commit**: `0555fcd`

- まだ `place door ...: failed: timeout（20.1秒）` が続く（ユーザーがブリッジを再起動したかは未確認）。どの段階で止まっているかが分からないので、`runner.mjs` の時間切れの理由に `bot.actionPhase`（プリミティブが書く今の段階）を添える: 例 `timeout (while walking to the outside of the door)`、`leaving the house`
- `placeDoor`: ボットに近い側から立つ（前は必ず外から）、片側 6 秒、古いドアを壊すのは 6 秒まで（壊れなければ止めて失敗に）、段階を記録
- テスト: npm 181

### ドアの置き直しが毎回 20 秒で時間切れ (2026-09-26)

**Commit**: `2ff3a0a`

- ユーザーのログ: `[act] place door at -76,65,-151: 失敗 failed: timeout（20.1秒）`。ドアの前に立つまでの歩き（遠い、または片側に行けず経路探しが長引く）で 20 秒を使い切っていたと見ている
- ドアから遠ければ、まず 48 m ずつドアへ歩く（1 回で終わらせようとしない）。ドアの前（外・内の各側）へは 8 秒で着かなければ反対側を試す。時間切れもドアの失敗に数える（3 回で諦める。前の変更では時間切れを数えていなかった）
- テスト: npm 181

### 「place door」で失敗し続けて固まる (2026-09-26)

**Commit**: `6bd036f`

- ユーザー「ずっと place door で失敗して固まっている」。ログの失敗の理由は未確認。コードで見つけたこと:
  - 家に入る・夜を越す・家具を置く目標は、家の計画のドアが「向きが違う」と、それを直す候補（place_plan door）だけを出して先に進まない。ところが `place_plan` の実行は、候補が指したドアではなく計画の最初の未完成のブロックを置いていた。ほかに未完成のブロックがあると、ドアを選んでも別のブロックで失敗し続ける
  - 家の計画のドアが今の家のドアでない場合（見つけた拠点、引っ越した家、建てかけの計画）も、そのドアを直しに行かせていた
- 直したこと: 候補に置くブロック（`planBlock`）を持たせ、実行はそれを置く。計画のドアの位置が今の家のドアと同じときだけ直させる。ドアの置き直しに 3 回失敗したら、それ以上は強いない（「left as it is」の行、ほかの行動に進む）
- テスト: npm 181（door 2）

### 操作がまったく行われなくなった: 探索の候補の id に距離を入れていた (2026-09-26)

**Commit**: `f3f49ec`

- ユーザー「操作が全く行われなくなった」→ be7506b で探索の候補の id を `explore east (unexplored land 48m away)` にした。`/act` は候補を作り直して id で探すので、観測と実行の間に見た区域が増えて距離が変わると「今はできない」になり、探索しかない状況（原木がない）では何も動かなかった（と見ている。ログでは未確認）
- id を `explore east` に戻し、距離は `unexplored_land_m` / `go` の欄に。植える・耕す・まくの候補の id からも場所を外した（場所の選び直しで id が変わらないように）
- テスト: npm 179

### 集めた物はまとめて運ぶ、ほかの中目標の物もそばで取れれば一緒に取る (2026-09-26)

**Commit**: `aa832eb`

- ユーザー「集める系は 1 個ずつ拠点に持って帰らず、ある程度たまってから」「今の中目標で一緒に達成できるものがその場にあれば一緒に作業してから帰る。例: 苗木集めと原木集めがあって木を見つけたら両方」
- ブリッジ `stored`: チェストに入れる候補は、32 個持った・足りる数になった・持ち物がほぼいっぱい・家のそば・ここで集める物がない・夕方か夜 のときだけ（それ以外は「carrying n: …」の行）
- `also`: Python が小目標と一緒に、ほかの中目標（と今の手順）で集める物を送る（have / stored、planted → 苗木、farmed → 種）。ブリッジは昼に、8 m 以内で取れる掘る・狩る候補を `also` の印つきで足す（今の目標の残りには数えない）。Python はそれを選んだステップを「進まない」に数えない（`PlaySession.skip_stall_once`）。見直しの決まりにも一文
- テスト: npm 179（chest 2）、pytest 641。目標のシステム指示 6481 字（上限を 7000 に）

### 中目標が進まないときは Gemini が考え直す（原木集めの無限ループ） (2026-09-26)

**Commit**: `a2ffd0b`

- ユーザー「更新しても原木集めで無限ループ。しばらく目標の進捗が変わらなかったら Gemini が考え直すように」「原木が見つからない → 苗木を植えて木を育てる、のようなプラン変更ができるとよい」
- 原因: 小目標の「進まない」（8 ステップ）は小目標ごとに数え直す。小目標が済む・時間帯が変わる切れ目では Jev が同じ手順（have(log)）を選び直すので、中目標は進まないまま回り続けた
- `AdvancePlay._mid_goal_stuck`: 一番上の中目標の進み具合（条件の `(n/m)` の n）が、そのための行動を `minecraft.agent.mid_goal_stall_steps`（60）続けても変わらなければ、次の切れ目で Gemini に回す（`goal_after_failure`、同じ小目標のやり直しは出さない）。理由の文に別のやり方の候補: 見つからない物は育てる（木がなければ planted、食べ物は farmed）、遠くへ explored、中目標を後ろに回すかやめる。見直しの決まりにも「近くで見つからない物は育てる」
- テスト: pytest 640（中目標が進まないと Gemini へ）

### 目標の決定の JSON が max_output_tokens で切れる (2026-09-26)

**Commit**: `dfa97dc`

- ユーザーのログ: 「Gemini が max_output_tokens（思考を含む）に達した」の後に `Gemini returned invalid JSON`（見直し → 中目標の編集の途中で切れた）。ユーザーは「途中である程度のところでコンパクションしたい」→ 原因は入力（文脈）の長さではなく出力の上限（思考を含めて 8192）。見直し（31）、中目標の編集、手順を書くと長くなる
- `gemini.max_output_tokens` の既定を 16384 に（使った分だけ課金される）。`generate_json`: 上限で切れたら（finish_reason MAX_TOKENS）上限を倍（最大 32768）にして 1 回だけやり直す
- テスト: pytest 639

### 植林と畑（設計書 33、軽い形）、探索がまだ見ていない土地へ進む (2026-09-26)

**Commits**: `be7506b`（ブリッジ）, `349028e`（Python）

- ユーザー「植林や農業はできないと断られる。B で Gemini ができるようになったと思っていた」→ 足りなかったのは語彙と 1 手の道具。設計書 33 §9: 述語 `planted` / `farmed`、道具 `plant` / `till` / `sow`、`dig` で草・作物、苗木は葉・種は草から、やり方は技に任せる。「できること」に植林と畑
- ユーザー「探索できる範囲に原木がなく無限ループ」→ 探索の候補は 24 m ずつで、行った区域に印をつけるだけだった（家に帰るたびに同じ所を回る）。`frontierDistance`: その向きでまだ見ていない土地が始まる所まで（最大 320 m）区間ごと（48 m）に進む。見ていない土地が近い向きから出す
- テスト: npm 177（farming 5、memory 1）、pytest 638。目標のシステム指示 6366 字（上限を 6500 に）
- 未確認（実機）: 苗木を植える・耕す・まくが本物のサーバーで通るか（`activateBlock` で耕す・まく）、探索が見ていない土地まで進むか

### 建物の設計が 400 で失敗する、Pillow が依存にない (2026-09-26)

**Commit**: `208d304`

- ユーザーのログ: 建物 stone_house の設計が 3 回とも `Gemini API error 400: Request contains an invalid argument`、その前に「Pillow がないので地図を描けない」
- Pillow が requirements.txt と pyproject.toml に入っていなかった（地図の PilMapRenderer が使う）: `game` に `pillow>=10.0` を足した
- 400 の原因は未確定（API は理由を返さない）。建物の設計は thinking `high` で、high は今まで実機で確かめていない（WORKLOG 「high は未計測」）。`GeminiTextGenerator`: 400 のとき何を送ったか（purpose、thinking、画像、スキーマ、道具、プロンプトの長さ）をログに出し、thinking high なら medium で 1 回やり直す（ログ「thinking high を medium に下げてやり直す」）。それで通るなら high が原因なので、`gemini.thinking_levels` の high（town / site / build_design / skill_write など）を medium にする
- テスト: pytest 638

### 頼んだ本人の「もういいよ」で頼みを取り下げる (2026-09-26)

**Commit**: `889c055`

- ユーザー「コメントでお願いした人がもういいよと言ってもキャンセルされない」→ 返答の扱いは none / accept / decline だけで、中目標をやめるのは目標の決定の plan_changes だけ（しかも「視聴者の頼みは簡単にやめない」）。返答の `request` に `withdraw`: 頼んだ本人が取り下げたら、その人の中目標をやめる（`MidGoalKeeper.withdraw`、本人の頼みだけ。MidGoalDropped が出て、語りが理由を言う）。その中目標のための小目標なら次の切れ目で決め直す
- テスト: pytest 637

### 浮いた幹を木と見なす、もっともな指摘で小目標を決め直す (2026-09-26)

**Commit**: `6218a4e`

- ユーザー「近くに木が生えているのに、地面と接していないからか木と認識されない」→ `world.mjs` の `logReachable` は、原木の下に地面まで原木が続くことを求めていた（下の原木を切った後の浮いた幹、張り出した木は候補にならない）。原木と空気を通り越して最初の地面（葉・水でない）までの高さで決める: 5 段まで（手が届く）、土を持っていれば 8 段まで（足元に積む、pathfinder の 1x1 の塔）
- ユーザー「その指摘のような合理的なコメントでも元の目標に固執してしまう」→ 返答で、やり方の頼みは none（普段からそうしている）になり、小目標は変わらなかった。返答の JSON に `rethink`（確かめられる具体的な指摘に同意したとき、何を考え直すか英語 1 文）。`request_rethink` で次の切れ目に小目標を終わらせ、指摘を理由に Gemini が決め直す（失敗の分析と見直しつき）。チャットからは 1 分に 1 回まで。返答の決まり: 同意するなら必ず rethink を書く
- テスト: npm 171、pytest 636

### 植林と畑の設計（設計書 33、ユーザーの判断待ち） (2026-09-26)

**Commit**: `60c4dc4`

- ユーザー「植林や農業は能力がなくてできない、と断られる。できるようにならないか」→ 今はブリッジに植える・耕す・まく・収穫する行動も述語もなく、断るのが正しい。設計書 33: 述語 `planted(sapling, n)` / `farmed(crop, n)`（中目標の条件になれる）、入手元（葉 → 苗木、草 → 種、育った作物）、場所はブリッジが家から 6〜24 m に選ぶ、行動 plant / till / sow / harvest、植え直し・まき直し。§7 の 5 つをユーザーに聞いている

### 視点のミラーが「72 bytes extra」で切れる、音声モデルの調査メモ（設計書 32） (2026-09-26)

**Commit**: `983fe07`

- ユーザー「プロキシがちょっと微妙かも。ちょいちょい 72 bytes なんたらで落ちる」→ 本物のクライアントの「Packet … was larger than I expected, found N bytes extra」（パケットが期待より長い）。ミラーは受け取ったパケットを生のバイト列で中継するので、ずれうるのは自分で作り直して送るものだけ。一番疑わしいのは 66cd961 で足した持ち物（`window_items`）: Mineflayer のアイテムの部品（コンポーネント: 名前、附呪、NBT など）を minecraft-data の型で書き直しており、型が本物と少しでも違うと余る。`mirror.mjs` の `safeSlot`: 作り直すアイテムは数だけの部品（damage / max_damage / max_stack_size）に絞る（見た目の附呪の光などは消える）。実機のクライアントでは未確認（切れた行のパケット名で確かめる）
- 音声モデル: 少量データから作る方法を調べた（`docs/design/32_tts_options.md`: Irodori-TTS、Qwen3-TTS、GPT-SoVITS、AivisSpeech と今の Style-Bert-VITS2 の比較、感情と GPU の見どころ、次の聞き比べ）
- テスト: npm 170（mirror 1: 絞った window_items が 1.21.4 の形で書けて読み直せる）

### チャット: 自分とボットには反応しない、`!commands` の一覧 (2026-09-26)

**Commit**: `d8e3cc5`

- ユーザー「自分のコメントには反応しないように」「!command で使えるコマンドをチャットに出せるように」「bot アカウント（StreamElements）には反応しないように」
- `ChatComment.login`（IRC のニック、小文字）。`ChatResponder(quiet=, ignore=)`: 配信者自身（チャンネル名）と書き込むアカウントには返事をしない（コマンドは使える）、`twitch.ignore_users`（既定 `[streamelements]`）には何も反応しない
- `!commands` / `!help` / `!コマンド`: コマンドの一覧（表から作る。`!` で始めない）をチャットに書く。30 秒に 1 回。書けなければ声で
- 書き込み: 出力ポート `IChatSink`（`can_post` / `post`）。`TwitchIrcChat(login=, token=)`: `TWITCH_ACCESS_TOKEN`（`chat:edit`）があれば `PASS`/`NICK` でログインして `PRIVMSG`（450 字まで）。ログインに失敗したら匿名に戻る。設定 `twitch.bot_login`（`TWITCH_BOT_LOGIN`、空ならチャンネル名）
- テスト: pytest 634（chat_responder 2、twitch 3）
- 未確認（実機）: トークンでのログインと書き込み

### 目標を決めるたびに今の状態で見直す（設計書 31） (2026-09-26)

**Commit**: `c2cc4a5`

- ユーザー「もっと効率がよい方法を常に模索して。Gemini が目標を選ぶたびに今の状態でできることを見直してプラン修正して」
- 目標の決定のスキーマの最初に必須の `review`（失敗のときは diagnosis → remedy → advice の次）。本文に一番上の中目標の手順の今の判定（`/check`: 済み / まだ / 今は手に入らない）。システム指示に「見直し」の節（済んだ・持っている・チェストの物は集め直さない、同じ外出でまとめる、道具を先に、夜は中でできること。変えないのも答え）。手順は見直しでよい手順が見つかったときも書き直す。`Goal.review` を `activity()` とログに
- ブリッジの変更なし。Gemini を呼ぶ回数は変えない（増やすなら `replan_minutes`）
- テスト: pytest 629。目標のシステム指示 5812 字（上限 6000）
- ユーザーの問い: 「木を植えたら？」に「今はできない」はそのとおり（苗木を植える行動・述語がブリッジにない）

### 視聴者の名前の読みの辞書（設計書 30） (2026-09-26)

**Commit**: `c350cbc`

- ユーザー「リスナーの名前の読み方も覚えておけるようにして、辞書で引けるようにしたい」
- `data/readings.json`（名前 → かなの読み、viewer / guess）。覚え方: 本人の `!yomi よみ`（`!読み` / `!よみ`、覚えたら一言応える）、返答の `name_reading`（読みがなければ Gemini の推測、本人が読み方を言ったらその読み。推測は本人の読みを上書きしない）。使うのは読み上げに渡すテキストだけ（`SpeakTextUseCase(pronounce=)`、長い名前から、英数字の途中は除く）。引く: `GET /api/readings`（`?name=`）
- テスト: pytest 626（readings 14、ボード 1）
- 未確認（実機）: 英字の名前の推測の読み、`!yomi` の後の読み上げ

### 「かまどを使って」に「そうするね」と言うのに使わない (2026-09-26)

**Commit**: `8a75310`

- 原因: 返答の決まりで、やり方の頼み（「〜するときは〜して」）は中目標にせず、できることにあれば none（「普段からそうしている」）。「かまどを使って」「肉は焼いて」はこれに当たり、できることに「かまどで焼く」があるので none になり、何も変わらなかった。加えて生肉も食べ物に数えるので、空腹の小目標ではかまどを使う必要がない。26 で小目標の多くを Jev が選ぶので、Gemini の目標の決定が会話を見る回数も減っている
- 返答の決まり: 結果が物で書ける頼みは、言い方がやり方でも accept にして完了条件にする（焼いた肉 have(cooked_beef, 4)、木炭 have(charcoal, 8)、鉄 have(iron_ingot, 3)、かまどを家に placed(furnace)）。物で書けないやり方の頼みだけ none / decline。solver はもともとかまどで焼ける（燃料、かまど、材料）

### レシピを全部入れて調べられるように（設計書 29） (2026-09-26)

**Commit**: `f4a066d`

- ユーザー「レシピはなるべくたくさん入れて検索可能に。Gemini が元から持っている知識でクラフトできるとよい」
- クラフトの実行はもともとサーバーのレシピ本（RCON が `recipe give @a *`）で全部できる。足りなかったのは計画と調べものの知識（minecraft-data はタグの材料を 1 通りしか載せない）
- `minecraft-bridge/data-static/`: 1.21.4 のバニラのレシピ 1,370 とアイテムのタグ 173（`npm run recipes:fetch`、misode/mcmeta の要約）。`src/recipes.mjs`（`RecipeIndex`: タグの展開（入れ子）、検索）。`Knowledge` がこれで計画する（材料は `#tag` / `any:` のグループ、同じ種類からのレシピは白いものからだけ、かまどの材料も）。道具 `recipe_of`（全部の作り方）、`find_recipes`（名前で探す）。できること: バニラの作業台・かまどのレシピはすべて作れる
- テスト: npm 169（recipes 3: タグ、検索、主な 14 品が不可能にならない）、pytest 611

### 色つきのベッド（「ベッド青くしない？」に「まだできない」と答えた） (2026-09-26)

**Commit**: `4c23532`

- 原因は操作の仕方（A/B）ではなく知識: minecraft-data は染色のレシピを 1 通り（青い羊毛は黒い羊毛＋染料）しか載せず、ブリッジは同じ種類からのレシピを外しているので、色つきの羊毛が作れないことになっていた。花（染料の材料）も入手元になかった。目標も「家にベッド」しか書けなかった。道具モード・技でも `how_to_get` / `recipe_of` が同じ答えを返すので、B でも同じだった
- ブリッジ `knowledge.mjs`: 染めた羊毛＝白い羊毛＋その色の染料（`DYED`。ベッドからベッドは作らない決まりはそのまま）、花を自然のブロックに（ヤグルマギク→青の染料など）。solver は 青いベッド → 青い羊毛 3（羊を狩って白い羊毛、ヤグルマギク）＋板材 と組む
- `placed(blue_bed, home)` など色つきのベッド: その色が家にあれば達成。持っていれば置く（`place_bed` はその色を選ぶ）、部屋が前のベッドでふさがっていれば前のベッドを拾う候補も出す
- Python: 述語の説明、できること（花から染料、羊毛を染めて色つきのベッド）。目標の決定の決まった文の長さの上限を 6000 に（27 と家具で増えた。キャッシュに当たる部分）。npm 166、pytest 611

### 「作業台も部屋に置いたら」が中目標にならなかった: placed を家具全般に (2026-09-26)

**Commit**: `47ab0a1`

- ユーザー「夜の籠城中に作業台を部屋にも置いたらと言ったら、置いとくねと言ったのに行動が変わらない」
- 原因: 頼みは中目標（世界で判定できる条件）になって初めて行動に届くが、置く条件は placed(bed, home) しかなかった。「部屋に作業台」は書けず、返答が「置いとくね」と言っても中目標に入らない（none のまま約束した、have(crafting_table, 1) にして持っているのですぐ達成、など）
- ブリッジ: `placed(item, home)` を bed / crafting_table / furnace / chest に（`furniture.mjs` の `HOME_FURNITURE`、`furnitureInHome`: 家の部屋の床とその上の段）。持っていなければ作る（solver）、持っていれば家の中の空いた所に置く候補（`place_in_home`、チェストは今までの place_chest）。夜に家の中でもできる
- Python: 述語の説明（例 placed(crafting_table)）、家があればいつも placed を出す。npm 165、pytest 610

### 家具を拾って置き直す (2026-09-26)

**Commit**: `b1b86e4`

- ユーザー「アイテムの移動もできるようにならないか。ベッドの置き直しとか」
- ブリッジ `src/furniture.mjs`: `placedBedToTake`（今の家の外に置いてある一番近いベッド）、`moveRefusal`（家の家具は家の中でだけ、ベッドは家にだけ、中身の分かっているチェストは断る）。`goals.mjs` の placed(bed, home): ベッドを持っていなければ、置いてあるベッドを拾う候補（`take the <bed> placed at …`、dig）を作る候補と並べて出す。プリミティブ `move_placed`（壊して拾い、ベッドは place_bed、ほかは place_station で指定の場所か家の中）。道具 `move_furniture`（道具モードと技の API）
- Python: 道具の定義、できること（ABILITIES）、技の API の説明。npm 164、pytest 609

### 襲われたときの反射を Jev が選ぶ（設計書 28） (2026-09-26)

**Commits**: `97b9d56`（ブリッジ）、`46632a6`（Python と文書）

- ユーザー「敵が近くにいて襲われているのに、しばらく立ち止まる。反射に近い処理を Jev でうまくできるとよい」「まわりの情報を毎回 Jev に渡してよいのでは」→ 設計書 28、決定は §5
- 立ち止まる理由: 遠くからの矢に反射しない、止まった行動が失敗に数えられ Gemini の目標の決定（2〜9 秒）の間立っている、見えない・高さの違う相手を脅威にしない、規則が粗い
- ブリッジ: きっかけ（ダメージ、向かってくる敵）、選択肢、`GET /danger`、`POST /reflex`、`interrupted`。Python: `DangerWatcher`、失敗に数えない、設定、候補モードの Jev にも目印。npm 162、pytest 608

### まわりの目印と、見つけた拠点を家にする (2026-09-26)

**Commit**: `a82567c`

- ユーザー「周辺の状況把握をしていないのか。視界にベッドも拠点らしいものも入っているのに認識できていない」→ 観測にあったのは、モブ・落とし物・持ち物・覚えた資源（原木、鉱石、石、動物）と、ボットが自分で建てた家だけ。ボットが建てていないベッドや家（視聴者が建てたもの、村の家、覚えていることを消した後の前の家）は目の前にあっても分からなかった。画面の見直しは失敗の後だけ
- ブリッジ `src/landmarks.mjs`: `landmarks`（ベッド、ドア、作業台、かまど、チェスト、松明を 24 m 以内で種類ごとに数と一番近いもの、家のものか）→ `observation.nearby`。`findBase` / `adoptFoundBase`: 家がないとき、ドアの片側が屋根つきの閉じた部屋（100 マスまで）なら、それを家にする（見つけた拠点）。今の家があれば置き換えない
- Python: 「今していること」に「近くにあるもの」の行（目標の決定、実況、返答、道具の選択が見る）
- テスト: npm 158（landmarks 4）、pytest 602

### 視点のミラーの手に持つ物、死んだら小目標を選び直す (2026-09-26)

**Commits**: `66cd961`（ミラー）、`33458a3`（死んだとき）

- ユーザー「手に持っているアイテムがサーバーとビューアーで一致していない」→ サーバーはクライアント自身が決めたこと（`held_item_slot` の持ち替え、`window_click` の結果）を送り返さない。ミラーは受け取ったパケットだけを中継していたので、視聴者の持ち替えと持ち物が古いままだった。`relayOwnState`: ボットの持ち替えを clientbound の `held_item_slot` にして送り、クリックの 100 ms 後に持ち物（`window_items`、ウィンドウ 0）を送る。後から参加した視聴者にも今の状態から送る。npm 154
- ユーザー「死んだらリスポーンして状況が一変するので、そのタイミングで一度強制的に小目標を選び直してほしい」→ ブリッジが死んだ回数（`observation.deaths`）を数え、`PlaySession` が小目標を決めたときの数より増えていたら小目標を終わらせる（達成より先に見る）。選び直しは必ず Gemini（画面つき、`goal_after_failure`）。失敗の分析（27）はしない。ブリッジの再起動で数が減っても死んだとはみなさない。pytest 601
- 合わせて、ユーザーのコミット `e93c03e`（config）をマージした

### isolated-vm を 6 に（Node 24 の Mac でビルドに失敗した） (2026-09-26)

- ユーザーの Mac（Node 24.11.1）で `npm install` が isolated-vm 5.0.4 のコンパイルで失敗（Node 24 にはビルド済みがなく、C++17 で Node 24 のヘッダ（C++20 の concept）を読めない）
- `isolated-vm@^6.2.0` に上げた（Node 22 と 24 のビルド済み: darwin-arm64、win32-x64、linux。コンパイルするときも C++20）。`engines: node >=22`。API は同じで、ブリッジのテスト 152 件はそのまま通る

### B: 技を実装（設計書 22） (2026-09-26)

**Commits**: `89a775a`（ブリッジ）、`317fef2`（Python）、`f542656`（文書）

- ユーザーの決定（22 §8）: サンドボックスは isolated-vm、上限はそのまま、ワールドのリセット後も残す、失敗作は配信の外の掃除で消す、ゼロから、書く深さは high
- ブリッジ `src/skills.mjs` と `index.mjs` の `/skills`・`/judge`、`npm run skills:clean`。isolated-vm は同期の時間切れと CPU の見張りの両方で止まらないループを止める（どちらが先でも同じ理由にした）。`npm test` 152 件
- Python: `run_skill` / `write_skill`、`SkillWriter`、道具の選択の技の一覧、失敗を直すときに見せる、見張りが judge に答える、イベントと実況・アバター、設定。pytest 598 件
- 設計からの変更: 同期の 50 ms の上限を、isolate の CPU 時間の合計 1 秒にした（isolated-vm では非同期の続きの時間を分けて測れないため）
- 次: 実機（`--control tools`）で、技を覚えるか、直せるか、Gemini の回数と費用が A より減るか

### B（技）の設計: 設計書 22 (2026-09-26)

- ユーザー「A はだいたい良さそうなので B の設計に入りたい」
- 設計書 22（承認待ち）: Gemini が A の道具を呼ぶ JS の技を書き、世界で確かめ（`expects`）、覚え、失敗したら直す。道具の選択に `run_skill` / `write_skill`（書くのは別の呼び出し `skill_write`、thinking high）。実行はブリッジのサンドボックス（案 `isolated-vm`）、API は A の道具と同じ（安全の制約もそのまま）、上限（180 秒、行動 40 回…）、`/abort` と反射で止まる。`t.judge()` は見張りのティックで Jev が答える。新しい技は実況とアバターで見せる
- 決めてほしいこと（22 §8）: サンドボックス、上限、ワールドのリセット後も残すか、消せるか、最初の技、書く深さ

### 進まないときは原因を分析してから目標を変える（設計書 27） (2026-09-26)

**Commit**: `08df41f`

- ユーザー「続いて進まなければ、Gemini に原因分析をしてから、必要であれば目標の再設定をするようにしてほしい」。きっかけは 09-25 のログの食べ物探し（have(food) が stalled → Gemini が数だけ変えて同じ小目標、を約 1 分ごとに繰り返した）
- 呼び出しは増やさない: 失敗の後の目標の決定（goal_after_failure）のスキーマの先頭に `diagnosis`、次に `remedy`（retry / change）と `advice`
- 分析の材料: その小目標の行動の記録（最大 12: 行動、確信度、結果）と最後に出ていた候補（距離、行ったことがある方角、何分前に見たか）。本文の「## うまくいかなかった小目標の記録」。書き方はシステム指示
- コードの決まり（`_check_remedy`）: 同じ種類（`GoalSpec.same_kind`、数は無視）のやり直しは助言が必須、change は別の種類、同じ種類が 2 回続けて失敗したら retry を出さない
- `Goal.advice` は Jev の状態（と道具モードの「今していること」）へ、`Goal.diagnosis` は「今していること」に出て実況が理由を話せる
- stuck / stalled だけ。考え直す（画面、視聴者の頼みを今やる）は分析しない
- テスト 9 件（pytest 581）

### キャッシュが効くプロンプトと入力の削減（設計書 26 §4） (2026-09-26)

**Commit**: `19be5b7`

- ユーザー「コンテキストエンジニアリングにも手を付けたい。キャッシュが効きやすく、構造化して入力自体も削れる所は削る」
- 目標の決定: 決まった文（決まり、述語の説明の全部、手順の書き方、出力）を `build_goal_system()` でシステム指示に（約 4,700 字、毎回同じ）。本文は状態だけ（代表的な状態で約 900 字）。今使える述語は名前だけ 1 行で本文の側
- 実況・返答: `build_system_prompt(character, purpose)` でキャラクター＋用途の決まり（頼みの扱い、完了条件、できること、出力）。本文は 今していること → 会話 → 感情 → その回だけのもの（イベント / コメント）
- 削減: チェストは中身のあるものの上位 6 種、死んだ場所は 2 件、持ち物は JSON をやめた、目標の決定の会話は実況を直近 3 件に
- テスト: システム指示が状態で変わらない、長さの上限（`TestPromptLength`）、実況の絞り込み。pytest 572、npm 138
- 未確認: 実機の `cached=` の割合（`tools/gemini_usage.py` で前後比較）

### 小目標は Jev が選び、Gemini は手順を書く（設計書 26 §2） (2026-09-26)

**Commit**: `13fbf53`

- ユーザー「小目標の決定を Jev に任せられないか。中目標と必要な行動と持ち物を渡して次を選ばせ、完了したら再設定」「材料は Gemini が渡しているのではないんだ（ソルバーだった）」「画面の見直しは詰まったときだけ」「小目標ごとの実況は数分に 1 回なのでそのまま」「設計書を書いてから実装して」
- 設計書 26（前半: 手順と Jev、後半: キャッシュと入力の削減）。見直しで決めたこと: 手順は世界で判定できる条件だけ、ステップの上限・時間帯の変化は Jev（進んではいる）、失敗・考え直しは Gemini、定期の見直しは中目標リストが変わっていなければ 3 倍まで待つ、身を守る小目標は中目標に数えない、小目標の理由は手順を書いたときの 1 文になる
- `PlannedStep` と `MidGoal.plan_steps`（保存）、`MidGoalPlan.set_steps`、`MidGoalKeeper.set_steps`。目標の決定の出力に `steps`（have/stored/built/placed/lit だけ）、Jev を使う設定で一番上に手順がなく書かなければ差し戻す
- `GoalChooser`（application）: 済んでいない手順（`/check`）と身を守る小目標（夜 through_night、夕方 at_home、空腹 have(food, 4)）を候補に Jev の Choice。候補 1 つなら Jev を呼ばない。確信度 0.4 未満・答えなし・手順が尽きた・判定できないは理由を返して Gemini へ。`logs/goals/*.jsonl` に記録
- `AdvancePlay._needs_gemini`（理由をログに出す）: 設定が gemini、失敗（stuck/stalled/reconsidered）、中目標がない、手順がない、定期の見直し
- 設定 `minecraft.agent.small_goals: jev`（gemini で今までどおり）、`jev_goal_min_confidence`、`replan_minutes`、`goal_record_dir`。Jev のクライアントは見張りと共有
- 費用のつまみ: `include_thoughts` を既定 false（`--debug` で true）。定期の画面の見直しは `4e3b2a3` で既定オフ
- テスト 7 件（pytest 566）。未確認: 実機での Gemini の回数（`tools/gemini_usage.py` で前後比較）、Jev の選び方（`logs/goals`）
- 次: 26 §4（キャッシュと入力の削減）

### Gemini の費用を用途ごとに測る (2026-09-26)

- ユーザー「1 サイクルどのくらいで終わり、何回 API を呼ぶか。昨日 2 時間で Gemini だけで 1,500 円くらいかかった。見直したい」
- 使用量のログの行に `purpose=`、`images=`、`cached=`（暗黙のキャッシュに当たった入力）を足した。`tools/gemini_usage.py`: ログから用途ごとに回数・1 時間あたり・平均秒・1 回あたりの入力/思考/出力、価格を渡せば費用の目安（古いログは thinking の深さごと）
- 呼び出しの場所: 小目標の決定（goal / goal_after_failure、小目標ごとに 1 回＋作り直し）、実況（commentary、目標が変わるごと）、道具モードの選択（tool、1 ステップごと）、画面の見直し（screen_review、4 分ごと、画像）、返答（reply、コメントごと）、まれに house / town / site / build_design。決まった文だけで小目標の決定のプロンプトは約 3,000 字
- 次: ユーザーのログで実測してから、減らし方を決める（道具モードか、画面の見直しの間隔、実況を小目標の決定にまとめる、プロンプトの削減、キャッシュ）

### 建築予定地への仮設 (2026-09-26)

- ユーザー「建築予定地でも作業効率のために仮設することはよくある」→ 作業台・かまどは建築予定地にも置く（`inStandingBuilding` 以外）。建てる側は予定のマスの仮設物を壊して置く（`removable`、`siteFits`）。道具の `place` も。13 §14 の追記。npm 138

### 中目標と小目標が同じになる (2026-09-26)

- ユーザー「中目標に『家を建てる』があるのに、小目標にも『家を建てる』が来ることがある。目標の基準を例示しているか」→ していなかった（「数分で終わる大きさ」の 1 行だけ）。しかも `built` の説明が「材料集めとクラフトも含めて進む」で、中目標と同じ built を小目標にすれば全部進むと読めた
- 小目標の決め方に、中目標は「できた状態」、小目標は「次の一歩」、同じ条件は材料がそろって仕上げだけのときだけ、と例 4 つ（家、ベッド、食料の備蓄、倉庫）。`built` の説明にも、小目標では材料を先に have で集めると書いた（プロンプトだけ。コードでは禁止していない: Gemini が判断する）

### 優先順位と置き場所を Gemini が判断する（設計書 13 §14） (2026-09-26)

**Commit**: `ba13bea`

- ユーザー「Gemini が合理的と判断したら優先順位を調整しながら進めてほしい」「家の外（中）にしか置けない縛りは何のため？ 既存の設備が遠ければ、その場でもう 1 つ作れてよい。判断は Gemini に」
- 優先順位: 返答の `when: now` の「待っているときだけ」を外した（作業中でも先頭に入れて小目標を区切る。理由を言う）。plan_changes は「理由があるときだけ」、合理的なら move で入れ替え、視聴者の頼みも一番上にできる（`MidGoalPlan.move`）
- 置き場所: `stationSpots`（家の中の `roomSpot`: ドアの列とベッドの場所を残す／外）を両方候補に。避難中は中だけ。遠い作業台（6m 超）には「ここに作る（板材 4）」「持っているものをここに置く」を `why` つきで。道具の `place` は作業台・かまど・チェストを家の中にも置ける。建てる予定の家の敷地には置かない（壁が建たなくなる）は残した
- テスト: npm 136、pytest 558。反映にはブリッジと配信の再起動

### 待っている間の頼みを今やる（設計書 13 §13） (2026-09-26)

**Commit**: `c5f1423`

- ユーザー「夜に家の中で待っている間に『チェスト作ったら？』と言っても『夜が明けてベッドを作ってから』と聞かない。頑固すぎる」→ 原因は返答の決まり（頼みは今の中目標の後ろ、小目標は中断しない、今すぐやるとは言わない）と、夜は作業台を置けない・使えないブリッジ
- ブリッジ: 避難中は作業台を家の中に置く（`stationSpot(bot, state, item)`、`chestSpot` と同じ場所の決まり）、使える作業台は家の中のものだけ（`findTable(bot, state)`、ソルバーの `table`、道具の craft）。`shelterOf` を `home.mjs` に移した。テスト 2 件（npm 133）
- Python: 返答の `when`（now/next）、`MidGoalProposal.now`、`MidGoalKeeper.accept(waiting=)`（待っていないのに now は `GoalRejectedError` → 返答の作り直し）、`MidGoalPlan.add(now=)` で先頭に、`session.request_rethink` で待ちを区切る。プロンプト（返答の決まり、小目標の決め方、`ABILITIES`）。テスト 2 件（pytest 558）
- 昼の決まりは変えていない。反映にはブリッジと配信の再起動が要る

### ワールドを作り直す手順 (2026-09-25)

- ユーザー「ワールドをリセットしたい。シードがちょっと難しそう」→ `docker-compose.minecraft.yml` に `LEVEL: ${MC_LEVEL:-world}` と `SEED: ${MC_SEED:-}`（`.env.example` にも）。名前を変えると新しいワールドになり、前のワールドはボリュームに残る。手順は `docs/streaming.md`「ワールドを作り直す」（state.json と mission.json を取っておいて別名に、`up -d --force-recreate`、シードの選び方）

### 大きな建築（設計書 25、3 段目） (2026-09-25)

**Commit**: `39043e8`

- 段階に分ける案（助言）はやめ、1 つの建物の上限を上げた: 中目標のリストは 6 件までで大きな建物 1 つで埋まる。予算は視聴者の頼みだけ、停滞の判定は小目標ごとなので大きな建物でも働く（25 §6）
- 配信者自身・街の建物は 3,000 ブロック・1 辺 48・高さ 16、家から 64 マス。視聴者の頼みは 600（`BuildDesign.VIEWER_MAX_BLOCKS`、`max_blocks`）。プロンプトと `ABILITIES` に上限
- 未確認: 3,000 ブロックの建物を実機で最後まで建てられるか（材料集めの量、足場なしで届くか）

### 建物の置き場所を地図の画像で選ぶ（設計書 25、2 段目） (2026-09-25)

**Commit**: `e4f95fb`

- ブリッジ `map.mjs` / `GET /map`: 家を中心に 64x64、列ごとの一番上のブロックの種類（ground、stone、sand、water、lava、leaves、tree、built、snow、other）と家の床からの高さ、家と建物の範囲。読み込まれていない列は null。アンカー `map` + `site` は、その中心から 6 マス以内の平らな場所（家・建物と 1 マス空ける）
- Python: `IMapRenderer` と `PilMapRenderer`（真上、北が上、4x4 ブロックで 1 マス、列 A〜P・行 1〜16、家は赤、建物は橙と名前、高さで明るさ）。`BuildAnchor.MAP` と `BuildDesign.cell/site`、`cell_center`（マス目 → 座標はコードだけ）。`BuildDesigner` は地図を画像で添え、`map` を選んだらマス目から中心を決める。地図がなければ map を差し戻す
- Gemini の画像の解像度を用途ごとに（`gemini.media_resolutions`、既定 `build_design: medium`。ほかは low のまま）
- 見た目: 地形らしい見本を描いて確かめた（名前は白い下地で枠の上、矢印はフォントにないので「N up」）
- テスト: npm 131、pytest 555

### Gemini が設計する建物（設計書 25、1 段目） (2026-09-25)

**Commit**: `d21bcfb`

- ユーザー「家を広げたり、Gemini が思い描いたものを作れるように」「置き場所は周りを見渡して vision で」「大きな建築も」→ 設計書 25（15 C の一般化）。おすすめのまま: 1 つ 600 ブロック・32x16x32 まで、家から 48 以内、壊せるのは設計が空けると書いた家の壁だけ
- 形の組み合わせ（`BuildDesign` / `BuildShape` / `ShapeKind` / `BuildAnchor`、domain）: fill・hollow_box・clear・door、材料は planks/log/cobblestone/dirt。展開は空けるマス（上から）→ ブロック（下の層、外周から）→ ドア
- `BuildDesigner`（application `builds.py`）: スキーマ、`parse_build`、材料の確認（`/check` の impossible）、登録（`PUT /builds/<name>`）、3 回まで理由つきで作り直し。thinking `build_design: high`
- `MidGoalKeeper`: 条件にまだない `built(name)` があれば先に設計（Gemini 自身の追加と視聴者の頼み）。視聴者の頼みの予算は `max(80, 2×ブロック数 + 40)`（`MidGoal.budget`、保存する）
- `GoalSpec.name`、観測の `unfinished_builds`、目標の述語（未完成の建物があれば built を出す）、`ABILITIES`（「2 軒目の建物はできない」を消し、設計して建てられると書いた。チャットの「今はその技術がない」の原因）、ゴールボードのラベル、道具 `build_next(name)`
- ブリッジ: `builds.mjs`（アンカーから原点、守るもの（ドア・ベッド・チェスト・室内・床・ほかの建物・家からの距離）、`buildAllowsDig`、`inBuilds`、`growHome`）、`BuildPlan` の種類（cobblestone、dirt、air）と建物のドアの向き、予定地の自然のブロックは掘って置く、`built(name)` の判定と候補、`place_plan`、状態の保存。`protectedReason` は建物が空けると書いた壁だけ許す。家の室内は `home.cells`（塗りつぶし）
- テスト: npm 129 件（builds 6、check 1 を追加）、pytest 549 件
- 未確認: 実機（Gemini がこの書き方で良い形を出すか、壁の入口を空けて部屋がつながるか、高い壁が足場なしで建つか）。次: 地図の画像（2 段目）、大きな建築の段階分け（3 段目）
- 見直し（次のコミット）: 空けるマスをブロックの後に（家と接する面は最後。先に入口を空けると部屋ができるまで家に穴が開いたままになる）。家の壁の中のドア・木や水のある側は登録で断る（置けずに詰まる）。まだない `built(name)` は `/check` では未達（`NotYetError`）、目標にはできない。街の段階の条件の建物は、中目標にする前に設計する（失敗した名前は 2 回まで）。チェストの置き場所も広げた室内の中だけ。設計役がないときは知らない建物を断る
- 視聴者の建物の予算は `max(80, 2×ブロック数 + 40)`: 600 ブロックなら 1,240 ステップ（数時間）。調整するかはユーザーの判断

### ベッドの置き場所が 1 か所だけだった (2026-09-25)

- ユーザー「家にベッドを置く → 狭くて置けない → 置く…で詰まる。コメントで『家を広くしたら』→『今はその技術がない』」→ `bedSpot` はドアからまっすぐ奥の 1 か所（inside の先の 2 マス）しか見ておらず、最小の家（室内 3x3）ではその列にたいまつなどが 1 つあるだけで `no free spot for the bed in the house` になっていた
- `home.mjs` の `bedSpot`: 室内の空いた 2 マス（足と頭）と、足の手前の立つマス（stand）の組をすべて探す。ドアの内側のセルはふさがない。まっすぐ奥を先に、次にドアから遠い順。`place_bed` は stand に立って足のマスを向いて置く（頭は向いた方に伸びる）。テスト 3 件（`npm test` 122 件）
- 家を広げる・Gemini が思い描いたものを作ることは別に検討（目標の語彙に「建てる」が設計図の家しかなく、今の家は安全の決まりで掘れない）

### 置いてあるベッドを「ない」と判定していた (2026-09-25)

- ユーザー「家に既にベッドを置いているのに、家にベッドを置く小目標を立てる」→ ブリッジの `hasBed` は `home.bed`（`place_bed` の操作で置いたときだけ記録する位置）しか見ていなかった。手で置いたベッド、道具モードの `place` で置いたベッド、記録の前から置いてあったベッドは「ない」になり、`placed(bed, home)` が満たされず、中目標「夜に寝られるようにする」も終わらなかった
- `home.mjs` の `hasBed`: 記録した位置になければ家の室内（床の高さ）を探し、見つけたら `home.bed` に記録（`state.json` に保存される）。記録した位置が読み込まれていない（ボットが遠い）ときは記録を信じる。ブリッジのテスト 2 件（`npm test` 119 件）
- 引っ越した後、ベッドが前の家にしかないなら「ない」のまま（新しい家に置く目標は正しい）
- 反映にはブリッジの再起動が要る

### 本番の配信の起動（python -m ailoveshen.stream）、Twitch のチャット、止まらないプレイ (2026-09-25)

**Commit**: `3c3780f`

- ユーザーの問い「残りは何か、本格配信用の起動スクリプトはどれか」→ なかった（examples だけ）。1) 起動口 2) 例外で止まらない 3) Twitch のチャット（読むだけ）をまとめて作ると提案 →「作ってください」
- `GameService.play(max_steps=None, keep_going=True)`: 開始・1 ステップの例外をログに残し、5 秒から倍々に最長 60 秒待ってやり直す（成功で戻す）。最初の 1 回はトレースバックも残す
- `TwitchIrcChat`（`infrastructure/adapters/twitch/`、ポート `IChatSource`、値オブジェクト `ChatComment`）: `irc.chat.twitch.tv:6697` に TLS、`justinfan<数字>` で匿名（トークン不要、読むだけ）。タグで表示名・user-id。PING/PONG、RECONNECT、切断・失敗は 1 秒から倍々に最長 60 秒でつなぎ直す。接続とログインに 15 秒の上限（この環境で Twitch への TCP が応答しなかったことから見つけた）。6 分何も来なければつなぎ直す
- `ChatResponder`（presentation）: 1 つずつ返事（`generate_response` にセッション）、間は 5 秒以上、待つのは新しい 3 件、`!` は無視。返事は TTS の HIGH
- `Stream`（presentation）と `create_stream`（factories）: ゲーム、LLM、Narrator、TTS（つながらなければ `StreamSetupError` で始めない）、目標ボードとアバター、チャット。止めるときは全部閉じる（1 つ失敗しても残りを閉じる）。入口 `src/ailoveshen/stream.py`（キーと config の確認、ログの設定は INFO、`--debug`）
- 設定: `stream.board_port`（0 で出さない）、`stream.speak`、`twitch.enabled`、`twitch.response.backlog`。`.env.example` の TWITCH_CHANNEL
- 止めるとき、開いたままの SSE（OBS）を uvicorn が打ち切ってトレースバックが出ていた → キャンセルされたら SSE に終わりの印を入れ、uvicorn 自身に止めさせる（`GoalBoard.serve`、`AvatarStage.end_streams`）
- 確かめたこと: ブリッジなしで起動 → 5 秒、10 秒待ってやり直す。目標とアバターの SSE をつないだまま SIGINT → 0.3 秒で止まり、トレースバックなし、後片付けが走った。IRC は手元の偽のサーバーで（タグ、PING、RECONNECT、つなぎ直し、応答しない接続）
- 未確認: 本物の Twitch（この環境から TCP が通らない）、本物の API での長時間の配信
- 見直し（次のコミット）: 裏のタスクが黙って止まる所を塞いだ。目標ボードのポートが使われていると uvicorn が SystemExit を投げて配信の全体が落ちていた（再現: 終了コード 3）→ 起動前にポートを確かめて `StreamSetupError`、`GoalBoard.serve` は SystemExit を OSError に。目標ボード・チャットのタスクが止まったら ERROR でログ（プレイは続く）。`ChatResponder` は 1 つの返事の失敗で止まらない。`TwitchIrcChat` はどの例外でもつなぎ直す。手順書のログの場所（`logging.file`、development なら `-dev.log`、INFO まで）
- 残る小さなこと: 開始のやり直しは最初から（`StartPlay` は observe が先なので、ブリッジが落ちていれば設計し直さない。家の設計の直後にブリッジが落ちたときだけ、設計し直しと実況の重複がありうる）

### 配信の手順書（docs/streaming.md） (2026-09-25)

- ユーザーの依頼「この辺全部、配信手順・コマンドとしてドキュメントにしてほしい」→ `docs/streaming.md`: 全体像とポート、初回の準備（依存、.env、音声モデル、仮想オーディオ、OBS の WebSocket とソース）、毎回の起動の順番（Docker → ブリッジ → 視点のクライアント → プレイの処理 → OBS）、オプション、止め方、引き継がれるもの、見るところ、うまくいかないとき。CLAUDE.md から参照し、コマンドやポートを変えたら直す決まりにした

### Minecraft の例で読み上げる（--speak） (2026-09-25)

- ユーザーの問い「Style-Bert で作った音声モデルで TTS するには？」→ TTS の仕組み（Docker の Style-Bert-VITS2 サーバー、`create_tts_service`）はあったが、`integration_test_minecraft.py` は `[say]` を表示するだけだった
- `--speak`: 実況（Narrator の say）を NORMAL、視聴者への返事を HIGH で `TTSService` に。感情は `LLMService.get_current_emotion`。TTS の `SpeechStartedEvent` でアバターの口も音声に合う（`lip_sync: auto`）
- `config.load_config_dict`（default.yaml + {env}.yaml + ${VAR}。辞書で受け取る TTS のファクトリー用。`load_settings` もこれを使う）。`tts.voice.model_name` を `${TTS_MODEL_NAME:-shen}` にし、`.env.example` に足した
- 未確認: 実際の読み上げ（この環境に sounddevice・TTS サーバー・モデルがない）

### Ctrl-C で止まらない (2026-09-25)

- ユーザー「サーバー止めたくなっても Ctrl-C で止まらない」→ `--board-port` のとき、uvicorn の `Server.serve` が SIGINT を横取りし、開いている接続（OBS のブラウザソースの SSE は閉じない）が閉じるまで待っていた。プレイにも Ctrl-C が届かなかった
- `goal_board._EmbeddedServer`: `capture_signals` を何もしないものにして、シグナルはプレイの処理（`asyncio.run`）に任せる。`timeout_graceful_shutdown=2`。例は Ctrl-C で「止めた」と出して 130 で終わる
- 確かめたこと: SSE を 1 本つないだまま SIGINT を送る再現で、直す前は 8 秒たっても止まらず、直した後は 0.1 秒で止まり `finally`（後片付け）も走った。テストはシグナルの処理を変えないこと

### OBS: obsws-python がないときのログ (2026-09-25)

- ユーザーの環境で「OBS から画面を撮れない（ModuleNotFoundError）。60 秒は画面なしで続ける」→ requirements.txt を足す前の環境で obsws-python が入っていなかった。ログから分からなかったので、ImportError なら入れ方を 1 回出して以降は撮らないようにした（テストあり）。23 §6 に手順 0

### アバターの表情・しぐさを Jev が選ぶ（設計書 24 §8） (2026-09-25)

**Commit**: `32bbc7e`

- ユーザーの問い「アバターの操作は状況や発言テキストから Jev が判断して操作するようにできる？」→ 設計を出して「作ってください」
- `AvatarDirector`（`application/use_cases/avatar_director.py`）: 1 回の `system_one` で表情（Choice）・強さ（Score）・しぐさ（Choice）。状態は出来事・発言・小目標・intent・状況。失敗・1.5 秒超え・確信度 0.35 未満・知らない答えは規則（今までの `cues_for`）に戻る
- `AvatarStage(director=...)`: 口はすぐ動かし、表情としぐさは答えが来たら `emote`（`source`）。`moment_of(event)` でゲームの出来事を英語の 1 行に。`close()`
- `avatar.html`: しぐさ tilt（首をかしげる）と wave（右手を振る）を足した
- 設定: `avatar.judge: jev|rules`、`judge_timeout_seconds`、`record_dir`（`logs/avatar/`、Jev と規則の答えを並べた記録）。`factories/avatar.py` の `create_avatar_stage`（キーがなければ規則だけ）。結合テストの例をこれに切り替えた
- 未確認（実機）: Jev の速さ、日本語の発言の読み方。記録を見て質問と閾値を直す

### アバター（VRM、three-vrm、設計書 24） (2026-09-25)

- ユーザーが VRoid で作った `models/vrm/ailoveshen.vrm`（VRM 1.0、表情 happy/angry/sad/relaxed/surprised、口 aa/ih/ou/ee/oh、blink、スプリングボーン）を `b1c2151` で追加 →「three-vrm で良い感じに動かしたい」
- `presentation/web/avatar.html`（`/avatar`、OBS のブラウザソース、背景は透明）: 立ち姿（腕を下ろす、息、揺れ）、まばたき、目線（ほとんどカメラ）、本文からかなを母音にして口を動かす、表情、しぐさ（nod / cheer / flinch）。`?demo=1`、`view`、`pos`、`scale`、`model`
- three.js 0.186.1 と three-vrm 3.5.5 を `presentation/web/vendor/` に同梱（この環境から jsdelivr は拒否された。OBS がネットなしでも動く）
- `AvatarStage`（`presentation/web/avatar.py`）: ドメインイベント → 合図（`/api/avatar/stream`）。TTS の `SpeechStartedEvent` に `duration_ms` を足した。TTS がなければ生成した本文で口を動かす（`avatar.lip_sync: auto`）。中目標クリアで cheer、被弾で flinch、行き詰まりでかなしい、本文の手がかりで表情
- 確かめたこと: ヘッドレスの Chromium（ソフトウェアの WebGL）で読み込み・話す口・4 つの表情・cheer・全身を撮った。途中で見つけた不具合 2 つ（腰の高さを毎フレーム縮めてモデルが沈んだ、しぐさの回転がたまった）を直した
- 実機（OBS、TTS との口の合い方）は未確認。次: 口を音声の大きさに合わせる、Gemini に感情を出させる

### 配信用の目標のオーバーレイ（/overlay/vtuber） (2026-09-25)

- ユーザーの依頼「大目標・中目標・小目標を配信画面に Vtuber っぽく表示する HTML が欲しい（OBS 用）」→ OBS のブラウザソースに HTML が一番よい（テキストソースを WebSocket で書き換える方法は飾りとアニメーションが弱い）
- `presentation/web/overlay_vtuber.html`（目標ボードの `/overlay/vtuber`）: 1920x1080、背景は透明。大目標と街の段階、中目標（今のものに進み具合のバー、次の 2 つ、最後に終わったもの、視聴者の頼みの印）、小目標の吹き出し（日本語の `label`、今やろうとしていること、理由、身を守るための印）。目標が変わると吹き出しが弾んで NEW、中目標が終わると「中目標クリア！」、頼みを受けると知らせ。`?demo=1`（サーバーなしの見本）、`pos`、`theme`（pink / mint / sky / lemon）、`scale`、`compact`、`toast=0`
- `/api/goals` に `goal.label`（述語を視聴者向けの日本語に。アイテム名は小さな表、ないものは英語の ID）と `intent`
- ヘッドレスの Chromium で見本を撮って見た目を確かめた（左上・ピンク、右上・ミントとクリアのお知らせ）

### 配信の画面を Gemini に見せる（OBS、設計書 23） (2026-09-25)

**Commits**: `45d9bc1`（本体）、次のコミット（obsws-python のログを止める: OBS がないと 60 秒ごとにトレースバックが出ていた。達成と考え直しが重なったら達成を先に）

- ユーザーの問い「行き詰まったときや定期的に、画面のスクショを Gemini がチェックして軌道修正するようになっているか」→ なっていなかった。決定「OBS で良い。OBS 前提で進めて」
- `ObsScreenCapture`（`infrastructure/adapters/obs/`）: `GetSourceScreenshot` で `obs.game_source`（既定 `Minecraft`、`.env` の `OBS_GAME_SOURCE`）だけを JPEG・幅 768 で撮る。シーン全体は撮らない（目標ボードが映る）。3 秒の上限、失敗したら 60 秒撮らない、ソースがなければ撮るのをやめる、例外は出さずに None（画像なしで続く）。`obsws-python` のロガーを WARNING にしてパスワードを出さない（テストあり）。`image/jpg` → `image/jpeg`
- `ScreenReviewer`（`application/use_cases/vision.py`、ステップのループからだけ呼ぶ）: 定期の見直し（既定 240 秒、`screen_review` は medium）で画面と activity を見比べ、`rethink` なら `PlaySession.request_rethink` で次の切れ目に小目標を終わらせる（理由 `... is reconsidered: on screen: ...` → `goal_after_failure`）。失敗の後の小目標の決定と道具の選択に画像を添える（最短 60 秒）。道具モードの `look_screen`（Python が扱う調べもの。画像は同じステップの次の選択にだけ）
- 画像から分かったことは `ScreenNote` として 1 件だけ、`stream_context` に「画面で見たこと（N 分前、画像の解釈で確かめていない）」として出す。完了の判定に使わない、Jev に渡さない
- `ITextGenerator.generate_json` / `choose_tool` に `images`。Gemini はテキストの後に `Part.from_bytes`、`media_resolution`（既定 low）。模擬サーバーでリクエスト本文に `inlineData` と `mediaResolution: MEDIA_RESOLUTION_LOW` を確認（`inlineData` の中は `mime_type` と snake_case。実 API が受け付けるかは未確認）
- デバッグ: 記録には画像の id・形式・大きさだけ、画像は直近 10 枚を `/api/debug/screen/<id>`、`/debug/gemini` に表示
- 依存: `obsws-python`（extra `stream`、`requirements.txt`）。設定: `obs.game_source` ほか、`minecraft.vision`、`gemini.media_resolution`
- 未確認（実機）: OBS への接続、実際の画像（縦横比）、3.8 Flash が Minecraft の画面をどれだけ読めるか、1 枚あたりのトークンと時間

### .env で環境変数、requirements.txt (2026-09-25)

**Commits**: `28bc0f5`（requirements.txt）、`5e0e204`（.env）

- `requirements.txt`（pyproject の依存と全 extras と `-e .`。新しい venv で入れてテストが通ることを確認）
- `.env.example`（コミット。`.gitignore` に `!.env.example`）。リポジトリ直下の `.env` を Python（`load_settings` の `load_env_file`。`config/*.yaml` の `${VAR}` に入る）とブリッジ（`minecraft-bridge/src/env.mjs`、`process.loadEnvFile`。index.mjs が最初に import する）が読む。シェルで export した値が優先。Docker Compose は `--env-file .env`
- `minecraft.bridge.host/port` を `${BRIDGE_HOST}` / `${BRIDGE_PORT}` に（ブリッジと同じ変数）

### デバッグ: Gemini の思考を見るエンドポイント (2026-09-25)

**Commit**: `3d87066`

- ユーザーの依頼「今の Gemini の思考内容が見えるエンドポイントが欲しい。JSON で返すだけでよい」「OBS で JSON を表示できるか」→ OBS は JSON を直接は読めないので、読んで表示するページをブラウザソースで開く形にした
- `GeminiTextGenerator` が呼び出しごとに記録する（用途、thinking_level、所要時間、思考の要約、出力、道具の呼び出し、finish_reason、トークン、プロンプト、エラー）。思考の要約は `gemini.include_thoughts`（既定 true）で `ThinkingConfig(include_thoughts=True)` を送って受け取る
- ポート `IGenerationLog`、`InMemoryGenerationLog`（直近 `gemini.debug_log_size` 件、ゲームと会話の生成器で共有）
- 目標ボードに `GET /api/debug/gemini?limit=N`（JSON、新しい順）と `/debug/gemini`（2 秒ごとに読んで表示。`?limit=5`、`?prompt=1`）。`examples/integration_test_minecraft.py --board-port 8765` で出る
- 未確認: 3.8 Flash が思考の要約を返すか（実 API）

### A の最初の版: 道具・共通の状態・Gemini の質問に Jev が答える見張り（設計書 21） (2026-09-25)

**Commits**: `16ad1fb`（設計書 21）、`d07fdd0`（ブリッジ: 実行を run() 1 つに、進み具合）、`d1d1721`（ブリッジ: /tool /state /abort）、`c90f24c`（Python: 道具のステップ、見張り、用途ごとの thinking_level）、`3e6c1e0`（文書）、次のコミット（格子を足元から測る、goto の時間の上限 45 秒）

- ユーザーの指示「その方針で一度設計・実装してみて」（Jev は Gemini が書いた質問に高頻度で答える役。19 §13 の 9・11・12 を承認）
- ブリッジ
  - `runner.mjs`: `/act` と `/tool` の共通の実行（反射・busy・時間の上限・被弾での中断・家を出る・履歴・保存）。`abortCurrent` は終わった行動には何もしない
  - `progress.mjs`: 実行中の進み具合（移動量、目標までの距離、経路が見つからない回数、`forcedMove` の回数、掘り、持ち物・体力の変化）。行動の種類ごとの判定は書かない
  - `state.mjs`: 共通の状態と `lookAround`（足元の高さから一番近い立てる高さの格子。空の見える縦穴なら隣 8 つが +3、天井のあるトンネルは横が #、家の中は床が 0 で壁が +4。最初は上から見下ろしていて、天井・屋根・葉を地面と読む誤りがあった → 足元から探すように直した）
  - `tools.mjs`: 行動の道具 17（`do_suggestion` でソルバーの候補も呼べる）と調べもの 3。避難中は外に出る道具を理由つきで断る（昼のドアの前の敵とは戦える）。今の家・建てている家は掘らない・置かない、前の家は掘れる（town4d のベッド）。引数は世界と知識から導いて確かめ、合わなければ理由を返す（プレイを止めない）
- Python
  - `tool_catalog.py`（道具の JSON Schema、行動の道具は `intent` 必須・`watch` 最大 3）、`watcher.py`（1 秒ごとに全質問を 1 回の Jev 呼び出しに。0.7 以上の「はい」が 2 回で stop / wake / maybe_done。コードの「進んでいるか」は記録だけ）
  - `AdvancePlayUseCase(control="tools")`: 小目標は今までどおり述語で決め、その中で Gemini が 1 ステップに 1 つ道具を呼ぶ（調べものは 3 回まで同じステップで）。停滞・予算・時間帯の判定はそのまま
  - `PlaySession.intent` → `activity()` → 実況・返答・プロンプトに「今やろうとしていること」
  - `GeminiTextGenerator`: `choose_tool`（function calling、mode ANY）、すべての呼び出しに `purpose`、用途ごとの thinking_level（town/site high、house・失敗の後 medium、ほか low）
  - `JevFastJudge`（Noul の「はい」の確率 → はい/いいえと確信度）、`JsonlWatchRecorder`（`logs/watch/*.jsonl`）
  - 設定: `gemini.thinking_levels`、`minecraft.agent.control`、`minecraft.watch`。`examples/integration_test_minecraft.py --control tools`
- 確かめたこと: 単体テスト。模擬サーバーで Gemini へのリクエスト本文（`mode: ANY`、用途の thinking、関数宣言 20）。SDK は宣言を `parameters_json_schema` と snake_case で送る（実 API が受け付けるかは未確認）
- この版でやらないこと（21 §1）: 一般の完了条件 `blocks` / `build`、B、大目標の見直し（20）、道具の好みの引数の大半、コメントの仕分け、夜の決まりの変更

### Jev を「Gemini が書いた質問に高頻度で答える役」に (2026-09-25)

- ユーザーの問い「高頻度チェックは Jev のほうが安いので積極的に使いたい。その場合また汎用性がない方式にせざるを得ないか」→ いいえ。前の構成が汎用でなかったのは、選択肢・判定をコードが決めていたから。Jev の質問（`instructions` / `criteria`）は自由な JSON なので、Gemini が実行中に書ける（道具に添える見張り、小さな選択の委任、B の `judge(...)`）
- コードが持つもの: Jev は完了を判定しない、Jev の答えで起きるのは止める・起こす・知らせるだけ、質問が見る状態は 1 つの書式（`/progress`・`look_around`・欲求など、要約）、質問を作れるのは Gemini のループだけ
- 比べ方: Gemini が書く質問は規則ではなく「Gemini 自身が同じことを確かめる」と比べる。費用は未計測（ユーザーの見立てを前提に実測する）
- 19 §11 を書き直し、§13 に 12 を足した（承認待ち）

### 設計書 19 の点検と Jev の整理 (2026-09-25)

- ユーザーの指示「Gemini の意思どおりの操作が可能な構成か見直したい。Jev をどう使うか整理したい」
- 19 §10: 洞窟の穴（town4c）、前の家のベッド（town4d）、何もない所から鉄の剣を A でたどった。A のままでは足りないもの: 地形の形（壁・段差・積める所）が観測にない、完了を確かめる言葉が閉じた述語と `HouseBlueprint` だけ（20 の自由な大目標もこれに縛られる）、Gemini のループの意図が `activity()` になく返答と食い違う。道具に隠れた決まり（前の家を掘らない、足場は土だけ、など）を安全と好みに分けた
- 19 §11: A では Jev の今の主な仕事（候補から 1 つ選ぶ）はなくなる。残る役は見張り（承認済み）と、未計測の「Gemini を起こすか」「技の中の判定」「技の選択」。対象の選択と欲求との調停は規則も全問正解なので予定しない
- 19 §13 の 1〜8 と 20 §8 は承認（後から調整する前提）

### Gemini が自分で操作する: 設計書 19 (2026-09-25)

**Commit**: `fcdd6b7`。設計: `docs/design/19_gemini_tools.md`（承認待ち）

- ユーザーの問い「ソルバーへの依存を減らしたい。問題のたびに実装を直すのは持続的でない。Gemini が好きに操作できる環境を作れないか」
- 最近のブリッジの修正約 20 件を分けた: 半分は操作の仕組み（どの構成でもコードで直す）、半分はコードに書いた判断（Gemini に移せる）
- Jev（typesafe-sdk 0.7.1）は `Choice` / `Noul` / `Score` だけで手順は作れない → 中心は Gemini、Jev は速さの効く判定へ
- ユーザーの決定: A（Gemini が道具を呼ぶ）から始めて B（Gemini が JS の技を書いて保存する）まで必ずやる。A の道具は B の API と同じにする。Jev は見張りから
- 見張り: 実行中の行動を 1 秒ごとに `Noul` で判定し、`/abort` で止めて理由を結果に入れる。反射の中断の経路を使う。単純な規則に勝ったときだけループに入れる（試験の場面は town4〜4d と frun の実際の行き詰まり）
- 考える深さ: ユーザーの問い「thinking_level を内容で変えたい、大目標はしっかり考えたい」→ 呼び出しごとに変えられる。application は用途の名前を渡し、設定で用途 → 深さ。大目標そのものは設定の固定の文章なので、`high` は街の定義と候補地の選択に当てる案。high は未計測
- ユーザーの方針「大目標も最初は自分で与えるが、その後は配信の流れで Gemini に決めさせたい」→ 設計書 20（13 の「大目標は変わらない」を変える）。見直しは切れ目だけ（中目標がなくなった、ゲーム内 1 日ごとの朝、断念が続いた）、`thinking_level: high`、コメントは直接は変えられない、変えた後は 2 日あける。A・B の詳細は設計書 21・22 に番号を送った
- ブランチ: `claude/peaceful-edison-prr51v` を `feat/notes` へ早送りし、`feat/town-m1-m2`（town4d の記録と修正 2 件）をマージした（衝突なし）

### town4d: 16 が引っ越しまで通った、前の家から出られない、戦いの掘り (2026-09-25)

**Commits**: `31490a4`（前の家の中で生き返ったら、その家のドアから出る）、`dee7158`（戦うときに追う目標を置き直さない）。18 は `feat/notes` の `e99fe77`

- town4d（ca11266 の後）で通ったこと: 穴から出る → ドアの置き直し → ベッド → 朝まで寝る → 候補地 9 か所の調査（約 2 分半）→ Gemini が E（82,9）「サンライズウッド」を選ぶ（平らな区画 13、石 22、原木 483 を理由に。SE は石 287 だが平らな区画 8）→ 街の定義（4 段階）→ 新しい家の完成（95/95）と家の切り替え（`引っ越した: ドア (78, 71, 12)`）
- 止まった所: 引っ越しの中目標の `placed(bed, home)`。前の家にベッドを置いてきたので、同じ色の羊毛 3 つを探して北へ約 370m 探索し、飢え（満腹度 6、腐った肉）のまま夕方にゾンビに倒された
- 倒される前、反射の戦いが 12 ティックごとに GoalFollow を置き直し、経路探しの掘りが止まって同じ雪を 4 回掘りかけた → 目標は一度だけ置く（dee7158、古いコードで失敗するテスト）。死の決め手だったとまでは言えない
- 生き返ったのは前の家の中（そこのベッドで寝ていた）。家から出る処理は今の家しか見ず、閉じたドアから出られずに、どの行動も経路なしで失敗し、目標が数秒ごとに変わった → 中にいる家（今の家か前の家）から出る（31490a4）
- 同じ種類の穴（未修正）: `useChest` は今の家にしか入らないので、前の家のチェストには近づけない（16 §6 の「前の家のチェストから運ぶ」が実際には動かない）
- 介入: 前の家に閉じ込められたので、プレイの処理を止めた（ブリッジは残した）
- ビューアーが接続できなかった件: ミラーは正常（テスト用クライアントで確認）、クライアントが参加直後に自分で切っていた。その後つながるようになった（原因は不明のまま）。ミラーが切断時に理由をエラーのまま送ろうとしてシリアライズに失敗するのは見苦しいだけの不具合（未修正）

### town4c: 遠くからの掘り、洞窟の穴に閉じ込め (2026-09-25)

**Commits**: `fe37581`（置き場のブロックは届く所まで歩いてから壊す）、`ca11266`（歩きながら自然の地形を掘り、土を積んで登る）

- ドアの置き直しが 30m 先から古いドアを掘った。サーバーは断り（RCON で確認: 西向きのドアが上下とも残っていた）、手元の世界だけ下半分が空気になった。`placeDoor` と `placeOne` を「歩く → 壊す → 置く」に。テストの偽のボットは届かない所を掘れないようにした（古い順番で失敗する）
- ボットは夜に豚を追って 4 段の段差を洞窟（y=57）へ下り、出られなくなった。移動させない経路探し（`getPathTo`）で比べた: 今の設定と敵のコストなしはどちらも 40 マスで尽きる（地形の閉じ込め。自前のコストのせいではない）、足場か掘りを許すと 8 手で地表
- ユーザーの決定: 「いつも掘れる・積める」（閉じ込めの時だけの脱出、片道の段差を避ける、の案もあった）。自然の地形と葉だけ掘る、家・前の家・建てている家は壊さずその 1 マス外まで足場を置かない、足場は土だけ（丸石は石の道具とかまどの材料）
- 介入: 閉じ込めでプレイの処理が同じ失敗を数秒ごとに繰り返したので、プレイの処理だけ止め（ブリッジは反射のため残した）、直した後にブリッジを再起動して town4d として再開（夜、ボットは穴の中）。16 の検証は town4d 以降の介入なしの区間で数える

### town4: 動けなくなる不具合 2 つ、掘り下げ（17）、ドアの向き (2026-09-25)

**Commits**: `58304b3`（中断の後に新しい移動を始めない）、`77111c2`（prismarine-physics の差分: 当たり判定の許容誤差）、`8824e91`（17 掘り下げ）、`51cf65f`（ドアの向き）

- town4 の 1 ステップ目で止まった原因（インスペクターで動いているブリッジの中を見て特定）:
  - ボットが幹の面にぴったり接した位置（x = -2.3、箱の端 -1.9999999999999998）で、prismarine-physics（最新 1.11.1）は 2e-16 の重なりを「もう重なっている」とみなして衝突を計算せず、幹の中へ歩こうとした。サーバーは拒んで 1 秒に約 19 回位置を戻した。Minecraft の VoxelShape.collide と同じ許容誤差 1e-7 を入れる差分を patch-package で当てる（ユーザーの選択）。postinstall で当たる。手元で再現し、差分なしで失敗・ありで通るテストを置いた
  - 時間切れの中断の後に「落とし物を拾いに行く」移動が始まり、上のせいで終わらなかった → 経路移動は `move.mjs` の `walkTo` からだけ始め、始める前に中断を確かめる。修正前のコードで失敗する回帰テストを置いた（比較は scratchpad のコピー）
- 17（ユーザーの決定: 階段掘り、深さは Gemini が決める、順番に実装）: 小目標に任意の `dig_depth`。埋まっていて深さが足りなければ「何段下に埋まっているか、dig_depth がいくつ要るか」を進められない理由に書く。階段は前に 1・下に 1、真下は掘らない、1 回 6 段、溶岩・水・砂利・家・空洞で止まるか別の向き。空気に面した石も記憶する（掘った階段の底へ戻れる）
- town4b: 最初の家は建ったが、一度も入れなかった。ドアのマスの東に立って置いたため南の壁のドアが西向きになり、開けると板が通り道をふさいだ（物理の差分による退行ではない。前からある、立つ位置しだいの不具合）→ ドアは壁の外に立って床を見て置く、判定で向きも確かめる、家に入る目標は向きの違うドアを先に置き直す
- 介入: town4→4b でブリッジを再起動（止まったブリッジは busy のままで、その間に家のない夜にボットが死んだ）。4b→4c でブリッジとプレイの処理を再起動（夜、ボットは外。家に入れない原因がドアだったので、朝を待たずに入れた）。調査中にインスペクター用のシグナルを誤ってブリッジを起動したシェルに送り、シェルが終わった（node 本体は動き続け、実行には影響なし）
- ユーザーの決定: 18 は作る（種類 3 つ、12 件、3 日）。順番は 16 の実機と 17 の後。視聴者の頼みは「予算は設定しないつもりだった」→ 数字の予算を外し、進まない頼みは理由を言ってやめてよいとプロンプトに書く案を出した（返事待ち）

### 街の場所: 候補地の調査・選択・引っ越し（16）、ノートの設計（18） (2026-09-25)

**Commits**: `9879eb5`（16 の決定事項と 4.5 の欄）、`10d8cf0`（ブリッジ: 調査と引っ越し）、`bc70ce5`（Python 側と、ブリッジの調整）、`24bc050`（設計書 `18_notes.md`、承認待ち）

- ブリッジ: `survey.mjs`（家を中心に、その場所と 8 方向 96m の 9 か所。平らな 9x9 区画、水・急な段差の割合、地表の石・石炭・鉄、原木、溶岩、動物、読み込めた割合）、`surveyed(n)`（家が要る。中心は最初に判定したときに 1 回だけ決める）、`survey` の候補とプリミティブ、観測の `survey`、`stored` は家のチェストだけ（前の家のチェストは取り出す元）、建築計画の `site` と `design`、`settleHome`（別の場所に建ち終わったら引っ越し、前の家は `formerHomes` として掘らない・置かない）
- Python: `TownSite`、`MidGoal.prepares_town`（調査と引っ越し。やめられない）、`PLANNABLE_CONDITIONS`（`surveyed` は LLM が書ける条件に入れない）、`TownPlanner.advance`（判定の後に: 調査の中目標 → 選択 → 引っ越しの家と中目標 → 定義。世界とプランから次の手順を決めるので再起動しても続く。LLM が失敗したら次の切れ目でやり直す）、`HouseDesigner`（`house.py`）、`StartPlay` は建築計画から家と完成の告知を決める、準備が済むまで段階の中目標を足さない
- ユーザーの指摘「調べたことは目標と同じように別で整理して置いておけると良さそう」→ 決めたこと（場所、理由、名前: `data/mission.json`）と調べた事実（候補地の数字: ブリッジ）を分け、`stream_context` の欄として目標の決定・実況・返答・ゴールボードに出す（16 §4.5）
- 承認の範囲を狭めた点: 新しいワールドでは場所を決める前に街がないので、「定義を 1 回だけやり直す」ではなく「場所を決めた後に 1 回だけ定める」にした（16 §5）
- ユーザーの問い「gemini が好きに使えるメモリ空間ってないの？」→ ない。推測が事実として戻ると強まるので、種類・量・寿命で縛り「確かめていないメモ」として別の欄に出す案を 18 に書いた（承認待ち、未実装）
- town4 の準備: 旧ワールドを `data/backups/minecraft-world-20260925-town3.tgz` に、`state.json` と `mission.json` を `data/backups/` に退避し、新しいワールドを生成（ユーザーの指示「新しいワールドで最初から」）

### 街の定義、精錬・照明の実機、town1〜3 の修正、日本語化 (2026-09-25)

**Commits**: `2619dea`（A 精錬）、`7faddab`・`74ad12c`（B 照明 `lit(radius)`）、`c7e6310`（15 §3 街の定義）、`0e63a8d`（書き直しで「もうそろっている条件」を断る）、`c8d8201`（遠いチェスト・かまどへ 48m ずつ、作業台は置ける所があるときだけ）、`857edbd`（今できる部分から進める）、`20b4b03`（ミラーでウィンドウが閉じない）、`dbc31aa`（拾えない理由を出す）、`5b51095`（記憶は掘れる物だけ、経路のないブロックは再び出さない）、`6566a6d`（水中の鉱石を掘らない）、`9250e0e`（肉を焼く）、`dd3bb60`・`599ffdf`（日本語化）、設計書 `16_town_site.md`（`d896651`、承認済み）、`17_dig_down.md`（`ea35d69`、承認待ち）

- 街の定義（`TownPlanner`）: Gemini が定義し、各条件をブリッジの `/check`（判定できるか、`impossible`: 手に入るか）で確かめ、だめなら理由をつけて作り直させる。定義は固定して `data/mission.json` に保存。段階の今できる条件を中目標（`stage`）として一番上に入れ、できたこと（`stage_met`）を保存し、`unresolved` が残る段階は終わらない。ソルバーの深さの上限を 6 → 10（何も持っていない状態から鉄の剣まで届かなかった）
- town1: Gemini の定義は 4 段階（安全地帯と装備 / 備蓄庫 / 街域の拡張 / 居住区）で、全段階に今できない部分（柵、倉庫、外周壁、道、2 軒目、畑）があり、街が進まなかった → `857edbd`。止めたとき夜の屋外にいて、ボットが死んだ（私の止め方の問題。以後は家の中で止める）
- 鉄 run（精錬の確認のつもり）: 地表に石がなく探索に約 50 ステップ、頼みの予算 80 で鉄の剣を断念。かまどまで行かず、A は未確認
- town2: 家のまわりに石炭 64 と記憶していたが、全部埋まっていた（掘る候補は空気に面した物だけ）→ 200m 北へ探索 → `5b51095`。海の下の石炭を掘って溺死 → `6566a6d`（水も当たり判定が空で「面している」扱いだった）
- town3（300 ステップ、死亡なし）: `lit(16)` 達成（B の実機確認）、`stored(food, 16)` は 11/16、鉄の剣は丸石 1/3 で止まった。探索 64 回の大半は石探し
- 拾えない件: 失敗に落ちた物の位置と理由を出すようにした。town3 で「1.7m 先、同じ高さ、経路のエラーなし」→ 真下を掘ると拾えた。壁の鉱石を掘った後の 1 マスの隙間に残ると推測。周りのブロックの形を確かめてから直す（未解決）
- ユーザーの指示: 「街づくりするなら場所の選定も必要」→ 設計書 16（候補地の調査 → Gemini が選ぶ → 定義を 1 回だけやり直す → 引っ越し）。4 点とも承認（定義のやり直し、半径 64〜160m の 8 方向、前の家は残す、実装へ）
- ユーザーの指示: 「肉は焼いてから」→ かまどがあり燃料を持つときだけ焼く候補、備蓄は焼いてから入れる（かまどがないときは今までどおり）
- ユーザーの指示: コード・コミット・ログを日本語に（既存分も）。Jev に渡すもの、ブリッジが返す文言、例外のメッセージは英語のまま。5 つのエージェントで並行し、HEAD と構文木を比べて振る舞いが変わっていないことを確かめた
- 介入: なし（town1〜3 は自律）。ブリッジの再起動は家の中にいるときに行った

### 14 の実機確認と修正、松明、街の定義のスパイク (2026-09-25)

**Commits**: `4783255`（全レシピの解放）、`ebaae48`（覚えた資源を探索より先に）、`7557f7d`（未読み込みチャンクでの落ち）、`80eb190`（`stored` の残り作業）、`cb3eb3e`（チェストの予約）、`0181865`（遠い移動を 48m ずつ）、`b07f9f3`（暗い所の松明、返答にできること）、`6cadb8f`（暗さをブロックから判定）、`eec93bc`（危険域の帰宅は危険なときだけ）。設計書 `15_town.md`、`spikes/town_definition.py`

- frun3（旧ブリッジ）: 松明の中目標で 80 ステップ近く待機。原因は、ブリッジがレシピブックでクラフトし、1.21.4 の松明は石のつるはしを持つまで解放されないこと（チェストはスロット 10 個）。サーバーが参加時に `recipe give @a *`（compose の `RCON_CMDS_ON_CONNECT`）→ frun4 の 1 ステップ目で松明ができた
- frun4: 覚えた羊の場所へ向かった（14 stage 2 の実機確認）。食料探しは覚えた牛ではなくメロンを探していた（見えない物は同じコストで、一覧の先頭が選ばれる）→ 覚えた資源を安くした。家から 190m で未読み込みチャンクの仮ブロックに位置がなく、経路コスト関数が例外でブリッジごと落ちた → 仮ブロックはコスト 0
- frun5: 記憶は再起動後も残った。270m 先から帰宅（1 回目は 45 秒の時間切れ）。ベッドを完成（frun2 から持ち越し）、剣も完成。mame の「チェストに食べ物を貯めて」は `stored(food, 10)` の中目標で受けて、順番どおりに返答。チェストの設置（ドアとベッドの列を避けた角）と `deposit` を確認。問題 3 つ: 置いた後に残り作業が増えて停滞と誤判定 → 置く前から集める分も数える。`stored` のための `have(food)` がチェストの羊肉を取り出した → 中目標の `stored` の分を予約（空腹 6 以下の食料は例外）。覚えた場所への 60 秒の移動が Python の 60 秒で先に切れて停止 → 遠い移動は 1 回 48m、行動の上限は 45 秒
- kuma の「羊どこで見た？」に記憶どおり「ずっと北」と答えた。`have(wool, 3)` が白 2 と黒 1 で満たされた（白いベッドには同じ色が要る）。Gemini は次の小目標で `have(white_wool, 3)` に直した
- ユーザーの依頼「暗い所では湧き潰しを兼ねて松明を置きながら進む」をコメントで再現: 今までは「気をつけるね」と、できない約束をした（行動も頼みの置き場所もない）→ 暗い所で「松明を置く」候補、返答にできることの一覧。mineflayer の明るさは開けた地上で空の光 0 を返した（サーバーの `location_check` では明るい）ので使わず、頭上の覆いと 8 ブロック以内の光源で判定する。再実行で家の中に松明を置き（湧き潰し）、返答は「暗い場所では置きながら進むようにしている」になった。初めてベッドで寝て夜を飛ばした
- 体力が危険域のとき、目標に関係なく「家に帰る」を出していたため、食料探しが出ては戻るを繰り返した（frun3 以降の往復の原因）→ 敵がいるか夜のときだけ
- 街の定義（`spikes/town_definition.py`、3 回）: 備蓄と装備 → 敷地の照明 → 複数の建物、の骨格は 3 回とも同じ。鉄の剣を 3 回とも「今できる」とした（精錬がないのでソルバーは入手できない）。設計書 15 にまとめた
- 介入: 明るさの確認で、調査用プレイヤー LightProbe を数秒接続した（初期地点）。frun3 の途中で `PUT /goal` で松明の目標を一時設定した
- 注意: 修正版と元の版を比べるため、`git stash` を 2 回使った（どちらもすぐ戻し、作業ツリーを確認した）。以後は scratchpad へのコピーで比べる

### 世界の記憶: 拠点・見た場所・チェスト (2026-09-25)

**Commits**: `6b245d9`（設計書 14）、`096ded9`（stage 1 拠点）、`46d3d5c`（stage 2 見た場所）、`c404c76`（stage 3 チェスト）

- ユーザーの方針: 「目標の進行度、チェストの中身、装備、拠点の場所などの進行情報は、別途状態として持っておきたい」「役割は永続化と判断材料が大きい。中目標の選択肢にも効く」。既定値 3 つ（動物の記録は 1 日で古くなる、チェストの中身は次に開けるまで記録どおり、空きスロット 8 未満で拠点の中なら `deposit` 候補を出し Jev が選ぶ）と「探索した範囲」の追加は承認済み（14 §8）
- 原則: 今読めるもの（持ち物・装備・体力・目標の進み）は写さず、見えなくなったものだけを見た時刻（`bot.time.age`）つきで覚える
- stage 1: 家の設計（名前、コンセプト、大きさ、ドア）を `PUT /build-plan` と一緒に送り、拠点に残す。設計を飛ばした起動でも `_home_blueprint` で設計図と家の名前を戻す
- stage 2: `memory.mjs`。原木・石炭・鉄・羊・牛・豚・鶏の場所を 16 マス区画ごとに 1 か所（種類ごとに 8 か所まで）、20m 以内で見えなくなったものは忘れる、32m より遠いものを `goto_memory` 候補として探索より先に出す、探索の方角は行っていない区画を先に出す、死んだ場所。`/observe` の `memory` を「覚えている場所」としてプロンプトに出す
- stage 3: プリミティブ `place_chest`（拠点の中、ドアとベッドの列を避けてドアから遠い床）、`deposit`、`withdraw`（開けるたびに中身を記録、無くなったチェストは忘れる）。ソルバーは持ち物の次にチェストの中身から取り出し（`withdraw` の葉）、足りない分だけ集める。述語 `stored(item, n)`（小目標にも中目標の条件にもなる、拠点が要る）。拠点の中で空きが少ないと、今の目標に要らない大きいスタック上位 3 つを `deposit` 候補に出す。プロンプトとゴールボードに家の名前とチェストの中身
- 未確認（実機）: 覚えた場所へ直接向かうか、チェストへの出し入れと再起動後の記録、`stored` の中目標が入れた時点で完了するか

### frun2 で見つかった問題の修正 (2026-09-25)

**Commits**: `2869b8b`、`81d7935`、`f73bf79`、`366949e`、`bdce457`

- frun2（Gemini + Jev の自律実行、300 ステップ中 150 で停止）で見つかったもの
  - 起動のたびに家を設計し直し、完成済みの家が未完成扱いに戻った → 拠点があれば設計しない（`2869b8b`）。設計と名前の引き継ぎは 14 stage 1
  - `through_night` が夕方→夜→明け方の切り替えやステップ予算で打ち切られ、夜越しの途中で目標が変わった → 夜越しはどちらでも終わらない（`81d7935`）
  - 装備が観測になく、中目標の進み具合がプロンプトに要約されていなかった → 観測に装備、`MidGoal.summary()` をプロンプトとオーバーレイに（`f73bf79`）
  - 食料探しが起点のまわりを 21m ずつ往復して飢えた → 探索は目標を設定した地点から離れる方向だけ、飢えたら腐肉も食べる（`366949e`）。食料を `explored` で探そうとした → プロンプトで `have(food, n)` に（`bdce457`）
- 介入: frun2 の起動で 2 軒目のプランが上書きされていたので、`data/state.json` のプランを完成済みの家「シェンのぽかぽかログハウス」（5x5x3、南ドア、原点 406,72,-273）に戻し、拠点に名前と設計を足した（バックアップ: scratchpad `state.before-repair.json`）。72/72 ブロック配置済みを確認
- frun3（上の修正を入れたブリッジ・stage 2 より前、200 ステップ）: 設計を飛ばして家の名前を復元、m1 は最初の判定で完了、満腹度 0 から腐肉・牛肉を食べて回復、夕方に `at_home` → `through_night`、夜の間の返答はリストと一致、朝に `cleared`。気になる点: 前から建っていた家に「家が完成したよ」と実況した（起動時の判定で完了したため）、松明の中目標（視聴者 tori の頼み、石炭がないと進まない）がベッドより前にある

### 目標の階層: 大目標・中目標・小目標、ゴールボード (2026-09-25)

**Commits**: `d88e36a`・`43abeea`（設計書 13）、`1ce9abe`（実装）、`ee30832`（実機確認で見つかった失敗の修正）。確認用スクリプト: `spikes/coherence_live.py`（13 用に書き直し）

- ユーザーの方針: 「コメントの言いなりにならないように。元の大目標から外れないように」「大目標 1 つ、中目標は複数で優先度順、小目標 1 つ」「目標は配信画面にも出したいので外から取れるように」
- 大目標は設定（`minecraft.mission`）。中目標は `MidGoalPlan`（完了条件は `built` / `placed` / `have`、ブリッジの `POST /check` が小目標の切れ目で、決定の前に判定）。小目標は一番上の中目標のため（`serves: current`）か生存のため（`survival`）
- 上限はコードが守る: リスト 6 件、視聴者の中目標は同時に 2 件・1 人 1 件、今の中目標より前に入れない（追加でも `move` でも）、予算 80 ステップ、断念には理由
- 返答は `request`（none / accept / decline）と中目標を 1 回で出す。受けた頼みは今の中目標の後ろに入り、今の小目標は中断しない。上限に当たる・判定できない頼みは理由つきで生成し直して断る
- `MidGoalKeeper`: 編集は写しで試してから `await` なしで実物に反映（決定の生成中に受けた頼みを上書きしない）、追加する条件は `/check` で確かめる、`data/mission.json` に保存（大目標の文が同じなら次の実行で続きから）
- `Narrator`: 中目標の完了・断念（視聴者のも必ず）と自分で足した中目標を、次の小目標と一緒に 1 回で言う。視聴者の受け入れは返答で言ったので繰り返さない
- ゴールボード（`presentation/web/goal_board.py`、extra `ailoveshen[stream]`）: `GET /api/goals`、`/api/goals/stream`（SSE、目標のイベントと各ステップで送る）、`/overlay`（背景透明）。`examples/integration_test_minecraft.py --board-port 8765`
- 12 の頼みの流れ（`ViewerRequest`、`request_goal`、置き換え・拒否のイベント）は削除した（互換層なし）
- 実機確認（家は完成済み）: 連投 5 回で受けたのは 1 件、判定できない頼み・関係ない頼み 3/3 で理由つきで断った、乗っ取りは大目標・中目標・小目標とも変わらず、「今なにしてるの？」は一致、遅延 1.2〜2.9 秒。詳細は設計書 13 §12
- 失敗 1 件 → 修正: 小目標の開始の実況が、終わった小目標の進み具合（原木）で話した。`set_goal` が返す新しい小目標の状態をすぐ観測に入れた
- 未確認: 「家ができたらやるね」の頼みを後で実際に進めるか、予算切れの断念の実機、`/overlay` のブラウザ表示（frun2 で確かめる）
- 介入: tp（家の中）、`time set 3000`

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

### 目標の 4 層（設計書 50、2026-09-29 時点。段階 2 は実装済み。段階 3（改名と語彙）は未着手）

ユーザーの依頼: 大目標を最終目標に、中目標を大目標に、手順（`plan_steps`）を中目標に。設計書 `docs/design/50_goal_layers.md`。段階: 2 仕組み（実装済み。上の Completed Work）→ 3 改名と語彙（1 回で: `MidGoal` → `Objective`、`PlannedStep` → `Job`、`MidGoalPlan` → `ObjectivePlan`、`MidGoalKeeper` → `ObjectiveKeeper`、イベント、`mid_goal_id` → `objective_id`、プロンプトと日本語の表示、ブリッジのエラー文、`abilities.yaml`、設計書 13・26・20、CLAUDE.md。`MidGoal.steps`（予算）は `budget_used` に）。
- 設計書で、ユーザーの決めた「小さい頼みは今の大目標の中目標」を「今の大目標に関係するものだけ」に絞った（§5）。確認待ち
- `data/mission.json` は移行しない。着手前に `data/backups/` へ退避

### 暗い所で松明を自動で置く（2026-09-28 時点、未着手。ユーザーが 1・2 の実装を了承済み）

1. 行動の途中で自動で置く: runner（`runner.mjs`）の 0.5 秒ごとの見張りで暗い状態が続いた時間を数え（`isDark`、`state.darkSince`）、5 秒続いて松明を持っていれば置く。置くのは primitives.mjs に足す `keepLit`（`torchSpot` の足元、`place_torch` と同じ置き方。失敗しても行動は失敗にしない）。掘るループ（`dig_down`、`dig_mine`、`level`）はブロックの区切りで呼ぶ（道具の持ち替えとぶつからない）。歩く行動は終わった直後に runner が呼ぶ。結果の文に置いたことを添える
2. 地下へ行く前に用意: goals.mjs の「ついで」（`upgradesToTry` の近く）に、葉が `go_mine` / `dig_down` / `dig_mine` か地下にいて松明が 8 本未満なら、作るだけでできるとき torch の作成を `also` で出す（材料からなら行で知らせる）
3. （任意・別作業）本物の光の値（ブロックの光）が 1.21.4 で信用できるか実機で確かめる
テスト（test/ に偽の世界）、CLAUDE.md・設計書（新しいか 33/47 の追記）、abilities.yaml の `light_up` に自動で置くことを書く

### 視聴者の助言（設計書 49、2026-09-28 時点）

実機で `python examples/integration_test_minecraft.py --comments examples/comments_advice.json` を回し、各 `pass` を見る。配信では `logs/chat/*.jsonl`（仕分けの種類）と警告「返答 N は〜を流している」「〜を流したまま話す」を見る。畑に水を引くのは Gemini の判断任せ（`use_item` のバケツは実機で未確認）

### アバターの作り直し（設計書 48、2026-09-28 時点）

調査だけ済み。§9（Tripo を誰が回すか・プラン、4 面図の生成、合格の範囲、案 A〜D）をユーザーが決めたら、§8 の試作（Blender と VRM Add-on の準備から）

### 設計書 47 の残り（2026-09-28 時点）

段階 1（`44aa0aa`）、2（`b38cc1c`）、3（`8004636`）、4A（`2391ee3`）、4B（`a281fb2`）、5（`9953314`）は済み。残りは 6（囲い・トラップ、別の設計書: 柵と門、動物を連れて来る、水、ホッパー）と、採掘場の坑道を延ばす・松明（畑の水は設計書 49 で乾いた耕地を知らせ、やり方の帳に足した）

### 次のセッションで決めること・やること（2026-09-25 時点）

0. **OBS の準備**（23 §6）: WebSocket サーバーを有効にしてパスワードを `.env` の `OBS_PASSWORD` に、視点のクライアントを映すソースを `Minecraft`（または `OBS_GAME_SOURCE`）に。`pip install -r requirements.txt` をやり直す（`obsws-python`）。実機で見ること: 画像が撮れるか（黒くないか、縦横比）、定期の見直しで Gemini が画面をどう読むか、1 枚あたりのトークンと時間（`/debug/gemini`）、`look_screen` をいつ使うか
1. **`--control tools` の実機確認**（ユーザーの環境）: ブリッジを再起動し、`python examples/integration_test_minecraft.py --control tools --max-steps 100`。見ること: Gemini の道具の選び方（格子を読めるか、提案との使い分け）、書かれる見張りの質問と Jev の答え（誤検知）、1 ステップの遅延、呼び出し回数とトークン（`logs/watch/*.jsonl`）、`goto` / `place` の実機の動き。town4c の縦穴や town4d の前の家のベッドを試験の場面にする。その結果で、コードの「進んでいるか」を規則と比べる（19 §6）、次の版（`blocks` / `build`、道具の好みの引数）、B（設計書 22）へ
1. **置いた物の扱い**（ユーザーと検討中）: 「使う（use）」と「持っていく（take）」は別の概念として用意する（ユーザーの指摘）。
   - 今の詰まり（新しい家にベッドを置く）を解くのは take: 前の家の家具を掘って持ち物に戻す。ソルバーの入手元（チェスト、かまどに並ぶ）として。対象の案はベッド、作業台、空のかまど、空のチェストで、前の家だけ。ユーザーの返事待ち
   - use（`station` をベッドとチェストにも広げる。例: 前の家のベッドで寝る）は、必要な場面が出てから
   - `useChest` が前の家に入れない不具合も一緒に直す（位置を含む家に入る）
2. **低レベルの操作を Jev に任せる案**（ユーザーの問い）: 前進・曲がる・攻撃などを候補にしてソルバーを飛ばす案。Jev の呼び出しは 1 回 0.2〜0.6 秒で、キー単位の操作（50ms ごと）には遅いこと、空間の判断ができるかは測っていないことを伝えた。中間案は、ソルバーの候補に加えて有界の低レベル候補（隣へ動く、このブロックを掘る、1 段積む、ドアを開ける）を出し、詰まったときに Jev が組み合わせて抜けられるかを測る。今回の穴の地形が試験台になる。測るか、いつ測るかはユーザーの判断待ち
3. **実機の再開**: ブリッジを再起動して 31490a4 / dee7158 を反映し、town4d を続ける（ボットは前の家の中）。16 の検証は、引っ越しの中目標の完了と街の段階 1 の開始まで
4. **18 の残り**（`feat/notes`、`docs/design/18_notes.md` §9）: AdvancePlay と stream_context のテスト、`feat/town-m1-m2` への取り込み、実機
5. 視聴者の頼みの予算: 数字の予算を外し「進まない頼みは理由を言ってやめてよい」とプロンプトに書く案を出した。ユーザーの返事待ち

### 街作りロードマップの続き（09）

- 拾えない件の原因の確定（1 マスの隙間の仮説）
- 起動のたびに `unresolved` の段階を全部 Gemini に書き直させている（能力が増えていなくても）。無駄な呼び出し
- 候補を選んだのに「今は選べない」になる食い違い（観測と実行の間で候補が変わる）
- 実機で未確認: `stored` の中目標がチェストに入れた時点で完了するか（予約と残り作業の修正後）、チェストの記録が再起動後も残るか、`eec93bc` 後の食料探し
- 返答の題名に依頼者名を入れない（「（まめさんの頼み）」が題名と `requested_by` で 2 回出る）
- `have(wool, n)` がベッドに要る「同じ色」を表せない。プロンプトで「ベッドの材料は `placed(bed)` のまま進める」と指示するか
- 敵が家の近くにいてベッドで寝られない（`there are monsters nearby`）。15 B の敷地の照明で減るはず
- 起動時の判定で完了した中目標（前から建っていた家）を「完成したよ」と実況しない
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

### 2026-09-25 (世界の記憶)
- frun2 の途中でユーザーから「進行情報は別途状態として持っておくのが良い」→ 今読めるものは写さず、見えないものだけを覚える形（設計書 14）を提案し、「1 は進めて、2 は永続化と判断材料、3 は次のタスク」と承認された。起動のたびの家の再設計は「完成済みなら飛ばす」で直し、名前は 14 stage 1 で引き継いだ
- `data/state.json` のプランを手で戻した（上の介入）。frun3 はその状態から自律で実行

### 2026-09-25 (目標の階層)
- ユーザーから「コメントの言いなりにはならないように。元の大目標から外れないように」→ 今は大丈夫ではない（頼みが小目標を直接置き換え、上限もない）。ユーザーの構想（大目標1・中目標複数を優先度順・小目標1）に沿って設計書 13 を書いた
- 「目標を配信画面に出したい」→ 外から取れる形はなかったので、13 §7 にゴールボードを足して、13 と一緒に実装した。ブリッジは `/check` のために 1 回再起動した

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
