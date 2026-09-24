# Spikes: プロトコルミラー + Jev による行動選択

Phase 6 の方式を決める前の検証コードです。本番コード（`src/ailoveshen/`）ではありません。
検証日: 2026-09-24。対象は Paper 1.21.4（`docker/docker-compose.minecraft.yml`）。

## 構成

| ファイル | 役割 |
|---|---|
| `minecraft-mirror/mirror.mjs` | プロトコルミラー。bot が受け取ったパケットを、偽サーバー（127.0.0.1:25578）につないだクライアントへ中継する |
| `minecraft-mirror/viewer-check.mjs` | ヘッドレス確認。nmp クライアントとしてミラーに接続し、受け取ったパケットを集計する |
| `minecraft-mirror/bridge.mjs` | bot + ミラー + HTTP API（`GET /observe`, `POST /act`）。今実行できる有界アクションを列挙し、実行する |
| `jev_eval.py` | Jev（typesafe-sdk）にアクションを選ばせる。シナリオ評価（`scenarios`）とクローズドループ（`loop`） |
| `results/*.json` | シナリオ評価の生データ |

## 実行

```bash
cd docker && docker compose -f docker-compose.minecraft.yml up -d && cd ..
cd spikes/minecraft-mirror && npm install && node bridge.mjs     # ミラーも同時に起動する
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
- **実クライアント（バニラ 1.21.4）で描画・HUD 表示ができるかは未確認**

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

### シナリオ評価（各3回。表の値は最頻の選択、一致回数、その選択肢の平均確率）

| シナリオ | 妥当な選択 | summary | raw | summary_generic | raw_generic |
|---|---|---|---|---|---|
| day_safe | collect_log/explore | collect_log 3/3 p=1.00 | collect_log 3/3 p=0.98 | collect_log 3/3 p=1.00 | collect_log 3/3 p=0.97 |
| drop_nearby | pickup_drop | pickup 3/3 p=0.98 | pickup 3/3 p=0.84 | pickup 3/3 p=0.97 | pickup 3/3 p=0.69 |
| hungry_safe（満腹度 0） | eat | eat 3/3 p=0.73 | eat 3/3 p=0.81 | eat 3/3 p=0.63 | eat 2/3 p=0.44 |
| night_zombie_armed | attack/equip | attack 3/3 p=0.77 | attack 3/3 p=0.64 | attack 3/3 p=0.82 | attack 3/3 p=0.67 |
| night_zombie_lowhp（体力 10） | flee | flee 3/3 p=0.87 | flee 3/3 p=0.64 | flee 3/3 p=0.65 | flee 2/3 p=0.42 |
| creeper_close（3m） | flee | flee 3/3 p=0.97 | flee 3/3 p=0.89 | flee 3/3 p=0.94 | flee 3/3 p=0.78 |
| unarmed_full_zombie | attack/flee | flee 3/3 p=0.80 | flee 3/3 p=0.63 | flee 3/3 p=0.61 | flee 3/3 p=0.50 |
| hungry_zombie_near | attack/flee | flee 3/3 p=0.63 | flee 3/3 p=0.56 | flee 3/3 p=0.56 | flee 3/3 p=0.56 |
| zombie_far_day（剣あり、14m） | attack/collect | attack 3/3 p=0.49 | **flee** 3/3 p=0.45 | attack 3/3 p=0.62 | attack 3/3 p=0.50 |
| night_zombie_natural（自然発生、約150体） | - | flee 3/3 p=0.91 | flee 3/3 p=0.85 | - | - |

- レイテンシは中央値 0.19〜0.24s で、状態の大きさにほとんど左右されない。例外は raw の 27.9k トークンのときで、0.44s だった
- 入力トークン: summary 540〜1,370、raw 2,300〜27,900
- 未検証: 低確信度のときの扱い（LLM へ上げる／規則で決める）、判断の揺れを抑える工夫

### クローズドループ（summary で observe → Jev → act）

- 昼、10ステップで 10/10 成功した。伐採 → 拾う → 板材のクラフトを自然に回した
  - 修正前は 8 ステップ中 2 回タイムアウトした。原因は、足元の原木を切った直後、空中にいるうちに次の原木を掘り始め、採掘速度が 1/5（約15秒）になっていたこと。着地を待ってから掘るように直した
- 夜、剣あり、AI 有効のゾンビ2体: 2体とも倒した。ただし体力は 20 から 11.3 まで減った。アクション実行中（約3.6秒）はもう1体に殴られっぱなしで、反射層がない
- あとの Jev のクエリの confidence は 0.11 だった。判断が本当に割れている状況を示していた

## 分かったこと / 設計への反映候補

1. 状態は要約して渡すのが正しい。raw でも大筋は同じ選択になるが、正解の確率は一貫して下がる。距離を計算させる必要があるケース（zombie_far_day）では、raw だけ判断が逆になった。トークンは 4〜20 倍になる
2. アクションは動的に列挙するのが効く。実行できないものは候補に出さないので、Jev が不可能な行動を選ぶことがない
3. Jev は説明文ではなく state を読んでいる。generic 説明で、同じ候補集合から、剣あり・満タン → attack、低体力 → flee と切り替えた
4. confidence は、はっきりした状況で 0.8〜0.99、割れる状況で 0.1〜0.5 だった。低確信度のときに LLM へ上げるか、規則で決めるかの閾値として使える
5. 実行中の危険は Jev の呼び出し間隔では防げない。反射層（physicsTick 上のコード）が必須
