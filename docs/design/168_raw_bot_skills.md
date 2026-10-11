# 168. 技が本物の mineflayer の bot を触れる（実験）と、版の成績・切り戻し

2026-10-11、user: 「mineflayer の API をそのまま LLM に見せて完全に操作を任せたらどうなるか試したい」「手の不足だったのはそうかもしれません。なので API を直接 LLM が扱えるようにして手を自分で作れるようにして欲しい。また自己改善ループも組み込んで成長できるようにしたい」
user の答え: 渡し方 = **今のスキルに `bot` を足す**／ガード = **反射だけ残す**／場所 = **ベンチ用サーバーで先に試す**／成長の仕組み = **保存前の検証・失敗の定期的な振り返り・成績の記録と比較**／実行方式 = **メインプロセスで vm 実行**。

## 1. 事実（調査）
- 技（設計書 22）は、LLM が JS で書き、isolated-vm で動く。触れるのはブリッジの道具（`t.goto` `t.dig` …）だけで、`bot` は見えない。書き直し（130）・同じ理由の失敗で止める（120）・Jev が検証済みの技を選ぶ（34）はすでにある
- 作業知識の断りは、すでに警告に変わっている（`BRIDGE_GUARDS=free` が 2026-10-09 から既定。設計書 124・125）。残っている断りは安全（家・水・溶岩・反射）と事実（対象がない、届かない）
- 断られた手 5173 件（全結果 30528 件の 16.9%、`tools/refusal_report.py`）の内訳: 材料がない 22.5%、距離 18.6%、古い対象 13.0%（ここまで世界の事実）、反射 6.4%・水/閉所 5.3%・台帳 3.3%・夜 1.2%（ガード）、呼び方 5.5%、その他 24.1%（未分類）。ガードだと言い切れるのは全結果の 3% 弱。「詰まりの大半はガード」は数字では支持されない。この日にブリッジへ足した手（`clear_room` `clear_site` `pave` `clear_former_home`）は、断りではなく**手の不足**への対処だった。今回の実験が測るのは、そちら（手を自分で作れるか）
- 跳び置きの失敗（設計書 131）は、mineflayer の `placeBlock` が次の tick まで待つ点に原因の候補があり、LLM が生の API を呼んでも同じ待ちが残る。生の `bot` にしたからといって直る問題ではない。ただし、`jumpAndPlace` を技として書き直して試す道具は、これで揃う

## 2. 生の bot を渡す（実装済み・段階 1）
スイッチ: ブリッジを `BRIDGE_RAW_BOT=1` で起動し、Python 側も同じ `BRIDGE_RAW_BOT=1`（`minecraft.agent.skill_raw_bot`、`config/default.yaml` が環境変数から読む）。**既定は両方オフ**。ベンチは `BRIDGE_RAW_BOT=1 mise run bench` で両方に届く。

- 技に `raw: true` を付ける（`PUT /skills/<name>` の本文、`SkillDraft.raw`、`SkillInfo.raw`。一覧に「（生の bot）」）。オフのブリッジは raw の保存を断る（`raw skills are off here`）。書き手にだけ `raw` を見せる（`skill_schema(raw_bot)`）
- raw の技は isolate ではなく、**メインプロセスの `vm`** で動く（`skills.mjs` `runRaw`）。`t` は今の API（`t.goto` `t.state` `t.judge` …）をホストの関数として渡し、そこへ `t.bot`（本物の bot）、`t.Vec3`、`t.goals`（mineflayer-pathfinder）、`t.sleep(ms)` を足す。`require` `process` `fetch` は見えない（ただし **`vm` は安全の境界ではない**: `t.bot.constructor.constructor('return process')()` で抜けられる。使い捨ての世界でだけ使う理由）
- 後始末（`releaseBot`）: 技が付けたリスナー（`bot.on` と `bot._client.on` は Proxy で覚えておく）、`t.sleep` のタイマー、操作キー（`clearControlStates`）、経路の目標（`pathfinder.setGoal(null)`）、掘り（`stopDigging`）を、終わり・止められたときに外す。残すと次の技や反射と二重に動く
- 反射: `preempt`（`index.mjs`）が、実行中の技が raw なら先にその技を止める（行動の道具がない間、`state.current` が空で、反射が操作キーを握った後に技が書き換え続けるのを防ぐ）
- 上限: `t.*` の行動 40 回・調べもの 200 回は同じ。生の `bot` の呼び出しは数えない。代わりに時間で切る（`rawMs` 10 分）。最初の `await` までの同期の部分は `vm` の時間切れ（`cpuMs` 1 秒）で止める。**`await` の後に回り続ける同期ループは止められない**（メインプロセスが固まる。isolate なら止められた所）。実験の限界として残す
- 失敗の記録: 生の `bot` の呼び出しは「最後の道具」に載らない。例外は理由に（先頭のフレームつき）、途中の様子は `t.log` に書かせる（書き手のプロンプトに書いた）
- 書き手のプロンプト（`SKILL_RAW_BOT`）に mineflayer の要点（`bot.entity` `blockAt` `dig` `placeBlock` `setControlState` `lookAt` `waitForTicks` `on` `_client.write` `registry` …）と決まりを足した。道具の定義は増やしていない（技を書くのは別の呼び出しなので、定義の上限 8920 に響かない）
- 値の渡し: `vm` の中で作ったオブジェクトは別の realm のもの（`instanceof Array` が偽になる等）。`bot` に渡す値は `Vec3` などホストのものを使わせる

