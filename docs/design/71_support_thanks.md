# 71. 配信への応援にお礼を言う（フォロー、サブスク、ギフト、ビッツ、レイド）

2026-09-30。状態: 実装済み、実機では未確認（配信の再起動と、フォロー通知のトークンが要る）。

## 1. きっかけ

user（2026-09-30）: 「Twitch の CLIENT_ID をセット済みだが、フォローとか諸々ちゃんと通知を受け取れているか」→ 受け取れていなかった
（`TWITCH_CLIENT_ID` は設定に読み込まれるだけで、使うコードがなかった。チャットは匿名の IRC で、通常のコメントしか読んでいない）。
「実装してほしい。お礼を言いつつアバターも動かしてほしい」。

## 2. 決めたこと

**受け取り方は 2 本**（フォローだけが認証を要る）:

- **IRC（チャットの接続。認証なし）**: `CAP REQ :twitch.tv/tags twitch.tv/commands` にして、USERNOTICE の `msg-id`
  が `sub` / `resub` / `submysterygift` / `subgift` / `raid` のものと、PRIVMSG の `bits` タグを応援にする
  （`twitch_irc_chat.py` `parse_support`）。`TwitchIrcChat.supports()` が流す。まとめて贈ったときの 1 人ずつの
  `subgift`（`msg-param-community-gift-id` 付き）は数えず、`submysterygift` の 1 回だけお礼を言う。匿名で贈った人は「匿名」
- **EventSub（WebSocket。フォローだけ）**: フォローは IRC に来ない。`twitch_eventsub.py` `TwitchEventSub`:
  `wss://eventsub.wss.twitch.tv/ws` に つなぎ、ようこそ（`session_welcome`）の 10 秒以内に `channel.follow` **v2**
  （condition は `broadcaster_user_id` と `moderator_user_id`。スコープ `moderator:read:followers`。トークンの持ち主が
  配信者本人かモデレーター）を購読する（公式ドキュメントで確かめた）。`session_keepalive` の間隔（+10 秒）黙れば、`session_reconnect`
  は新しい URL へ先につないでようこそを待ち、古い接続を閉じる（購読は引き継がれる）。切れたら 1 秒から倍々に最長 60 秒でつなぎ直して
  購読を作り直す。`message_id` で重複を捨てる

**起動時にトークンを検証する**（`prepare()`。黙って何も届かない状態にしない）: `id.twitch.tv/oauth2/validate` で、無効・期限切れ、
Client-Id の食い違い（`TWITCH_CLIENT_ID` と別のアプリで作ったトークンは購読が 401 になる）、スコープ不足を、理由と直し方つきで ERROR に出して、
つなぎ直さずに終わる（サブスクなど IRC の応援は続く）。`GET /helix/users` で配信者の id を調べる。購読が 401 / 403 なら同じく理由を出して終わる。
トークンは自動で取って更新する（下の「トークンの自動取得と更新」。2026-10-03 から。それまでは `TWITCH_EVENTSUB_TOKEN`、なければ `TWITCH_ACCESS_TOKEN` を手で作って書いた）。
`TWITCH_CLIENT_ID` がなければ、フォローは受けないと警告して、ほかの応援だけ受ける。

**お礼の言い方**（`application/use_cases/support_thanks.py`）: LLM は呼ばない。応援は待たせず、決まった言い方から選ぶ
（種類ごとに 1〜3 通り。直前と同じ文は避ける）。名前の読みは、読み上げの前に既存の辞書（`NameReadings`）が当てる。
フォローは名前を呼ぶのが 3 人まで（「A さん、B さん、C さんほか 2 人」）、ビッツは 100 以上で数を言う、レイドは 2 人以上で人数を言う、
Tier 2 以上のサブスクは Tier を言う、続けてのサブスクは何か月目かを言う。
LLM の一言にしなかった理由: 応援は同時に何件も来る（レイドのフォロー）、ローカルの LLM は数秒〜数十秒かかる、名前と数を間違えてはいけない。

**お礼を言う流れ**（`presentation/services/support_responder.py` `SupportResponder`）:

- 溜めて 1 つずつ言う。フォローは、最初の 1 件から 2 秒（`batch_seconds`）待って、溜まったフォローすべてを 1 回にまとめる。
  ほかの応援は 1 件ずつ。お礼とお礼の間は 3 秒以上。20 件（`backlog`）を超えたら古いものから捨てる
- 同じ人のフォローは 1 時間（`dedupe_seconds`）は繰り返さない（フォロー・解除の繰り返し）
- 配信者自身、書き込むアカウント、`twitch.ignore_users`（ボット）の応援には何も言わない
- 言う前に `SupportReceivedEvent` を出し、`say_support`（`factories/stream.py`）が HIGH の優先度で、喜んだ声（HAPPY 0.9。
  Jev の判断は待たない）で話す。`Conversation` に実況として残す（あとの実況が、誰かがフォローしたと分かる）。`logs/spoken` に `support`
- 1 件の失敗（TTS が落ちている）で止めない

**アバター**（`presentation/web/avatar.py`）: `SupportReceivedEvent` を購読し、表情はハッピー。フォローとレイドは手を振り
（`wave`、強さ 0.9）、サブスク・ギフト・ビッツは万歳（`cheer`、1.0）、4 秒。Jev（`avatar.judge: jev`）が表情としぐさを選べるよう、
`moment_of` に「応援があって、お礼を言っている」を足した（規則が下敷き。お礼の文の口の動きは、いつもの `SpeechStartedEvent`）。

