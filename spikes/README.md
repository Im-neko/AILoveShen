# Spikes: プロトコルミラー + Jev による行動選択

Phase 6 の方式を決める前の検証コードです。本番コード（`src/ailoveshen/`）ではありません。
検証日: 2026-09-24。対象は Paper 1.21.4（`docker/docker-compose.minecraft.yml`）。

## 構成

`minecraft-mirror/` は Phase 6 最小版で `minecraft-bridge/`（本番のサイドカー）に移しました。以下のパスは spike 当時のものです。

| ファイル | 役割 |
|---|---|
| `minecraft-mirror/mirror.mjs` | プロトコルミラー。bot が受け取ったパケットを、偽サーバー（127.0.0.1:25578）につないだクライアントへ中継する。**現在は `minecraft-bridge/src/mirror.mjs`** |
| `minecraft-mirror/viewer-check.mjs` | ヘッドレス確認。nmp クライアントとしてミラーに接続し、受け取ったパケットを集計する。**現在は `minecraft-bridge/tools/viewer-check.mjs`** |
| `minecraft-mirror/bridge.mjs` | bot + ミラー + HTTP API（`GET /observe`, `POST /act`）。今実行できる有界アクションを列挙し、実行する。**Phase 6 最小版で `minecraft-bridge/src/` に作り直した**（spike 版はコミット `c0df529` にある） |
| `jev_eval.py` | Jev（typesafe-sdk）にアクションを選ばせる。シナリオ評価（`scenarios`）とクローズドループ（`loop`） |
| `results/*.json` | シナリオ評価の生データ |

## 実行

```bash
cd docker && docker compose -f docker-compose.minecraft.yml up -d && cd ..
cd minecraft-bridge && npm install && npm start     # ミラーも同時に起動する（spike 当時は spikes/minecraft-mirror/bridge.mjs）
# 実クライアント: Java Edition 1.21.4 で 127.0.0.1:25578 にダイレクト接続

python -m venv spikes/.venv-jev && spikes/.venv-jev/bin/pip install typesafe-sdk==0.7.1
# シナリオ評価の前に RCON で gamerule doMobSpawning false / doDaylightCycle false にしておく
TYPESAFE_API_KEY="$(pass show JEV_API_KEY)" spikes/.venv-jev/bin/python spikes/jev_eval.py scenarios --repeat 3
TYPESAFE_API_KEY="$(pass show JEV_API_KEY)" spikes/.venv-jev/bin/python spikes/jev_eval.py loop --steps 10
```

## Spike 1: プロトコルミラー

方式（rmalde/minecraft-agent のアイデアを 1.21.4 向けに作り直したもの。ライセンスがないのでコードは流用していない）:

- bot のセッションで受け取った configuration パケット（`select_known_packs`、`registry_data`、`tags`、`feature_flags`）を記録し、ビューアーにそのまま流し直す
  - nmp サーバーは `login_acknowledged` を受けるとすぐに `finish_configuration` を送る。そのため `prependOnceListener` で先に割り込んで流す。`registryCodec: {}` を渡して、nmp が自前の `registry_data` を送らないようにする
- play パケットは生バイト（`fullBuffer`）のまま `writeRaw` で中継する。後から接続したビューアーには、記録しておいたチャンク・光・ブロック変更・エンティティ・状態パケットを流し直す
- ビューアーは bot と同じエンティティ ID を持つ。50ms ごとに `position` を送って、bot の位置と向きに固定する。ビューアーからの入力は捨てる
- サーバーは自分自身の腕振りや採掘の進み具合を送ってこないので、`animation` と `block_break_animation` を合成して送る

**結果**:
- ヘッドレス（nmp クライアント）: configuration → play まで到達した。8秒で `map_chunk` 329件、`update_health`、`position` 153件（移動と視点回転を反映）を受け取り、パースエラーはなかった
- 実クライアント（バニラ 1.21.4）でも描画と HUD 表示を確認した（下記）

### 実クライアントでの確認（2026-09-24）

- バニラ 1.21.4（Microsoft アカウントでログイン）で `127.0.0.1:25578` に接続できた。弾かれずに bot の一人称視点が描画された
- スクリーンショットで確認できたこと
  - 体力、満腹度、ホットバー（原木の個数が 2 → 3 と増える）
  - 手に持った原木
  - 見上げて幹を切る視点
- フォーカスが外れるとポーズメニューが出る。クライアント側の設定 `pauseOnLostFocus` によるもので、F3+P で切れる
- 未確認
  - 視点のカクつき。静止画では判断できないので、目視での評価が必要
  - 採掘のひび割れの見え方
- このループで見つかった問題
  - 昼間なのに `flee_hostile` が2回選ばれ、2回とも失敗した（経路探索のタイムアウト）
  - 原因の候補1: mineflayer は enderman（中立）を hostile に分類する
  - 原因の候補2: 洞窟内にいて届かない敵も、高低差 6m 未満なら候補に入る
  - 原因の候補3: 逃げる先の座標が地形上たどり着けない
  - どれが原因かはまだ特定していない

## Spike 2: Jev による行動選択

