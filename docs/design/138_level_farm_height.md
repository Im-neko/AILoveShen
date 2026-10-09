# 138. 畑の上の `level` は、畑の地面の高さでなければ断る

2026-10-09、user: 「これなんで畑の周り1段下げてるのかわかる？」→「直せるものがあれば直して」

## 1. 原因（`logs/bridge.log`、`data/state.json` の `leveling.done`）
- LLM が直接呼ぶ `level` の `where` の y は「立つ足の高さ」で、地面はその 1 つ下（`levelTarget`）。畑 2 の地面（耕地）は y=77、立つ高さは 78
- 06:25 `level({"radius":16,"where":"52,77,-455"})`（251 ブロックを掘り、209 を埋めた）、06:48 `level({"radius":8,"where":"53,77,-456"})`（82 ブロックを掘った）。どちらも地面の高さ 77 を渡したので、周りが地面 y=76 にそろえられ、耕地・作物・水は触らないため、畑だけが 1 段高く残った。畑の整地（`farmLevel`）は y+1 を正しく使う
- 畑の整地が周りを埋め戻し（06:45〜 `filled 48 / 1 / 2`）、LLM の `level` が掘り下げる、という取り合いもあった（遅さの一因と見ている。未計測）

## 2. 直したこと
`tools.mjs` `level`: `where` の中心が設計した畑の枠（四角 + `FARM_MARGIN` 2 マス）の中で、`y - 1` が畑の地面（`layout.y`）と違えば断る。文は「the farm X, whose ground is y=77: where's y is the height you STAND on (the ground is one lower), so use x,78,z. The farm levels itself with the candidate "level the ground of the farm X"」。畑の外は今までどおり、別の高さでもよい。`standard` / free に関わらず断る（間違いの被害が大きく、LLM に見分けがつかない）。

## 3. 直していないこと
- 実際の地形は未確認（スキルが動いている間は `inspect` が使えず、周りが何段下がっているかを見ていない）。すでに下げた土地は元に戻さない
- 畑の枠の外で畑の高さと違う `level`（畑のすぐ外の土地）は断らない

## 4. テスト
`test/leveling.test.mjs` の「level: 設計した畑の上で…」: 畑の中・周囲 2 マスで地面の高さの座標を渡すと断って正しい高さを言う、正しい高さと畑から離れた所と畑がない場合は通る。断りを外すと落ちることを確認した。