## 3. ガード（反射だけ残す）
raw の `bot` 経由の操作には、ブリッジの安全の断り（保護ブロック・夜の籠もり・水の断り・台帳）は掛からない。残るのは反射（敵・溺れ。働くと raw の技は止められる）。`t.*` の道具を呼んだときは、今までどおりの断りが掛かる（free の警告を含む）。user の「ブリッジでのガードの類は無くしたい」は、ここでは `bot` を直接触る手に限って実現した。道具側のガードを外すかは、ベンチの結果を見てから

## 4. 版の成績と切り戻し（実装済み・段階 2/4）
- 事実: 書き直した版は、書いてすぐ 1 回試す。その試しが失敗すると、その技は未検証（`verified` 偽）になり、Jev に出なくなる。前に成功した版は残っているのに、既定では動かない（`run_skill` は版を指定しなければ一番新しい版）
- 変更: 版を指定しない実行（`runSkillOnce`）は、一番新しい版が **1 回以上動いて一度も成功していない**とき、前に成功した版のうち成功率が一番高いもの（同じなら新しい方）を動かす（`SkillStore.defaultVersion`）。結果に `fell_back_from`（`SkillRun.describe` が「v2 failed its trial, so the version that worked was run」と言う）。まだ動いていない新しい版は、そのまま動かす（書いた直後の試し）
- 一覧（`GET /skills`）: `verified` は「版を指定せずに動かす版」が成功していて、同じ理由の失敗が続いていないこと（切り戻す版を含む）、`runs_version`、版ごとの `versions: [{version, uses, successes, failures, avg_seconds}]`。`record` が実行の秒数を足す（`seconds_total`）。既存の `last_good_version` と `version`（一番新しい版）は変えていない（直すときの前の版は新しい方）
- 限界: 試しが 1 回の環境のせいで失敗しただけの新しい版も、以後は動かない（直すなら書き直し＝新しい版）。書き直しは 1 時間に 3 回まで（`skill_rewrites_per_hour`）

## 5. まだ作っていないもの
- **失敗の定期的な振り返り**（user が選んだ 3 つ目）: 拒否・失敗の記録をまとめて LLM に見せ、足りない手を自分で見つけて技にする。LLM の呼び出しの目的（purpose）と周期が新しく要る一番大きい部品で、raw の技が実際に出す失敗の記録（`t.log`・例外・`fell_back_from`）を見てから形を決めたほうが当たる。ベンチの記録を見てから作る
- ベンチは実行ごとに `SKILLS_DIR` を分けるので、技は実行をまたいで育たない（実行の中で書いて直す所までを測る）。育つかを測るには別の設計が要る
- 検証が要るもの: 実機で raw の技が動くか（`bot` の Proxy が mineflayer の内部と衝突しないか）、書き手が mineflayer を正しく書けるか、`vm` の別 realm の値が mineflayer に弾かれないか

## 6. 測り方（予想を先に書く）
`BRIDGE_RAW_BOT=1 mise run bench -- --tasks food,pickaxe,build` を、オフと比べる。予想: 道具の作成連鎖と単純な移動は raw でもできる。足場積み・夜越し・長い作業は失敗する。遅延と費用は増える。外れたら、そのこと自体が収穫。成否は課題の条件をブリッジが世界から判定する（技の `done()` では成功にならない）

## 7. テスト
ブリッジ `test/raw-skills.test.mjs`（15 件）: 保存の可否（フラグ）、`bot` が見える/見えない、`bot` と `t.*` の併用（`this` が正しい）、終わりの後始末（リスナー・操作キー・経路・掘り）、止められると `t.sleep` が即終わる、例外の理由、行動の上限、`rawMs`、同期の暴走、`require`/`process`/`fetch` が見えない、一覧の `raw`、切り戻し 4 通り、秒数の平均。`test/skills.test.mjs`（`verified` が切り戻す版で決まる）。Python `test_skills.py::TestRawBotSkills`（6 件）: スキーマ、保存、プロンプトの有無、一覧の表示、`fell_back_from` の表示
