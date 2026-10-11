# 170. サーバーとの接続が切れたら、ブリッジは状態を保存して終わる

2026-10-12、user: 「足していいです」（ブリッジに切断の処理を足す）。きっかけは 2026-10-11 の凍結。

## 1. 事実
- 2026-10-11 13:59Z、Minecraft サーバー（Paper 1.21.4、Apple Silicon の Docker）の JVM が SIGSEGV で落ちた。スタックは Paper 同梱の spark のバックグラウンドプロファイラ（`libasyncProfiler.so`、`vframeStreamForte::forte_next`）。Docker の `restart: unless-stopped` で約 10 秒後に別のサーバーが起動した
- ブリッジは切断に気づかなかった。`index.mjs` には `bot.on('end')` がなく、`kicked` と `error` を表示するだけだった。ボットは接続が切れたまま、HTTP の API だけが応答し続けた。位置が動かず、全部の手が `Timeout waiting for 20 ticks` で失敗した。約 55 分、誰も気づかなかった（Python 側の「ブリッジにつながらない」も出ない）
- 道具の数が減ったことと API が応答することだけを確かめて、「ボットが動いたか」を確かめなかった（再起動後の確認の抜け）

## 2. 変更
- `bot.on('end')`: 自分で止めたとき（`stopping`）以外は、理由をログに出し、状態と世界の地図を保存して、終了コード 1 で終わる。ブリッジが終われば、Python は「ブリッジにつながらない」と待ち（`docs/streaming.md`）、ログにも `[bridge] サーバーとの接続が切れた` が残る
- サーバー側の原因は、`plugins/spark/config.json` の `backgroundProfiler` を `false` にした（`docs/streaming.md` のうまくいかないとき）。ボリュームの中の設定で、compose には入れていない

## 3. 作っていないもの
- 再接続: ボット（`const bot`）をほとんどのモジュールが直接持つので、つなぎ直しはプロセスごとやり直すのが確実。外側（zmx のシェルなど）で `until npm start; do sleep 3; done` と包むと自動で戻るが、サーバーがまだ起動中のとき入れ替わりで落ち続ける。user の判断
- 実機の確認: サーバーを落としてブリッジが終わるか、は未確認（ブリッジの再起動が要る）