- API: `typesafe-sdk` 0.7.1 の `AsyncTypeSafeClient.system_one(state, questions={"action": Choice(criteria={id: 説明})})`。モデルは `jev-latest` = `jev-1.13.0`
- 有界アクション（今の状況で実行できるものだけを動的に列挙する）: `attack_hostile` / `flee_hostile`（16m 以内に敵対モブがいるとき）、`equip_weapon`、`eat`（食料があり、満腹度が 20 未満のとき）、`pickup_drop`、`collect_log`、`craft_planks`、`explore`、`idle`
- 状態は2種類を比べた
  - **summary**: 時間帯、自分（体力、満腹度、位置、手持ち）、インベントリの個数、48m 以内のモブ上位16体（距離・方角・高低差）、24m 以内のドロップ、最寄りの原木、直近5アクションの結果
  - **raw**: Mineflayer の値をほぼそのまま出したもの（全エンティティ、スロット、7×5×7 のブロック）
- 説明文の条件も2種類を比べた
  - 距離などの手がかりを含む説明
  - 手がかりのない generic な説明
  - generic のときは説明文から答えが分からないので、state を読んでいるかどうかを確かめられる

### シナリオ評価

- 1シナリオにつき観測した state は1つ。同じ state で3回ずつ問い合わせたが、確率の揺れは ±0.02 程度でほぼ決定的だった。つまり「3/3」は3サンプルではなく、**state 10 個の評価**にすぎない
- 表の値は最頻の選択、一致回数、その選択肢の平均確率
- 前のシナリオのモブを kill したときのドロップが次のシナリオに残っていたので、RESET を直して該当シナリオを取り直した

| シナリオ | 妥当な選択 | summary | raw | summary_generic | raw_generic |
|---|---|---|---|---|---|
| day_safe | collect_log/explore | collect_log p=1.00 | collect_log p=0.98 | collect_log p=1.00 | collect_log p=0.97 |
| drop_nearby | pickup_drop | pickup p=0.98 | pickup p=0.84 | pickup p=0.97 | pickup p=0.69 |
| hungry_safe（満腹度 0） | eat | eat p=0.83 | eat p=0.96 | eat p=0.81 | eat p=0.75 |
| night_zombie_armed | attack/equip | attack p=0.77 | attack p=0.64 | attack p=0.82 | attack p=0.67 |
| night_zombie_lowhp（体力 10） | flee | flee p=0.89 | flee p=0.82 | flee p=0.70 | flee p=0.67 |
| creeper_close（3m） | flee | flee p=0.97 | flee p=0.89 | flee p=0.94 | flee p=0.78 |
| unarmed_full_zombie | attack/flee | flee p=0.84 | flee p=0.82 | flee p=0.69 | flee p=0.70 |
| hungry_zombie_near | attack/flee | flee p=0.63 | flee p=0.56 | flee p=0.56 | flee p=0.56 |
| zombie_far_day（剣あり、14m） | attack/collect | flee p=0.50 | flee 2/3 p=0.49 | attack p=0.53 | attack p=0.67 |
| night_zombie_natural（自然発生、約150体） | - | flee p=0.91 | flee p=0.85 | - | - |

- レイテンシは中央値 0.19〜0.26s。raw が 27.9k トークンのときだけ 0.44s
- 入力トークンは summary 540〜1,370、raw 2,300〜27,900
- zombie_far_day は、汚染ありの初回では「raw だけ flee」に見えたが、取り直すと違った
  - 距離入りの説明（"...14m away"）では、state の形式に関係なく flee（p≈0.5）
  - generic な説明では attack
  - つまり判断を動かしていたのは、state の形式ではなく説明文の書き方だった
- 未検証: 低確信度のときの扱い（LLM へ上げる／規則で決める）、判断の揺れを抑える工夫

### クローズドループ（summary で observe → Jev → act）

- 昼、10ステップで 10/10 成功した。伐採 → 拾う → 板材のクラフトを自然に回した
  - 修正前は 8 ステップ中 2 回タイムアウトした。原因は、足元の原木を切った直後、空中にいるうちに次の原木を掘り始め、採掘速度が 1/5（約15秒）になっていたこと。着地を待ってから掘るように直した
- 夜、剣あり、AI 有効のゾンビ2体: 2体とも倒した（実行結果の "killed" は、エンティティが消えたことからの推定）。ただし体力は 20 から 11.3 まで減った。アクション実行中（約3.6秒）はもう1体に殴られっぱなしで、反射層がない
- あとの Jev のクエリの confidence は 0.11 だった。判断が本当に割れている状況を示していた

## 分かったこと / 設計への反映候補

1. **状態は要約して渡す**
   - 要約と raw で選択が変わったケースはない
   - ただし正解の選択肢の確率は、要約のほうがほぼ一貫して高い（例外は hungry_safe）
   - raw はトークンが 4〜20 倍になり、状態が大きいとレイテンシも増える
2. **アクションは動的に列挙するのが効く**
   - 実行できないものは候補に出さないので、Jev が不可能な行動を選ぶことがない
3. **Jev は説明文だけでなく state を読んでいる**
   - 同じ候補集合と同じ generic 説明のまま、剣あり・満タン → attack（0.82）、低体力 → flee（0.70）と切り替えた
4. **説明文も判断材料として効く**
   - 距離を入れると慎重側に倒れた（zombie_far_day）
   - 候補の説明に何を書くかはプロンプト設計の一部として扱う必要があり、状況の数値は state 側だけに置くほうが一貫する
5. **confidence は割れ具合をよく反映している**
   - はっきりした状況では 0.8〜0.99、割れる状況では 0.1〜0.5 だった
   - LLM へ上げるかどうかの閾値に使える
6. **実行中の危険は、Jev の呼び出し間隔では防げない**
   - 反射層（physicsTick 上のコード）が必須
7. **サンプルが小さい**
   - state は10個、同じ state への再問い合わせではほぼ同じ答えが返る
   - 方式の筋の良さは確認できたが、精度を数値で主張できる規模ではない
