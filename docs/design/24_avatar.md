# 配信者のアバター（VRM、three-vrm）

作成: 2026-09-25。ユーザーの依頼「VRM モデルを作ったのでプロジェクトに保存した（`models/vrm/ailoveshen.vrm`）。three-vrm などで良い感じに動かせるようにしたい」。**実装済み（最初の版）。**

## 1. 形

- OBS のブラウザソースで開くページ `/avatar`（目標ボードのサーバー、`--board-port`）。1920x1080、背景は透明
- three.js（0.186.1）と @pixiv/three-vrm（3.5.5）は `presentation/web/vendor/` に同梱して `/vendor/` から配る（CDN につながらなくても動く。MIT）
- モデルは設定 `avatar.model_path`（既定 `models/vrm/ailoveshen.vrm`、VRoid の VRM 1.0）を `/assets/avatar.vrm` で配る
- ページは `/api/avatar/stream`（SSE）の合図で動く。合図を作るのは `AvatarStage`（`presentation/web/avatar.py`）で、ドメインイベントを読むだけ（ゲームの判断には関わらない）

## 2. 動き

| 動き | 中身 |
|---|---|
| 立ち姿 | T ポーズから腕を下ろす。息（胸・背骨）、体の揺れ、首の小さな動き |
| まばたき | 2〜6 秒ごと（笑顔のときは弱める） |
| 目線 | ほとんどカメラ（視聴者）、ときどき少しよそ見 |
| 話す | 本文からかなを母音（あいうえお → aa ih ou ee oh）に並べ、長さに合わせて口を動かす。漢字は読みが分からないので字ごとに決まった 2 モーラ。話している間は頭も少し動く |
| 表情 | happy / sad / angry / surprised / relaxed をなめらかに出して戻す（ドメインの感情 excited・scared は組み合わせ） |
| しぐさ | うなずく（nod）、両手を上げて跳ねる（cheer）、びくっとする（flinch） |

## 3. 合図（AvatarStage）

| イベント | 合図 |
|---|---|
| `SpeechStartedEvent`（TTS の再生の直前。`duration_ms` を足した） | 話す（音声の長さ、感情。感情が neutral なら本文の手がかり） |
| `SpeechCompletedEvent` | 話し終わる |
| `CommentaryGeneratedEvent` / `ChatResponseGeneratedEvent` | TTS がないとき（`lip_sync: text`、または `auto` で再生のイベントがまだ来ていない）に話す。長さは本文から見積もる（1 字 140ms） |
| `MidGoalCompletedEvent`、`HouseCompletedEvent` | うれしい＋cheer |
| `TownSiteChosenEvent`、視聴者の頼みを受けた `MidGoalAddedEvent` | うれしい＋nod |
| `GoalEndedEvent` | 達成: うれしい＋nod。行き詰まり・停滞: かなしい |
| `GoalSetEvent` | nod |
| `GameActionExecutedEvent`（被弾で中断） | 驚き＋flinch |

本文の手がかり（`TEXT_CUES`）: 「うわ・えっ」→ 驚き、「ざんねん・うう」→ かなしい、「くやしい」→ 怒り、「やった・できた・ありがとう」→ うれしい。Gemini はまだ感情を出していない（`LLMService.update_emotion` を呼ぶ所がない）ので、これとゲームの出来事で表情を決める。

## 4. 使い方

- `examples/integration_test_minecraft.py --board-port 8765` → OBS のブラウザソースに `http://127.0.0.1:8765/avatar`（幅 1920、高さ 1080）
- 見た目だけ: `/avatar?demo=1`（話す・表情・しぐさを繰り返す）
- `?view=full`（全身）、`?pos=left|center|right`（既定 right）、`?scale=1.2`、`?model=URL`
- ブラウザのコンソールから `avatarCue({type: 'emote', emotion: 'happy', gesture: 'cheer'})` で試せる

## 5. 確かめたこと・まだのこと

- ヘッドレスの Chromium（WebGL はソフトウェア）で、読み込み・立ち姿・話す口・4 つの表情・cheer・全身を撮って確かめた。単体テストは合図の変換と配信の経路
- まだ: OBS での表示と重さ（GPU で動くはず）、TTS と合わせたときの口の合い方、実際の配信のイベントでの表情の出方

## 6. 次にできること

- 口を音声の大きさに合わせる（TTS の音声から 20ms ごとの大きさを出して合図に入れる。今は本文から）
- Gemini に感情も出させる（実況・返答の生成で感情を選ばせ、`SpeechStartedEvent.emotion` に入れる）
- ゲームの状況で姿勢を変える（夜は眠そう、戦うときは身構える）

## 7. モデルのライセンス

モデルのメタ情報は `avatarPermission: onlyAuthor`、`commercialUsage: personalNonProfit`。作者本人の配信には問題ないが、収益化する配信で使うなら VRoid Studio でライセンスの設定を見直す。
