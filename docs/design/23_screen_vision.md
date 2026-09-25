# Gemini が配信の画面を見る（OBS のスクリーンショット）

作成: 2026-09-25。ユーザーの問い「行き詰まったときや定期的に、画面のスクショを Gemini がチェックして軌道修正や状態チェックをするようになっているか。できるか」→ なっていない（Gemini が見るのはブリッジの文字の状態だけ）。ユーザーの決定「OBS で良い。OBS を使う前提で進めて」。**決定済み。この設計書のとおり実装した（§8）。実機（OBS、Gemini）ではまだ動かしていない。**

`22` は B（技）のために空けてある。Phase 7（`07_phase7_obs_integration.md`）のうち、ここで作るのはスクリーンショットだけ（場面の切り替え、字幕は 07 のまま）。

## 1. 撮り方

- OBS WebSocket 5（OBS 28 以降に内蔵）の `GetSourceScreenshot` で、**ゲームを映しているソース 1 つ**を撮る（設定 `obs.game_source`）。ライブラリは 07 のとおり `obsws-python`
- シーン全体は撮らない: 目標ボードやデバッグ画面が映り込み、Gemini が自分の目標の一覧を画像で読むことになる。ソース名が空か、OBS にそのソースがなければ、画像なしで続ける（理由をログに 1 回）
- JPEG、幅 768 まで縮める（縦横比は保つ）、品質 70
- OBS が閉じている・つながらない・遅いときは、画像なしで続ける。プレイは止めない。1 回の撮影は 3 秒で打ち切り、失敗したら 60 秒は撮らない（毎ステップ接続の待ちを払わない）
- パスワードはログに出さない（`obsws-python` は接続時にパスワードを INFO で出すので、そのロガーを WARNING にする）

## 2. いつ見せるか

どれもステップのループの中で行う（小目標を変えるのはループだけ。12、13）。ブリッジが実行中・反射中のときは見ない。

| 場面 | 何をする | 間隔 |
|---|---|---|
| 定期の見直し | 画像と「配信者が今していること」を見せ、今の目標と画面が食い違っていないかを聞く（`purpose="screen_review"`） | `minecraft.vision.review_interval_seconds`（既定 240 秒） |
| 失敗の後 | 行き詰まり・停滞の後の小目標の決定（`goal_after_failure`）と、失敗・見張りの中断の後の道具の選択（`tool_after_failure`）に画像を 1 枚添える | `failure_interval_seconds`（既定 60 秒）より短い間は添えない（town4c の穴では数秒ごとに失敗した） |
| Gemini が自分で見る | 道具モードの調べもの `look_screen`。画像は同じステップの次の道具の選択にだけ添える（ステップをまたいで持ち越さない）。1 ステップの調べもの 3 回の上限に数える | — |

定期の見直しの出力（JSON）: 画面に見えること（1〜2 文）、今の目標と合っているか、気になること、考え直すべきか。「考え直す」なら、今の小目標を行き詰まりと同じ経路で終わらせ（理由は「画面を見て: …」）、次の小目標の決定がその理由を見る。見直しが目標を直接決めることはない。

## 3. 画像から分かったことの扱い

- 完了の判定には使わない（完了はブリッジが世界から判定する。13、15 §2）
- Jev には渡さない（Jev は JSON と文字だけ）
- 見直しで書いた「画面で見たこと」は `PlaySession` に 1 件だけ持ち、`activity()` から目標の決定・実況・返答に出す。**Gemini の解釈で確かめていないもの**として、何分前かと一緒に、世界の状態とは別の行に書く（18 のメモと同じ扱い）

## 4. Gemini への渡し方

- `ITextGenerator.generate_json` と `choose_tool` に `images`（`Screenshot` の並び）。アダプターはテキストの部分と画像の部分（`Part.from_bytes`）を並べて送る
- 画像があるときは `media_resolution` を設定（既定 `low`）で送り、画像のトークンを抑える
- 考える深さ: `screen_review` は medium（`gemini.thinking_levels`）

## 5. デバッグ

- `/api/debug/gemini` の各呼び出しに、添えた画像の id・形式・大きさだけを入れる（画像そのものは入れない。ページは 2 秒ごとに読む）
- 画像は直近の数枚だけ別に持ち、`/api/debug/screen/<id>` で返す。`/debug/gemini` のページはそこから `<img>` を読む

## 6. OBS の準備（ユーザーの環境）

0. Python 側に `obsws-python` を入れる（`pip install -r requirements.txt`）。ないと「モジュール obsws_python がない」と 1 回出して、画面なしで続ける
1. OBS の「ツール」→「WebSocket サーバー設定」で「WebSocket サーバーを有効にする」。ポート 4455、「認証を有効にする」をオンにし、「接続情報を表示」でパスワードを確認する
2. `.env` に `OBS_PASSWORD=...`（ホストとポートを変えたなら `config` の `obs.host` / `obs.port`）
3. ボットの視点のクライアント（Minecraft 1.21.4 で `127.0.0.1:25578` に接続）を「ウィンドウキャプチャ」か「ゲームキャプチャ」のソースで映し、そのソース名を `obs.game_source`（既定 `Minecraft`）と同じにする

## 7. 構成

| 層 | 追加・変更 |
|---|---|
| domain | `Screenshot`、`ScreenNote`。`PlaySession.screen_note`、`request_rethink(reason)`（次の切れ目で小目標を終わらせる）、`Activity.screen_note` |
| application | ポート `IScreenCapture`、`ITextGenerator` の `images`、`IGenerationLog` の画像。`vision.py`（`ScreenReviewer`、`VisionPolicy`）。`AdvancePlayUseCase` の定期の見直し・失敗の後の画像・`look_screen` |
| infrastructure | `ObsScreenCapture`（`obsws-python`、遅延 import、3 秒、60 秒の待ち、ロガー）、Gemini の画像と `media_resolution`、見直しのプロンプト、`stream_context` の「画面で見たこと（確かめていない）」、設定 `obs.game_source` ほか、`minecraft.vision` |
| presentation | 目標ボードの `/api/debug/screen/<id>`、デバッグのページに画像 |

## 8. 実装と検証

実装: このブランチ（コミットは WORKLOG）。単体テスト: OBS のアダプター（偽のクライアントで、データ URI、`image/jpg` → `image/jpeg`、時間の上限、失敗の後の待ち、ソースがないとき、パスワードをログに出さない）、見直しの間隔と「考え直す」、失敗の後の画像の間隔、`look_screen` の画像が次の呼び出しだけに行くこと、Gemini への実際のリクエスト本文（画像の部分）。

未確認（実機）: OBS への接続と実際の画像、3.8 Flash が Minecraft の画面をどれだけ正しく読めるか（穴、壁、夜、敵）、1 枚あたりのトークンと時間。
