# 設計書 48: アバターを画像生成 → Tripo → Blender（AI）で作り直す（調査）

状態: 調査のみ（2026-09-28）。実装・制作はまだ始めていない。

## 1. きっかけ

ユーザー（2026-09-28）:
- 「最近 neobanana とかでキャラクターの三面図を出して、それをもとに Tripo でパーツごとにメッシュを作成、Astra や Opus が Blender で調整等やっている記事をよく見ます。この方法を色々調べてみてください。AILoveShen プロジェクト用のモデルもこれで作り直したいです」
- デザイン画像: `models/assets/avatar_design.jpeg`
- 追加の参考（1 回目の調査のあと）: seagetch/reference-guided-rig-parts、@Dstudio_ai（Nano）、@manaimovie、@baku_lab の投稿。「結構いけそうに見えるので再検討してほしい」

## 2. 今のアバター（作り直すものが満たすこと）

`models/vrm/ailoveshen.vrm`（VRoid Studio、VRM 1.0、元データ `ailoveshen.vroid`）。設計書 24 の `/avatar`（three.js + three-vrm）が使っているもの:

| 種類 | 使っているもの |
|---|---|
| 表情 | `happy` `sad` `angry` `surprised` `relaxed`（重みを 0.3〜1.0 で変える。組み合わせもある） |
| 口 | `aa` `ih` `ou` `ee` `oh`（かなから並べる。`happy` と重ねる） |
| まばたき | `blink`（`happy` のとき弱める） |
| 目線 | `vrm.lookAt`（カメラとよそ見） |
| 骨 | hips, spine, chest, upperChest, neck, head, 左右の shoulder / upperArm / lowerArm / hand（息、揺れ、nod / cheer / flinch / tilt / wave） |
| 揺れもの | VRM のスプリングボーン（three-vrm が動かす） |
| 見え方 | 全身が既定、`?view=bust` で胸から上。OBS のブラウザソース（1920x1080、透明） |

作り直したモデルがこの表を満たさなければ、アバターの表現は今より下がる。これを合否の基準にする（§8）。

## 3. 手法の全体像

記事や投稿でよく見る流れ:

1. **設計図を作る**: 画像生成（Nano Banana = Gemini の画像モデル、または gpt-image）で、キャラの前・左・後・右の 4 面図を作る。最近の成功例は **頭（髪）・顔・体を別々に** 作る
2. **メッシュを作る**: Tripo の複数視点入力（前・左・後・右の 4 枚）で 3D にする。Smart Mesh（四角ポリゴン中心の、低ポリで流れの整ったメッシュ）とテクスチャも Tripo が作る。部位ごとに作ると、1 回の生成の面数の上限（2〜2.5 万）を部位ごとに使える
3. **仕上げる**: Blender で合体、顔の直し、骨とウェイト、表情、揺れもの、出力。ここを人がやる例と、AI（GPT-6 Astra、Claude Opus 5.5）にやらせる例がある

「Astra」は Google の Project Astra ではなく、OpenAI の **GPT-6 Astra**（2026-09-03 公開）。Tripo が「GPT-6 Astra 3D Character Workflow」として宣伝している。

## 4. 事例

### 4.1 人が Blender をやった例（表情がないか少ない）

