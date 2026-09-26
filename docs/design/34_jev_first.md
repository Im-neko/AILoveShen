# 34. Jev でできることは Jev に（まず Jev、迷ったら Gemini）

作成: 2026-09-26。21（道具での操作）、26（Jev の小目標）、24（アバター）、27（失敗の分析）、12・13（返事と頼み）に関係する。

## 1. きっかけ

ユーザー（2026-09-26）: 「道具モードだけにしていい」→「Jev の役割を増やせないか。Jev でも可能な部分はなるべく Jev にしたい」→ 5 つの案（下の §3〜§7）を「順に実装して」。

道具モードでは 1 手ごとに Gemini を呼ぶ。Jev（TypeSafe の System One）は、決まった選択肢から選ぶ・はい/いいえに答えるのが速く安い。文を書くこと（実況、返事、技のコード、建物の設計）はできない。

## 2. 共通の決まり

どれも同じ形にする（反射 `reflex.judge`、アバター `avatar.judge`、小目標 `small_goals` と同じ）:

- 設定で `jev` か前のやり方（`gemini` / `always` など）を選べる。既定は `jev`
- Jev の答えを待つ上限がある。時間切れ・失敗・確信度が低いときは前のやり方に戻る（プレイは止めない）
- 答えは `logs/<用途>/*.jsonl` に残す（選択肢、選んだもの、確信度、前のやり方に回した理由）。見比べて閾値を直すため
- Jev に「済んだか」は判定させない（ブリッジが世界から判定する）。選択肢に優先順位や「おすすめ」を書かない（`spikes/primitive_choice_eval.py`: 順位をつけると悪くなった）
- テストは台本どおりに答える偽の `IFastJudge` / `IActionSelector` で、確信度が低い・時間切れ・失敗・選択肢なしの道を確かめる

## 3. 1 手の操作: まず Jev、迷ったら Gemini（`minecraft.agent.step_picker`）

道具モードの 1 ステップの初めに、Jev（候補モードと同じ `IActionSelector`）が選ぶ。選択肢は:

- ブリッジの候補（`obs.candidates`、同じ観測のものをそのまま。id は `/act` で照らし合わせるので、間で観測し直さない）→ `do_suggestion(id)` として実行
- 覚えた技のうち、一番新しい版が成功していて（`verified`）、引数の要らないもの（Jev は引数を埋められない）→ `run_skill(name)`
- `ask_gemini`（「どれでもない」）

実行は Gemini が選んだときと同じ経路（`ToolWatcher.run` / `_run_skill`）で、`_recent_tools` と `session.record` に同じ形で残る（Gemini が次に見る道具の記録、失敗の分析（27）もそのまま）。見張りの質問はない（コードの進み具合の質問だけ）。意図（`intent`、目標ボードに出る）は候補の説明。

Gemini に回すとき:

- Jev が `ask_gemini` を選んだ、確信度が `step_min_confidence`（0.5）より低い、時間切れ（`step_timeout_seconds` 3 秒）、失敗した
- 選択肢（候補と技）がない
- 直前の道具が失敗した（道具の失敗の後は Gemini が画面も見て考える: 19 §7、23 §2）
- Jev の選んだ手が `step_max_failures`（2）回続けて失敗した
- Jev が `gemini_every`（8）回続けて選んだ（Gemini が道具を調べる・技を書く機会を残す）

これで候補モード（`--control candidates`）の良いところは道具モードに入る。候補モードは比べるために残す。

## 4. 読み上げの感情（`tts.emotion_judge`）

今は Gemini の文から感情を取っておらず、声はいつも中立だった（`LLMService.update_emotion` を呼ぶところがない）。読み上げる前に、アバターの表情を選ぶのと同じ 1 回の Jev の呼び出し（`AvatarDirector.react`、表情・強さ・しぐさ）で感情を選び、その感情を読み上げに渡す（`TTSService.speak(emotion=)`）。表情と声がそろう。

- 表情 → 声の感情: happy → happy、sad → sad、angry → angry、surprised → surprised、relaxed / neutral → neutral。強さはそのまま `intensity` にする（Irodori-TTS の感情の声と説明は `caption_min_intensity` 0.6 以上、Style-Bert-VITS2 は `emotion_style_map`）
- 選んだ反応は、同じ文の読み上げが始まったときにアバターがそのまま使う（同じ文でもう一度 Jev を呼ばない）
- Jev が答えられなければ、前と同じ（中立の声、アバターは規則）
- モデルにないスタイル（`emotion_style_map` が指す感情のスタイルをまだ足していない）は Neutral で読む（Style-Bert-VITS2 のサーバーは 422 を返し、その文が読まれなかった）。接続のときに `/models/info` からスタイルを読む

## 5. コメントの仕分け（`twitch.response.triage`）

返事を作る前に、Jev が 1 回の呼び出しで 2 つ答える: 返事をするか（yes/no）と、種類（request 頼み / advice 助言 / withdraw 取り下げ / question 質問 / chat 雑談 / noise 返事の要らないもの）。

- **頼み・助言・取り下げは必ず Gemini に回す**（受ける・断る・考え直す・教訓・取り下げは、返事と同じ 1 回の生成で決める: 12・13。黙って落とさない）
- 返事をしないのは、Jev が「返事は要らない」かつ種類が chat / noise で、確信度が `skip_min_confidence`（0.7）以上のときだけ
- 取り下げと頼みは、待っているコメントの先頭に並べ替える（急ぐ）
- 返事をしなかったコメントは文ごと `logs/chat/*.jsonl` に残す（取りこぼしを後で確かめる）

## 6. 実況の間合い（`stream.commentary_judge`）

目標の切れ目の実況（Narrator）の前に、Jev が「今話す価値があるか」（yes/no）を答える。起きたこと（`events`）と今の様子を見せる。

- 見どころ（中目標の完了、家の完成、技を覚えた、中目標をやめた、視聴者の頼みに関わること）は Jev に聞かずに必ず話す
- 最後に話してから `max_silence_seconds`（60 秒）たっていれば必ず話す（黙り続けない）
- 話さなかった出来事は捨てずに取っておき、次に話すときに一緒に話す
- Jev に聞くのはバックグラウンドで（イベントの発行はハンドラーを待つので、聞くのを待つとプレイのループが止まる）

## 7. 失敗の後の振り分け（`minecraft.agent.failure_routing`）

小目標が行き詰まった・進まなかった（stuck / stalled）とき、今は必ず Gemini が原因を分析して決める（27）。その前に、Jev に「一番上の中目標のまだ済んでいない別の手順」か「Gemini に考え直させる」（`ask_gemini`）かを選ばせる。

- 同じ種類の小目標をもう一度は Jev の選択肢に入れない（同じやり方を繰り返すには、Gemini の分析と助言が要る: 27 の `_check_remedy`）
- 次のときは Jev に聞かずに Gemini: 同じ種類の失敗が 2 回続いた、種類を問わず 3 回続いた（A→B→A と行き来しない）、死んだ、中目標が進まない（`mid_goal_stall_steps`）、画面で考え直すことになった、別の手順がない
- Jev が選んだら、失敗の分析は行わない（代わりに、失敗した小目標は記録に残り、次に Gemini が決めるときに見える）

## 8. 決めなかったこと・あとで

- 技の引数を Jev に埋めさせる（数や名前は Jev の不得意な自由な値）
- Jev の自信の閾値の調整は、記録（`logs/steps`、`logs/chat`、`logs/commentary`）を見てから
- 実機での確認はまだ（どれも）