## 3. 設定

`twitch.support`（`config/default.yaml`）: `enabled`、`follows`（EventSub を使うか）、`min_interval_seconds` 3、`batch_seconds` 2、
`backlog` 20、`dedupe_seconds` 3600。`twitch.eventsub_token`（`TWITCH_EVENTSUB_TOKEN`）。依存: `websockets`（`stream` extra）。
トークンの作り方と、ログの読み方は `docs/streaming.md`。

## 4. 採らなかったこと

- 全部を EventSub にする: サブスク・ギフト・ビッツにはそれぞれ別のスコープ（`channel:read:subscriptions`、`bits:read`）が要る。
  IRC なら認証なしで、同じ内容が届く。EventSub はフォローだけにした（トークンがなければフォローだけ届かない）
- ビッツ・サブスクに添えられたメッセージへの返事: 添えられたコメントは、いつもどおりチャットのコメントとして返事の対象になる
  （お礼は定型の一言で、メッセージには触れない）
- ハイプトレイン、チャンネルポイント、投票、予想: 種類が多いので、要るものが出てから
- お礼の LLM の一言: 上の理由

## 5. 実機で見ること

- `[support] follow: <名前>` → `[say] <名前>さん、フォローありがとう…` の順にログが出て、アバターが手を振る
- 起動で `[eventsub]` の ERROR が出ていないこと（出ていれば、そのとおりに直す）
- サブスクとレイド: Twitch のテスト機能（`twitch event trigger` の CLI、ダッシュボードの「アラートのテスト」）では IRC に流れない
  ことがある。本物の応援か、友人のアカウントで確かめる
- 読み上げが応援のお礼で詰まったら（続けて何件も来る）、間隔とまとめ方（`twitch.support`）を見直す

## トークンの自動取得と更新（2026-10-03、実機の Twitch では未確認）

user: 「自動でトークンの取得・リフレッシュ出来るようにならないですか？」（public クライアントは Client Secret が発行されず、`twitch token -u`（Twitch CLI）は Secret が要るので使えない。ユーザーアクセストークンは 4 時間で切れる）。

- Twitch の公式文書（getting-tokens-oauth）: public クライアントが使えるのは**デバイスコード方式だけ**。`POST id.twitch.tv/oauth2/device`（`client_id`、`scopes`）→ `verification_uri` と `user_code` を配信者本人が開いて許可 → `POST /oauth2/token`（`grant_type=urn:ietf:params:oauth:grant-type:device_code`、`device_code`、`client_id`、`scopes`）を `interval` ごとに（`authorization_pending` の間）。更新は `grant_type=refresh_token`（public は Secret なし）。更新トークンは**使い捨て**、30 日使わないと失効、アクセストークンは 4 時間
- `twitch_auth.py` `TwitchUserToken`（`TokenSource`: `get` / `refresh` / `maintain`）: 保存したトークン（`data/twitch_token.json` = `twitch.token_path`、権限 600、一時ファイルに書いて置き換え。Client-Id かスコープが違う保存は使わない）が期限の 10 分前まで有効ならそのまま、近いなら更新、更新トークンがない・400/401 ならデバイスコード方式（ログに URL とコードを WARNING で出し、許可まで待つ。コードが切れたら取り直す。`slow_down` は間隔を 5 秒延ばす）。一時的な失敗（5xx、通信）は更新トークンを捨てない。Secret があれば更新に付ける。呼び出しは `asyncio.Lock` で 1 つにまとめる。`TWITCH_EVENTSUB_TOKEN` があれば `StaticToken`（更新しない）
- `TwitchEventSub` は `TokenSource` を受ける: 呼び出しが 401 なら取り直して 1 回だけやり直し、接続している間は受信のたびに `maintain()`（1 時間ごとの validate、期限が近ければ更新）。許可されるまで待つのは EventSub のタスクだけで、配信とほかの応援は止まらない。`TwitchAuthError`（許可されなかった、Client-Id が違う）は ERROR を出して終わる
- 公式の表は `scopes`、例は `scope`: デバイスコードの許可後の要求には両方を送る（実機で確かめる）
- 許可を求め始めたとき 1 回だけ、`webbrowser.open` でブラウザを開き（`twitch.open_browser`、既定オン。開けなくても URL はログにある）、`say_notice` で「Twitch の許可が必要だよ」と声で知らせる（user: 「URLを求める時はブラウザのオープンまでできない？ログはすぐ流れるので気づかないこともありそう」）。コードが切れて取り直すときは出さない（ウィンドウが何度も出るのを避ける）。既定のブラウザが配信者本人でなければ別のアカウントで許可してしまう点は、ログとドキュメントで注意するだけ
- 「自動」の限界: 初回と、30 日以上つながなかったあとの許可は人が URL を開く必要がある（デバイスコード方式の定義）
- チャットへの書き込み（`TWITCH_ACCESS_TOKEN`、`chat:edit`）の更新は、この仕組みに乗せていない（IRC は認証失敗で匿名に戻る作りで、更新後につなぎ直す経路が要る）。次の課題
- テスト: `test_twitch_auth.py`（承認待ち → 保存、保存の再利用、更新で使い捨てトークンが入れ替わる、Secret の有無、死んだ更新トークンで許可からやり直し、一時的な失敗で更新トークンを残す、slow_down / 期限切れ、別 Client-Id・スコープ不足の保存、1 時間ごとの validate、同時呼び出し）