| 出典 | 流れ | 結果 |
|---|---|---|
| [ユニスポット](https://www.uni-spot.com/blog_post/ai_3d_create2/)（2025-10、2026-07 更新） | FLUX → Nano Banana で T ポーズ化 → Tripo（約 3.2 万ポリゴン）→ Blender で骨・ウェイト・VRM は手作業 | 体は動いた。**表情はシェイプキーがなく動かない**。VRoid の顔には及ばないと結論 |
| [尾仲ゆる note](https://note.com/onaka_yuru/n/n03c123789b46)（2026-02） | Nano Banana で A ポーズ化 → Tripo v3.0 → Mixamo で自動リグ → VRM Add-on | 半日で動いた。**表情なし**、テクスチャが外れた。本格的には VRoid と結論 |
| [Nano（@Dstudio_ai）](https://x.com/dstudio_ai/status/2089209243207680484)（2026-08-17） | gpt-image-2 で顔の三面図と衣装を別に → Tripo で頭と体を別に（Smart Mesh、各 2 万ポリゴン）→ Blender（人）で合体・ウェイト・揺れもの・顔のテクスチャの修正・シェイプキー → VRM（ELYTH） | 約 6 時間でダンスまで。全身の動画は十分見られる。ただ **8 割が人の Blender 作業**、半分以上がウェイト塗り（自動ウェイトは足掛かりにしかならない）。顔のテクスチャと UV が荒く、寄りに耐えない。**髪はほぼ揺らせない**。表情はまばたき・ウィンク・喜ぶ・口開けの 4 つ。Tripo の Smart Mesh は高評価（Meshy はウェイトが塗れないほど） |

### 4.2 AI が Blender をやった例

| 出典 | 流れ | 結果 |
|---|---|---|
| [manaimovie](https://x.com/manaimovie/status/2104419496845037689)（2026-09-28） | GPT-Image 2.5 で頭・顔・体の 4 面図 → Tripo Smart Mesh 2.0 で 3 つ（テクスチャ 8K、頭は約 9 割が四角ポリゴン）→ **Opus 5.5** が Blender で合体・顔とテクスチャの直し・表情・骨・揺れもの・モーション。作者は Blender を触っていない | 動画（3 分 26 秒）の顔は元絵にかなり近い。途中で失敗して直している: 頭と髪の比率（目の間隔で合わせて頭を約 13% 小さく）、作者の「なんか微妙じゃない？まつ毛もひどいし」からの直し（あごを V 字に、埋まった前髪を引き出す、汚れ 10 か所）、スカートが板のように跳ねる、袖の金具が 24 mm 浮く。**表情は「絵の差し替え＋あごの動き」**（絵だけだと横から見て口が開かない）。Astra では目を閉じると別人になり、表情差分ができなかったとのこと |
| [SushiNFTart の記事](https://x.com/SushiNFTart/article/2100164342071316713)、[Tripo blog](https://www.tripo3d.ai/blog/gpt-6-astra-3d-character-workflow) | Tripo P2.0 で髪・頭・体を別に（各約 2.5 万ポリゴン）→ GPT-6 Astra × Blender（MCP）でまばたき・口パク・リグ・揺れもの | Tripo 側の宣伝と重なる。紹介記事（[runtimewire](https://runtimewire.com/article/tripo-p2-structured-meshes-astra-blender-rigging)）も、首・肘・膝のウェイトは人が確かめる、揺れものは別の手作業、と書いている |
| [baku_lab](https://x.com/baku_lab/status/2104522248153686188)（2026-09-28） | Blender × Astra × Tripo のパイプラインを作っている途中。**表情を整えた素体の頭**（喜怒哀楽、両目・片目のまばたき、口パクの 5 母音、視線移動の 26 のシェイプキー。VRM にする前提）を先に作り、キャラごとに AI が「作り直す」のではなく「調整する」 | 動画は素体の頭（トポロジーの流れが見える）と表情の一覧。キャラへの当てはめの結果はまだ出ていない。素体は販売予定の教材に入れるとのこと（メルマガの宣伝） |

ここから分かること:
- ボトルネックは **顔と表情**。どの例でもそこが一番時間がかかるか、できていない
- 解き方は 2 つ出てきた: (a) manai: Tripo の顔のまま、表情は絵の差し替え＋あご、(b) baku_lab: 表情を作り込んだ素体の頭を使い回し、キャラに合わせて調整する
- **リアルタイムで動く VRM に、表情・口・揺れものまで揃えた AI 仕上げの完成例はまだない**。manai の成果物は Blender で描いた動画で、作者は「まだ頑張ってくれてる」と書いている（途中）。Nano は VRM まで行ったが人の作業

### 4.3 当てはまらないもの: seagetch/reference-guided-rig-parts

[リポジトリ](https://github.com/seagetch/reference-guided-rig-parts)（最終コミット 2026-09-28）は **2D リギング用**（Live2D / nijilive 系）。一枚絵から部位ごとの透過パーツと PSD を作り、隠れた面を画像生成で補い、元の位置に重ねて元絵と比べる Codex のスキル。README に「深度生成、メッシュ・骨・表情・物理演算などのリグ設定は別工程」とある。今の 3D（three-vrm）には使えない。2D のアバターに切り替えるなら出発点になる（描画も作り直し。§7 の案 D）。

## 5. 道具

### 5.1 Tripo

- 入力: 1 枚、または前・左・後・右の 4 枚（3.1 Multi View）。別々に生成した横向きの画像は視点がずれていて、メッシュが歪む原因になる
- Smart Mesh（P1.0 / P2.0 / 2.0）: 四角ポリゴン中心。面数を 500〜2.5 万（四角）/ 5 万（三角）で指定できる
- パーツ分割（Segmentation v2 はプレビュー）、自動リグ、アニメ
- 出力: OBJ / FBX / GLB など。**VRM は出せない**
- API（1 クレジット = $0.01）: 画像・複数視点から 3D 20〜30、パーツ分割 40、パーツ補完 50、Smart Segmentation 55〜85、自動リグ 25、テクスチャ 10〜30（[料金](https://developers.tripo3d.ai/en/pricing)）
- サブスクと規約: 公式ページが取得できなかった（403）。無料プランで作ったものは権利が Tripo 側、商用利用は有料プランの特典という情報がある（[aipicks](https://aipicks.jp/mag/tripo-ai-guide-2026)）。**配信（特に収益化）に使うなら有料プランが前提。公式の規約で確かめる**

### 5.2 他の生成

| 候補 | 状況 |
|---|---|
| StdGEN（CVPR 2025、Apache-2.0） | 1 枚の絵から A ポーズに揃え、体・服・髪を分けて作る。アニメ向きで一番近い。リグと VRM はない |
| CharacterGen | アニメで学習、A ポーズ化。ライセンス未確認 |
| Hunyuan3D（公開は 2.1 まで）、Hunyuan3D-Part | 汎用 |
| Meshy | Nano の比較では低ポリが苦手で、ウェイトが塗れない |
| Neural4D AnimeArt など「VRM・52 シェイプキー込み」をうたうもの | 第三者の評価が見つからない |

### 5.3 Blender と AI

- この Mac には `/Applications/Blender.app`、`VRoidStudio.app`、`Unity Hub.app` がある。Blender はコマンドラインのパスにない
- Claude（Opus 5.5）は MCP なしでも、Blender を裏で動かして Python で操作し、描いた画像を見て直せる（manai の「作って、見て、直す」と同じ回し方）。MCP なら Blender Lab の公式サーバーか ahujasid/blender-mcp
- [VRM Add-on for Blender](https://vrm-addon-for-blender.info/)（Blender 5.x 対応）は Python から操作できる: 骨の自動割り当て、ARKit 名のシェイプキーからの表情割り当て、スプリングボーン、MToon
- 2026-05 の検証記事（[MindStudio](https://www.mindstudio.ai/blog/claude-blender-mcp-real-world-performance)、Opus 5.5 より前）は、キャラのウェイト・IK・顔は「現実的でない」としていた。manai の例（Opus 5.5）はこれを越えている

## 6. VRM にするときの問題

- **表情の作り方**:
  - manai の「絵の差し替え」は、VRM 1.0 の表情の `textureTransformBind`（テクスチャの位置・拡大をずらす。目と口の絵を 1 枚に並べて切り替える）で表せる。「あご」はシェイプキー（`morphTargetBind`）にする。three-vrm は両方を動かす。表情ごとの絵とあごを組み合わせれば、§2 の表情・口・まばたきを満たせるはず（未確認）
  - ただし `textureTransformBind` はマテリアル単位で効く。目と口を別々に切り替えるには、目・口・顔の残りのマテリアルを分ける。`happy` と `aa` を重ねる（今のページはする）と、同じマテリアルの位置が足し合わされて絵が崩れる。口の絵と表情の絵を別のマテリアルにするか、重ね方を決める
  - Tripo の「表情ごとの別メッシュを縮めて隠す」方式は、VRM では表せない（変換がいる）
  - baku_lab の素体の頭のように、シェイプキーで作り込んだ頭なら、そのまま VRM の表情になる
- **揺れもの**: Blender の物理ではなく、VRM のスプリングボーンとコライダーで作り直す。髪は Tripo のメッシュだと一つの塊で、骨を通しにくい（Nano: 「ほぼ揺らせない」）
- **見た目**: Tripo のテクスチャは陰影が焼き込まれた写実寄り。MToon（アニメ調のシェーダー）にすると色味が変わる。8K はブラウザと OBS に重いので縮める
- **目線**: `lookAt` は目の骨か、テクスチャのずらしで作る
- **ライセンス**: VRM のメタ情報（今は `personalNonProfit`）を、Tripo のプランと合わせて決め直す

## 7. このプロジェクトの選択肢

| 案 | 中身 | 良い点 | 危ない点 |
|---|---|---|---|
| A. VRoid で作り直す | 新しいデザインを VRoid Studio で再現する（水色のロングヘア、デニムジャケット、パーカー、プリーツスカート、ニーハイ、ブーツはどれも作りやすい） | 表情・口・揺れものがそのまま。一番確実 | 見た目は VRoid の絵柄の範囲。独自性は低い |
| B. 顔は VRoid、服・体は AI | VRoid の頭（表情のシェイプキー付き）を素体として使い、髪・体・服・小物を Tripo で作って合体する。baku_lab の「素体の頭を使い回す」を、手元にある VRoid の頭でやる形 | 表情の心配がほぼない。服の作り込みは AI の方が上 | 首のつなぎ目、VRoid の顔と Tripo の体の絵柄の違い。髪を Tripo にすると揺れにくい |
| C. manai 方式（全部 Tripo、Opus 5.5 が仕上げ） | 頭・顔・体の 4 面図 → Tripo → Claude が Blender で合体、表情は絵の差し替え＋あご、VRM の骨と揺れもの | デザインに一番忠実。人の手がほぼ要らない（manai の例では） | リアルタイムの VRM の完成例がまだない。表情の重ね方（§6）、髪の揺れ、テクスチャの色味 |
| D. 2D に切り替える | seagetch のスキルの流れで PSD パーツ → Live2D / nijilive / Inochi2D | 元絵の絵柄がそのまま | アバターの描画（設計書 24）を作り直す。2D のリグ（メッシュ・変形・物理）は別工程 |

見立て（2026-09-28）:
- 1 回目の調査では「作り直しても良くならない」とした。manai と baku_lab の投稿で、顔と表情の解き方が見えたので、**C（だめなら B に戻る）を試す価値はある**に改める
- ただし「AI で全自動」はまだ宣伝と途中経過が中心。試作で合否を確かめてから進め、合わなければ今の VRoid モデルを使い続ける

## 8. 進めるなら（試作）

合否（§2 から）: `/avatar?demo=1` で話す口 5 つ、表情 5 つ（`happy` と口の重ね）、まばたき、目線、cheer / wave などのしぐさで破綻しない。全身と `?view=bust` の寄りに耐える。OBS で重すぎない。

段階:
1. Blender をコマンドラインから使えるようにし、VRM Add-on を入れる
2. 設計図: `avatar_design.jpeg` から、頭（髪）・顔・体の 4 面図（前・左・後・右）を作り直す（今の画像は横がなく、上・下がある。上・下は Tripo では使わない）
3. Tripo（Smart Mesh）で 3 つ作る
4. Blender（Claude）: 合体 → VRM の骨 → ウェイト → 表情（絵の差し替え＋あご、または VRoid の頭を移植）→ スプリングボーン → MToon → VRM 1.0 で出力
5. `/avatar?demo=1` をヘッドレスの Chromium で撮って合否を見る。通れば `avatar.model_path` を差し替える

## 9. 決めること（ユーザー）

- Tripo の生成: ユーザーが Web で操作してファイルを渡すか、API キーで Claude が回すか。プラン（商用利用）
- 4 面図の生成: Gemini の画像生成 API で Claude が作るか、ユーザーが Nano Banana で作るか。良し悪しの判断はユーザー
- どこまでを合格にするか: 胸から上の寄りと表情・口を優先し、全身の揺れものは後回しにしてよいか
- 案 A〜D のどれで行くか

## 10. 確かめていないこと

- Tripo の公式の料金・規約（403 で取れなかった）
- manai の成果物が VRM として出せるか、three-vrm で同じ表情が出るか
- `textureTransformBind` の表情と口の重ね方が three-vrm でどう見えるか
- baku_lab の素体（未公開）
- CharacterGen のライセンス
