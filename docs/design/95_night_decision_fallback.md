# 95. 夜に小目標が決まらず、寝ないまま夜が明けた

2026-10-07、user: 「べっどがそばにあって敵もいないのに寝ずに夜を明かしていました」。

## 1. 起きていたこと

00:42〜00:52Z（夜）、ベッドが 0.7 m にあり、敵もいなかった（`logs/bridge.log`、`logs/steps`、`logs/generations`）。

- 00:33 に `facility(wheat_farm)` が行き詰まって終わり、そのあと小目標の決め直しが空回りした。00:33〜00:52 に実行した手は 0 件
- ローカル LLM（Gemma）は毎回 `have(raw_iron, 3)`（+ `have(coal, 2)`）を選び、ブリッジが「進められない（`dead_end`）」と差し戻した。3 回続けて失敗すると `TextGenerationError`、待ってまた最初から。約 20 分
- プロンプトには「時間帯: 夜（朝まで約 8 分）」と「ベッド 0.7m、家のもの」が出ていたが、差し戻しの文は「進められない。今できる小目標を選べ」だけ。夜だとも、寝ればよいとも書いていなかった
- 夕方→夜で小目標を切る処理（`goal_end_reason`）は、実行中の小目標にしか効かない。決め直しの最中は働かない（00:31 は小目標があったので切れて、寝た）

穴: 夕方・夜に夜を越す以外の小目標を選び続けても、コードが受け止めない（逆向きの `_check_survival_time` だけがあった）。ブリッジが寝るのを禁じていたわけではない。

## 2. 直したこと（`application/use_cases/play.py`）

1. `_night_note`: 夕方・夜に、夜を越す以外（`through_night` / `at_home` / `cleared` 以外）の決定を差し戻すとき、理由に「今は夜。外でできることがなければ through_night を選ぶ（家に帰り、眠れる時間になればベッドで寝る）」を添える（`_decide_goal` の差し戻し。`dead_end`、済み、その他）
2. `_spend_the_night`: 決定が `max_goal_attempts` 回通らず、夕方・夜なら、`TextGenerationError` にせず `through_night` を小目標にする（`objective_id` なし。生存のため）。ブリッジが断れば今までどおり `TextGenerationError`。昼は今までどおり

テスト: `test_play_use_cases.py`（4 件。1・2 を戻すと落ちる）。

## 3. 確かめたこと・限界

- 実機は未確認。Python 側だけの変更なので、配信（`python -m ailoveshen.stream`）の再起動で効く
- 夜にこの回復が働くまでに、3 回の LLM 呼び出し分（約 30 秒）かかる
- 空回りの原因（`have(raw_iron)` を選び続ける LLM の癖、鉄の入手手段がない `dead_end`）は直していない。昼は同じ空回りが続きうる
- 昨夜 22:07〜23:04Z の寝られなかった夜は別の原因（サーバーの `there are monsters nearby`）
