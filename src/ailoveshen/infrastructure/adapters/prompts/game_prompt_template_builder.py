"""テンプレートでゲームのプロンプトを組み立てるアダプター。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from string import Template
from typing import Any, Optional

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.text_generator import CACHE_BREAK
from ailoveshen.application.use_cases.goal_vocabulary import MAX_TOWN_STAGES
from ailoveshen.application.use_cases.tool_catalog import ASK_ADVISOR
from ailoveshen.application.use_cases.town_map import removable_facilities
from ailoveshen.domain.value_objects import (
    Activity,
    BuildDesign,
    Candidate,
    CharacterProfile,
    ConditionStatus,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalPredicate,
    HouseBlueprint,
    Mission,
    Objective,
    SameCause,
    SkillInfo,
    ToolOutcome,
    TownDefinition,
    TownMap,
    TownSite,
    TownStage,
    Zone,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import (
    ABILITIES,
    format_activity,
    format_conditions,
    format_messages,
    format_predicates,
    format_site_choice,
    format_site_facts,
    format_time_en,
)

HOUSE_DESIGN_TEMPLATE = Template("""\
あなたは「$name」というAI配信者です。性格: $personality_traits
これから Minecraft のサバイバルで、自分の家を建てます。建てる家を設計してください。
$site_note
## 建てられる家の条件
- 1部屋の四角い家。壁と平らな屋根は木の板材でできる
- 幅（x方向）と奥行き（z方向）は $min_side〜$max_side ブロック
- 壁の高さは $min_height〜$max_height ブロック
- ドアは1つ。door_side の壁の door_offset の位置に置く
  （offset は壁の端から数えた位置で、角は選べない）
- corner_pillars を true にすると四隅の柱が原木になる
  （見た目のアクセント。原木が4本×壁の高さぶん余分に必要）
- 材料はすべて自分で木を切って集める。大きい家ほど時間がかかる

$provisional_note
## ヒント
- 配信で完成まで見せられる大きさが良い
- あなたらしさが伝わる名前とコンセプトにする。名前はシェン（LoL）の世界観で付けて、視聴者に笑ってもらう
  （均衡の結社キンコウ、アイオニア、黄昏、気、影など。何の家か分かる言葉は残す）
$previous_error
## 出力
指定された JSON だけを出力してください。
""")

# 最初の家は仮の小屋（設計書 109）: 大きさはコードで決まっていて、決めるのは名前・コンセプト・ドア
PROVISIONAL_NOTE = """
## 仮の小屋
これは最初の夜を越すための、仮の小さな小屋です。大きさは決まっています:
幅 5、奥行き 5、壁の高さ 2、corner_pillars は false（これ以外にはできません）。柱も飾りもありません。
決めるのは名前、コンセプト、ドアの向き（door_side）と位置（door_offset は 1〜3）、壁と屋根の材料
（wall_material）です。dirt は素手で掘れていちばん早く建つ（斧も原木も要らない。ドアだけ板材 6 枚）。
planks は原木が約 14 本要る。急ぐので、特別な理由がなければ dirt にします。
街の場所が決まったら、本家をあとで別に建てます。
"""

BUILD_DESIGN_TEMPLATE = Template("""\
あなたは「$name」というAI配信者です。性格: $personality_traits
Minecraft のサバイバルで、建物「$build_name」を設計します。

## 頼まれたこと
$brief

## 今の家
$home_note
$builds_note
## 書き方（形を並べる。コードがブロックに直して、下から順に建てる）
- 座標は建物の原点（最小の角）からの相対。x は東、z は南、y は上。すべて 0 以上
- y=0 は床の層（地面・家の床と同じ高さ）。人が立つのは y=1 から
- 形（from と to は直方体の両端。両端を含む）:
  - fill: 埋める（床、壁、柱、屋根）
  - hollow_box: 外殻だけ（床・壁・屋根の 6 面）。中は触らないので、部屋の中は clear で空ける。
    底の面も固い: 床を y=0 に作って y=1 から箱を置くと、y=1 が板で埋まって立てない。部屋の中は y=1 から空ける
  - clear: 空気にする（入口、窓、部屋の中）
  - door: ドア（from が下のマス。上のマスも自動でドアになる）。壁の中に置く。ドアの内側の 2 マス（足と頭）は空ける
- 後の形が前の形を上書きする（hollow_box のあとに clear で入口や窓を開ける）
- 材料: planks（木の板材）、log（原木）、cobblestone（丸石）、dirt（土）。すべて自分で集める
- 大きさ: x と z は $max_side 未満、y は $max_height 未満。展開したブロックは $max_blocks 個まで
  （空けるマスも数える。中まで埋めずに外殻にすると少なくて済む）
- 形は $max_shapes 個まで
- 足場は作れない。高い壁や広い屋根は、下と外周から順に積める形にする（高さ 8 くらいまで）
- 大きな建物は時間がかかる（1 ブロックに 1〜2 ステップと材料集め）。配信で見せ場になる形にする

## 置き場所（anchor）。座標は書かない
- home:east / home:west / home:north / home:south: 家の増築。建物の家側の面が家の外壁に重なる
  （east なら x=0 の面、west なら x が最大の面、south なら z=0、north なら z が最大の面）。
  建物は壁に沿って家の中央にそろう。床の高さは家の床と同じ
  - 家とつなぐには、その重なる面に clear で入口（幅 1、y=1〜2）を開ける。ドアの位置は避ける
  - 家の室内や床は変えられない。家のドア・ベッド・チェストには掛けられない
- near_home: 家の近くの平らな空き地（コードが探す）。独立した建物（倉庫、塔、小屋）
$map_anchor$previous_error
## 出力
指定された JSON だけを出力してください。
""")

CHECK_TEMPLATE = Template("""\
Minecraft のサバイバル（1.21.4）で、大目標の完了を世界から判定するコードを書きます。
判定の名前: $check_name

## 大目標（頼まれたこと）
$brief

## 今の場所
$place_note

## 書き方
JavaScript で `function check(w) { ... }` を 1 つ書く。世界を読んで
`{ met: true/false, remaining: 数, lines: [文] }` を返す。
- met: 頼まれたことが世界で成り立っているとき true。見えない所・読めない所は成り立っていないとする
- remaining: 残りの作業の量（0 以上の数。作業が進むほど減る。met が false なら 1 以上）。
  例: 花を 4 本 → 4 - 見つけた数
- lines: 今の状態を英語で 1〜3 文（例: "poppies near the door: 2/4"）。配信者が次に何をするか決めるのに読む
- 同期で動く（async / await / Promise は使えない）。50 ms、ブロックを読むのは 20000 回まで。
  require・fetch・時計はない
- 使えるのは w だけ（読むだけ。世界は変えられない）:
  - w.block(x, y, z) → { name, solid, props? } か null（読み込まれていない）。name は "oak_log" など。
    props はブロックの状態で値は文字列（耕地の moisture "0"〜"7": "7" が湿っている、作物の age、
    ドアの open など）
  - w.find(name, { near?: [x, y, z], max_distance?: 32（64 まで）, count?: 64（256 まで） })
    → [[x, y, z], ...] 近い順。name は 1 つのブロック名か "*_suffix"（"*_bed" はすべてのベッド）
  - w.inventory() → { item: 数 }（持ち物）
  - w.me() → { x, y, z }（足の位置）
  - w.home() → { door, inside, min, max }（各 { x, y, z }。min と max は家の外壁の両端の角）か null
  - w.chests() → { item: 数 }（家のチェストの中身。最後に開けたときの記憶）
  - w.mobs(name?, radius?) → [{ name, x, y, z, hostile }]（半径 64 まで。"sheep" など。空ならすべて）
  - w.time() → { time_of_day, day }
- 場所は家（w.home()）か、今の場所の座標を基準にする。ずっと同じ場所を見る（呼ぶたびに
  w.me() から測ると、歩くたびに判定が変わる）ので、今の場所なら座標をコードに数で書く
- 頼まれたことが今もう成り立っているような判定は受け取られない（まだしていないことを確かめる）
- 組み込みの条件（have, stored, placed, built, lit, planted, farmed, leveled）で書けることは
  ここには来ない。形・並び・場所・数の組み合わせを確かめる
$previous_error
## 出力
指定された JSON だけを出力してください（description: 何を確かめるかの英語 1 文、code: コード）。
""")

MAP_ANCHOR_NOTE = """\
- map: 添えた地図の画像のマス目を cell に書く（例: C7）。建物はそのマスのまわりの平らな所に建つ
  - 地図は真上から見たもので、北が上、東が右。1 マスは 4x4 ブロック。家は赤い枠、ほかの建物は橙の枠
  - 色: 緑 草地・土、灰 石、薄い黄 砂、青 水、赤橙 溶岩、濃い緑 葉、茶 木の幹、薄茶 作ったもの、
    黒 まだ見ていない所。明るいほど高く、暗いほど低い（家の床と同じ高さが基準）
  - 家からの見え方（街並み、道、眺め）を考えて選ぶ。水・溶岩・木・黒い所は避ける
"""

# 配信の名前の約束（街・家・建物・施設の名前。設計書 160）
NAMING_THEME = """
## 名前の付け方（配信の約束）
この配信は LoL（League of Legends）のシェン（均衡の結社キンコウの「黄昏の目」）にちなんでいる。
名前は、シェンの世界観で付けて、視聴者に笑ってもらう:
- 使う言葉: 均衡の結社キンコウ、アイオニア、黄昏、気（Ki）、影、精神の刃、結束、均衡、忍者、守り手、など
- 何の建物か分かる言葉は残す（工房・畑・倉庫・塔・拠点など）。例: 「キンコウの修練場」「黄昏の畑」「均衡の工房」
- 日本語で 2〜12 文字ぐらい（長くても 24 文字まで）。ほかの名前とかぶらせない
"""

NAMING_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者です。次の建物・施設に、配信で呼ぶ名前を付けてください。
元の名前（name）は判定に使う識別子なので変わりません。付けるのは呼び名（title）だけです。
$theme
## 名前を付けるもの
$things

## もう使っている名前（かぶらせない）
$taken

## 出力
指定された JSON だけを出力してください。上の全部に 1 つずつ title を付けること。
""")

SITE_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者「$name」（性格: $personality_traits）です。
配信の最終目標は「$mission」です。街を作る場所を決めるため、最初の家のまわりの候補地を
見て回り、地形を数えました。この中から街の場所を 1 か所選び、街の名前を付けてください。
場所は一度決めたら変えません。最初の家の場所以外を選ぶと、そこに家を建てて引っ越します
（最初の家は残ります）。

## 調べた候補地（数えた数字。候補地のまわり半径 32 ブロック）
$sites

## 数字の意味
- 家を建てられる平らな区画: 重ならない 9x9 の区画のうち、乾いた地面で高低差が 1 以内のもの。
  街には建物を何軒も置くので多いほど良い
- 水・急な段差: 地面のうちの割合。多いと建てられる所が減る（水は景色や将来の畑には良い）
- 地表の石・石炭・鉄: 空気に面していて、掘りに行けるもの。今は地面の下は掘り進めないので、
  石がないと石の道具・かまど・鉄の道具が作れない
- 原木: 木材（家、道具、松明の木炭）。動物: 食料と羊毛（ベッド）
- 溶岩: 危ない。家からの距離: 遠いほど引っ越しと行き来に時間がかかる
- 読み込めた範囲: 数えられた地面の割合（低いと数字が少なめに出ている）

## 選び方
- 数字で選ぶ。見た目の想像（「きれいそう」など）で選ばない
- 理由は配信でそのまま話す。どの数字が決め手かを 1〜2 文で言う
$theme
## 街の名前
街の名前（town_name）も付けること。
$previous_error
## 出力
指定の JSON で出力してください。
""")

TOWN_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者「$name」の方針を決めます。
配信の最終目標は「$mission」です。この「街」とは何か、いつ完成したと言えるかを定義してください。
定義は一度決めたら変えず、配信の最後まで、この段階を順に進めます。

## 街の場所
$site

## 今の状況
- 家（木の家）が 1 軒ある。街の場所が最初の家の場所でなければ、そこに家を建てて引っ越す
  （段階はその家から始まる）。サバイバルで、夜は敵が湧く
- 視聴者はコメントで話しかけてくる。配信で完成まで見せられる規模にする

## 今できること
$abilities

## 今ゲームの状態から判定できる条件
$conditions

## 定義の決まり
- 段階（先に作るもの → 後で作るもの）に分ける。$max_stages 段階まで。前の段階が後の準備になるように
- 段階ごとの完了条件（conditions）は、上の判定できる条件だけで書く。1 段階 3 つまで
- 上の条件で書けないもの（2 軒目の建物、柵、道など）は unresolved に文で書き、何ができれば
  判定・実行できるかを添える。判定できるふりをしない（今の built() は最初の家のこと）
- 「にぎやか」「きれい」のような判定できない言葉は、数や配置に言い換える
- 手に入らない物（今できることで作れない物）を条件にしない
- 場所の数字に合わせる（例: 石が多いなら石を使う、動物が多いなら食料の備蓄から）
$previous_error
## 出力
指定の JSON で出力してください。
""")

# 街の理想図（docs/design/47 §6）: 区画ごとの用途。ローカルの LLM が考えて描く（town_plan）
TOWN_MAP_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者の、街の理想図（どの区画に何を作るか）を描きます。
理想図は街のマスターデータで、畑・植林場・採掘場・建物を作るときは、ここで決めた区画に作ります。

## 街
$text
段階:
$stages

## 区画
街の中心から 12 x 12 区画、1 区画は 8 ブロック四方（全体 96 x 96）。列 A〜L が西→東、行 1〜12 が北→南。
地図（1 文字 = 1 区画）:
$grid
凡例: H 家、B 建物、F 畑、W 植林場、M 採掘場、P 囲い、X トラップ（今あるもの。固定）、= 道（固定）、~ 水、
! 溶岩（使えない。固定）、. 平らな空き、^ でこぼこ、T 木が多い、? まだ見ていない

## 固定の区画（変えない。areas に入れない）
$fixed
$removable
## 空き区画の地形（平ら = 高さがそろった列の割合、木・石・水 = 割合、高さ = 家の床からの差）
$terrain
$current
## 描く理由
$reason

## 決まり
- 街の定義と段階に要るもの（住まい、畑、植林場、採掘場、倉庫、作業場、広場、道など）の場所を
  先に決めておく。後の段階で作る物にも場所を取っておく
- 効率を先に: 毎日使う施設（畑、倉庫、作業場）は家の近く。畑は平らな所、植林場は平らか木の多い所、
  採掘場は石の多い所。行き来が短くなるように並べる
- 1 つの施設は隣り合う区画にまとめる（畑・植林場は 1〜4 区画）。施設ごとに名前（facility）を付ける
- 道は先にコードが決めてある（固定。家から東西南北に延びる大通りと、施設への枝）。新しい施設は道に面した
  区画に置く（採掘場だけは道から離れてよい）。道に面さない用途は差し戻される。道そのものは書かない
- 固定の区画は変えない。まだ見ていない区画にも割り当ててよい
$clear_rule$previous_error
## 出力
指定の JSON で出力してください（concept、areas）。
""")

# 施設の設計（docs/design/47 §7、§16）: 畑・植林場・採掘場の区画・大きさ・作物か苗木・深さ（facility_design）
FACILITY_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者の、施設「$name」を設計します。
施設は作る前に場所と大きさを決めます。実際に耕す・植える・掘るマスは、決めた区画の中からコードが選びます。

## 何のための施設か
$brief
$where
## 種類
- farm（畑）: crop（wheat / carrots / potatoes / beetroots）と大きさ width x depth（各 1〜16）。
  四角の中の 9 x 9 ごとに 1 マスは水のマスになり（水のバケツを置く。水が届いた耕地だけが湿って、
  何も植わっていなくても土に戻らず、作物が速く育つ）、残りを耕して種をまくと完成。作物のマスは
  幅 x 奥行き − 水のマス（9 x 9 なら 80 マス、2 x 1 なら 1 マス）。区画の平らな地面に収まる大きさにする（1 区画は 8 x 8）。
  畑は、地面の高さをそろえ（整地）、四角の 1 マス外側を柵で囲い、柵のゲートを 1 つ付けるまでが完成。
  外側の輪が家・建物・ほかの施設にかかる場所は断られる
- tree_farm（植林場）: sapling（苗木の種類。何でもよければ sapling）と本数 count（1〜32）、
  苗木どうしの間 spacing（4〜6。木が育つ間隔）。全部のマスに植えると完成。1 区画に 4 本ほど
- mine（採掘場）: 底の高さ bottom_y と、底の部屋から横へ延びる坑道の本数 tunnels（0〜4）と長さ
  tunnel_length（4〜32）。4 x 4 の地表から、らせん階段で bottom_y まで下りる（1 区画で足りる）。
  全部掘ると完成。鉄は y 16 あたり、ダイヤは y 0 より下、石炭・銅は浅い所に多い。溶岩や水に
  当たる深さは断られる（届く一番深い y が返る）

## 決まり
- 効率を先に: 今要る量に合う大きさにする（食べ物なら小麦 1 列 8〜16 マスから、木材なら 4〜8 本から）。
  できた施設は広げられない（増やすときは別の名前の施設を足す）
- 畑は水の近くの平らな区画、植林場は平らか木の多い区画、採掘場は石の多い区画（家や作業場の近く）
$previous_error
## 出力
指定の JSON で出力してください。
""")

# 大目標の計画（docs/design/50 §18）: 段階（中目標）と、それぞれの小目標の並び、前提つき（purpose=plan）
PLAN_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者の、大目標を進める計画を立てます。
大目標を段階（中目標）に分け、それぞれを小目標（今やる 1 つの具体的な作業）の並びにします。
計画は一度立てたら、失敗や行き詰まりがあるまでこのとおりに進めます。材料の作り方の分解と細かい操作は
自動ですが、何を先にそろえるか、どの順に進めるかはここで決めます。

## 大目標
$objective

## 今していること
$activity
$town_map
$replan
## 計画の書き方
- 中目標（jobs）: 大目標を達する段階を、順番どおりに 1〜6 個。title は段階の名前（日本語の名詞句。
  例: 水源の確保、種の用意）で、条件（predicate…）は「その段階が終わった状態」。条件で書けない段階
  （例: 土地の確保）は、その段階の最後の小目標の条件を書く。最後の中目標は大目標の完了条件に達する
  もの（完了条件そのものでよい）
- 小目標（steps）: 中目標を進める具体的な一歩を順番どおりに（8 個まで）。条件は世界から判定される
  （持つ、置く、植える、整地する…）。今もう満たしている小目標は自動で飛ばされるので、要るなら
  持っている物も書いてよい
- 前提（needs）: 小目標の前に要るもの（2 段まで）。小目標がもう済んでいれば前提も飛ばされる。材料が
  なければ集める小目標を前提に書く。順序: (1) その物のレシピが直接使う材料と道具を並べる、(2) 持ち物
  （状態の「持ち物」）を引く、(3) 足りない物だけを、その物に近いほうから順に追う（足りない物の材料が
  さらに足りなければ、その分を前に）。持っている材料の元（板材を持っているのに原木）は集めない。
  思い込みで足した物は、関係がなくても集めに行ってしまう（コードが、ほかの条件の材料にない物と、
  もう持っている物を差し戻す）。前提のない小目標は空にする
- 今の持ち物・近くにあるもの・覚えている場所を見て、集め直しや遠回りをしない。1 つの小目標は数分
  で終わる大きさ。素材の採集だけの中目標は作らない（何のための素材かを中目標にする）
- 済んだ中目標は書き直さない。視聴者の頼みの中目標は書かなくても残る
- 畑（facility）の柵とゲートは、畑のマス（整地・水・耕す・まく）が全部済んでから最後に、施設が自分で置く。
  柵のための板を先にそろえる段階や、placed(oak_fence…) の段階は書かない（板は柵の番になってから）
- 中目標に大目標の完了条件を書くのは最後の 1 つだけ。途中の段階は、その前にそろえるものにする。
  同じ条件の中目標や、同じ title の中目標を並べない
- built(名前) は、大目標の完了条件に入っている名前の建物か、もう建ててある建物だけ。家そのものは
  名前なしの built で、存在しない名前（built(house) など）は判定できないので書かない。家が建ててある
  （「家:」に建ててあると書かれている）とき built() は今もう満たしているので、中目標にも小目標にも
  書かない（書いた瞬間に済んで何もしない。コードが差し戻す）。「家の中で夜を越す・待つ」も計画に
  書かない: 夜と安全は生存の小目標が自動でやる
- 品目は Minecraft の正式な名前 1 つ（oak_log、oak_planks、oak_door）。複数形（oak_logs）、まとめた
  名前や造語（planks_and_door、wooden_tool_materials）は判定できない。いくつもの物は小目標を分ける
- 家がまだ無い間（「家:」に建ててあると書かれていない間）は、placed(…, near_home) や placed(…, home) を
  書かない（家ができてから）。場所は x,y,z か、大目標の built の名前（build:名前）だけ。at_home と
  through_night は計画の条件にない
  leveled は where が home か x,y,z（座標）で、distance が要る

## 例（形だけ。<A>〜<E> は、この大目標の品目に置き換える。この例の物は書かない）
1. <段階の名前> have(<A>, 1): 小目標 have(<A>, 1)（前提 have(<B>, 1)（前提 have(<C>, 3)））
2. <段階の名前> have(<D>, 16): 小目標 have(<E>, 1)、have(<D>, 16)
3. <最後の段階> <大目標の完了条件>: 小目標 <大目標の完了条件>

## 使える条件
$conditions
$previous_error
## 出力
指定の JSON で出力してください（outline、jobs）。
""")

_GRID_FIXED = {
    "home": "H",
    "building": "B",
    "farm": "F",
    "tree_farm": "W",
    "mine": "M",
    "pen": "P",
    "trap": "X",
    "road": "=",
}


CLEAR_RULE = """\
- 計画に合わない既存の建築（道や大通りをふさぐ、置き場所が悪い）は、clear に書く。壊して更地にし、その区画は
  areas に使える。使っている畑・建物は壊さない。本当に邪魔なものだけ（1 回に 3 つまで）
"""


def _removable_section(base: list[Zone]) -> str:
    """壊してよい既存の建築の節（なければ空）。"""
    removable = removable_facilities(base)
    if not removable:
        return ""
    lines = [f"- {name}: {', '.join(ids)}" for name, ids in removable.items()]
    return (
        "\n## 壊してよい既存の建築（家と採掘場は壊せない。計画に合わなければ clear に書く）\n"
        + "\n".join(lines)
        + "\n"
    )


def _zone_letter(zone: Zone, row: dict[str, Any]) -> str:
    """地図の 1 文字（固定の区画は用途、空きは地形）。"""
    if zone.fixed:
        if zone.use == "unusable":
            return "!" if zone.note == "溶岩" else "~"
        return _GRID_FIXED.get(zone.use, "B")
    if float(row.get("seen", 0)) < 0.5:
        return "?"
    if float(row.get("trees", 0)) >= 0.4:
        return "T"
    return "." if float(row.get("flat", 0)) >= 0.6 else "^"


def _zone_grid(letters: dict[str, str], ids: list[str]) -> str:
    cols = sorted({z[0] for z in ids})
    rows = sorted({int(z[1:]) for z in ids})
    lines = ["     " + " ".join(cols)]
    for r in rows:
        lines.append(f"  {r:>2} " + " ".join(letters.get(f"{c}{r}", " ") for c in cols))
    return "\n".join(lines)


def _terrain(row: dict[str, Any]) -> str:
    if float(row.get("seen", 0)) < 0.5:
        return "まだ見ていない"
    height = row.get("height")
    h = f"、高さ {int(height):+d}" if height is not None else ""
    return (
        f"平ら {row.get('flat')}、木 {row.get('trees')}、石 {row.get('stone')}、"
        f"水 {row.get('water')}{h}"
    )


def format_town_map(town_map: Optional[TownMap]) -> str:
    """
    目標の決定に見せる理想図の要約: 用途ごとの区画と施設（今あるものと予定）、空きの数。
    """
    if town_map is None:
        return ""
    lines = [
        "## 街の理想図（区画は 8 ブロック四方。列 A〜L が西→東、行 1〜12 が北→南。施設はここに作る）",
        f"考え: {town_map.concept}",
    ]
    counts: list[str] = []
    for use, zones in town_map.by_use().items():
        if use in ("free", "unusable"):
            counts.append(f"{use} {len(zones)} 区画")
            continue
        groups: dict[tuple[str, str, bool], list[str]] = {}
        for z in zones:
            groups.setdefault((z.facility, z.note, z.fixed), []).append(z.id)
        parts = []
        for (facility, note, fixed), ids in groups.items():
            label = facility or ""
            if not fixed and facility:
                label += "、予定"
            if note and not fixed:
                label += f": {note}"
            parts.append(f"{', '.join(ids)}" + (f"（{label}）" if label else ""))
        lines.append(f"- {use}: " + " / ".join(parts))
    if counts:
        lines.append("- " + "、".join(counts))
    if town_map.clear:
        lines.append("## 計画に合わない既存の建築（壊して更地にしてよい。道具 clear_site {name}）")
        lines.extend(f"- {name}: {why}" for name, why in town_map.clear)
    return "\n".join(lines) + "\n"


STAGE_TEMPLATE = Template("""\
配信の最終目標の「街」は、次のように定義してある:
$text

その段階「$title」（$why）には、まだ判定できない部分がある:
$unresolved

今は次の条件で判定できるようになった。この段階の意味を変えずに、書けるものを conditions に
書き直してください。まだ書けないものは unresolved に残してください（何ができれば書けるかを添えて）。
今の条件（すでにある conditions も含めて 3 つまで）:
$conditions

すでにある conditions: $current

書き直しの決まり:
- 意味を変えて書けるものに言い換えない
  （今の built() は最初の家のことで、2 軒目や倉庫の意味にはならない）
- 今もうそろっている条件にしない（まだできないことは、今そろってはいない）

## 今できること
$abilities
$previous_error
## 出力
指定の JSON で出力してください。
""")

# 目標の決定の決まり（システム指示）。どの呼び出しでも同じ文にして、Gemini の暗黙のキャッシュに
# 当たるようにする（docs/design/26 §4）。状態で変わるものは GOAL_TEMPLATE の側に書く
GOAL_SYSTEM_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者の方針を決めます。
目標は 4 層: 最終目標（変わらない）、大目標（上から順に取り組むリスト）、中目標（一番上の大目標のための仕事の並び）、小目標（今の 1 つ）。
決めるのは次の小目標と、見直しに応じた大目標リスト・中目標・自分のメモの編集です。

## 方針: 効率を先に
- 最終目標に直接関係しなくても、先に作ると後が速く・安全になるもの（良い道具・防具、畑、植林場、
  備蓄、明かり、作業台やかまどの置き場）は、要るなら大目標に足して先にやってよい（最終目標への近道）
- 同じ物を何度も遠くへ取りに行く、同じ作業を繰り返す、待ち時間ができた、は効率化の合図
- 目安は、作る手間がこの先それで浮く手間より小さいこと。理由は review に書く（配信で伝わる）

## 定石（順序はコードが守る）
- 街づくりの土台は、寝床 → 食料（畑）→ 建材（植林場）→ 資材（鉄）…の順（状況の「定石」の行）。
  今の段は [定石 …] の大目標として一番上にあり、コードが守る: 動かせず、やめられず、ほかの大目標を
  その前に置けない（置こうとすると差し戻される）。足したほかの大目標は定石の段の後ろに入る
- 定石の段は、作って回るところまでが 1 段（畑なら畑と最初の収穫）。小目標は今の段を進めるもの。
  段のための材料は、その段の中目標に書いてよい（例: 畑の水に要る桶の鉄集め、植林場の苗木）
- 種・苗木は全マスの 1/4 だけ集めて植え、残りは育ったものから回収して増やす（探し回らない）。
  1/4 植わった段は育成中で、次の段を同時に進めてよい
- 夜・空腹・体力・死亡の身を守る小目標は、定石より先にしてよい（定石の大目標は動かない）

## 見直し（review。毎回、最初に）
今の状態（持ち物、チェスト、目の前の物、近くにあるもの、覚えている場所、時間帯、「中目標の今の
状態」）から、もっと早く・無駄なく進むやり方を探す。確かめること:
- 済んだ中目標、持っている物・チェストにある物の中目標は飛ばす（集め直さない）
- 目の前の材料と落ちている物を先に使う（遠くへ探しに行くのはそのあと）
- 同じ外出で取れるものは束（together）にする（ほかの大目標の物も。木を見つけたら原木と、葉から
  苗木）。合わなくなった束（片方だけ遠い、順番が要る）はほどく。集めた物を 1 個ずつ家に運ばない
- 先に作ると速くなる道具（石のツルハシなど）は先に作る
- 何度も要る物・近くにない物は増やす仕組みを作る（木は植林場、食べ物は畑: facility で作る・別の名前で増やす）
- 待つ時間（作物が育つ間、夜、家の中）は、そばでできること（畑の空きを埋める、クラフト、置く、しまう）に使う
- 今は手に入らない中目標には、手に入るようにする中目標を前に入れるか、大目標を後ろに回す
見つけたら plan_changes / jobs を直し、何をどう変えたかを review に書く。今のままが一番よければ
変えずに理由を書く（変えないのも答え。理由のない入れ替えはしない）

## 小目標の決め方
- 小目標は、編集したあとのリストの一番上の大目標を進めるもの（serves: current）。別の大目標を今
  やるなら move で一番上にする。ほかの大目標の物をついでに取るなら together に入れる
- 例外は身を守る小目標（through_night、at_home、cleared、have(food, n)）で、serves: survival なら
  大目標に関係なく選べる（夜や空腹は待ってくれない）
- 食べ物を探すときも have(food, n) を選ぶ。近くになければ、畑や狩りなど手に入れる方法を考える
- 大目標は「何ができた状態か」（数十分）、中目標はそのための仕事の単位（数分〜10 分: 道具を持つ、
  食料を 8 個）、小目標はその仕事の「次の一歩」（数分: 材料を集めに出る）。小目標が中目標と同じになる
  のは、仕事そのものが 1 回の遠征のときだけ（画面に 2 つ並ぶので同じ文では伝わらない）。中目標を選べば
  その次の一歩（「中目標の今の状態」に「次の一歩」と出る）が小目標になる。例: 家 (built) → 中目標
  have(planks, 40) → 小目標 have(log, 10) → 仕上げ built
- 分解（ベッドには羊毛 3・板材 3・作業台）と細かい操作（何を掘る・どこへ行く）は自動。小目標は
  下の述語の形で出す（達成はゲームの状態から判定）
- 夜は地上が危険。近ければ家、遠ければ近くのベッドか持っているベッドを置いて寝る（そこが復活地点）、
  地下ならそのまま作業。夜に家の中にいる間は、中でできること（クラフトなど）を選んでよい。
  視聴者に「今やる」と言った頼み（一番上）は夜でも選ぶ
- 石・石炭・鉄が地表にないときは階段を掘って下りる（深さの上限なし。溶岩・水・砂利・岩盤で止まる）。
  下りたくないときだけ dig_depth（0: 下りない、n: 今の足元から n 段まで）
- 近くの敵への対処と、空腹のときに食べるのは選ばなくても行われる
- 配信での自分の発言・視聴者との約束と食い違わない。「pointed out in chat」で考え直すときは、
  返事で約束したことを今やるか大目標に足す。しないなら reason に理由を書く（黙って戻らない）

## 大目標の中目標（jobs）
- 一番上の大目標の中目標を、仕事の並び（1〜6 個、順番どおり）で書く。仕事は成果物や状態の単位
  （石のツルハシを持つ、食料 8 個）で、1 回のクラフトや 1 種類の素材集めだけにしない（分解と次の一歩は
  自動）。良い書き方 have(stone_pickaxe, 1)、悪い書き方 have(cobblestone, 3) → have(stick, 2) →
  have(stone_pickaxe, 1)。素材の採集だけ（have(raw_iron, 3)）の中目標は差し戻される: 鉄のツルハシ
  のように、それを使って作るものを書く。次にどれをやるかは速いモデルが選び、済んだかはゲームの状態で判定される
- 書くのは、中目標がない（「今していること」の大目標に「中目標:」がない）、うまくいっていない、見直しで
  もっとよい中目標が見つかったとき。それ以外は空にする
- 中目標に「計画:」（小目標の並び。今:・待:・済:、<は前提）が付いているときは、別の計画が書いてある。
  jobs は書かず（欄がない）、小目標は「計画」の今: を選ぶのが基本。計画が合わないときは reason に書く
- 中目標は完了条件の述語で書き、各中目標に理由を 1 文付ける（配信に出る）。最後は大目標の完了条件そのもの
- 「今していること」の中目標は 済:（一度満たしたら済みのまま。あとで材料を使っても戻らない）・今:
  （まだの一番先）・待: で出る。書くのはこれから残す中目標だけ（済んだものは書かない）。視聴者の頼みや
  提案の中目標は、書かなくても残る（やめるのは理由を付けた drop だけ）。中目標は全部で 8 個まで
- 束: together で 4 つまで同時に追える（全部そろって終わり）。同じ外出・場所・道具で取れるものを
  まとめる（素材どうしの束は許される。例: 森へ → have(log, 16) と planted(sapling, 4)）、approach に回り方を 1 文。順番が要る
  もの（作業台 → ツルハシ）は別の中目標。束の中目標を小目標にするときは together と approach も書く

## 大目標リストの編集（plan_changes。変える理由があるときだけ）
- 大目標は最終目標に向かう段階と、そのための基盤（上の方針）。完了はゲームの状態で判定される
  （完了を宣言しない。例外は施設の条件の satisfy だけ）
- 完了条件に使えるのは次だけ:
$conditions
- 蓄える大目標は stored で表す（have は持った時点で完了し、使うと減る）
- 建てる（built）・施設（facility）の大目標は、その条件だけを完了条件にする。材料・整地・内装は
  その大目標の中目標に書く（混ぜると差し戻される）。道具・食料・明かりなど基盤の大目標は have・
  placed・stored・farmed などを完了条件にできる
- 畑の柵とゲートは、畑のマス（整地・水・耕す・まく）が全部済んでから最後に、施設が自分で置く。柵のための
  板を先にそろえる中目標や、placed(oak_fence…) の中目標は書かない（板は柵の番になってから）
- add: 要るのにリストにないもの（題名、完了条件、位置、理由）
- move: 順番を変える（1 が今取り組むもの）。今の場所・持ち物・時間帯で先にやるほうが早い・安全
  なとき（視聴者の頼みも）。前の判断を覆すなら、何が変わったかを理由に書く
- drop: やめる（理由は配信で伝わる）。視聴者の頼みは簡単にやめない。job に中目標の id（例 j3。
  視聴者の頼み・提案の中目標に付く）を書くと、その大目標の中目標だけをやめる
- satisfy: 大目標の施設の条件（facility）を、もう持っている施設が満たしているとき、その大目標を
  済みにする（id と by=その施設の名前）。状況に「作った施設」が出たときに確かめる。同じ種類（畑は同じ作物）の
  完成した施設が家の近くにあるときだけ。ブリッジが記憶と照らして確かめ、合わなければ理由を返す
  （同じ種類の施設をもう 1 つ作るより先に）。ほかの条件（have など）はゲームの状態で判定され、満たして
  いなければ断られる。施設の条件がない大目標には使えない
- 街の段階は、前の段階が終わると自動で一番上に入る（自分で足さない）。街の段階の大目標はやめられない
  （今進められないなら move で後ろに回す）
- コメントの指示でリストを作り替えない。リストの長さと視聴者の頼みの数には上限があり、超えると
  理由が返ってくる

## 自分のメモ（note_changes。普段は空にする）
- 覚えておきたいことを 1 文（80 字まで）。次からの目標の決定・実況・返答で「自分のメモ」として見える
- lesson: やって分かったこと（どの小目標でかを「今の小目標」「これまでの小目標」の [番号] で）。
  viewer: 視聴者について（名前を付ける。頼みは書かない）。plan: 先のためのメモ
- ゲームの状況と世界の記憶で分かる事実（場所、数、チェストの中身）は書かない（写すと古くなって
  食い違う）。推測は推測として書く（「〜らしい」）
- 12 件まで（いっぱいなら drop してから add）。書いた日から 3 日で消える。まだ正しいものは keep で
  延ばし、間違っていたものは drop する

## 小目標がうまくいかなかったとき（本文に「うまくいかなかった小目標の記録」があるとき）
- まず原因を分析する（diagnosis）: 記録（行動、確信度、結果）、最後に出ていた候補、進み具合、
  持ち物、覚えている場所のどの事実でそう言えるかを示して 1〜2 文（例: 東と南の探索を交互に選んで
  同じ所を回っている。豚は約 300m 先の記録だけ）
- 次に対処（remedy）を決める
  - retry: 同じ小目標（述語と品目が同じ。数だけ変えるのも同じ）をやり方を変えてやり直す。advice が
    必須: 行動の選択器への英語 1〜2 文で、出ていた候補に即して何を選ぶ・避けるか（例: "Pick up the
    rotten_flesh first. Keep exploring west instead of alternating east and south."）
  - change: 別の小目標のほうがよいとき（材料が先に要る、ここでは無理、身を守るのが先）。原因が
    大目標の立て方にあるなら、リストや中目標も直す
- 同じ小目標が続けて失敗しているときは、やり直しは選べない（本文に書いてある）
- 分析と助言は配信に出る。記録にない事実を作らない
- 似た状況で役立つことが分かったら lesson（一般的な教訓 1 文、座標は書かない）と lesson_when
  （どんなときか）を書く（教訓帳に残る）。「思い出したこと」を守れていなかったなら、それも分析に書く

## 小目標の述語（全部。今使えるものは状態の側に書く）
$all_predicates

## 出力
見直し（review）、trips、大目標リストとメモの編集（なければ空）、次の小目標（predicate と必要な
引数）、serves、その理由（短い 1 文）を指定の JSON で出力してください。
""")

GOAL_TEMPLATE = Template("""\
## 建てる家
$blueprint

## 今していること
$activity
$town_map$facility_memory
## 今使える小目標
$predicates
$jobs

## 最近の会話（配信での自分の発言と視聴者のコメント）
$recent_messages

## 小目標を選び直す理由
$reason
$failure$previous_error
""")

# spikes/primitive_choice_eval.py で測った: 体が必要とするもの（needs）を状態に書くと効いた。
# 指示に優先順位を書くと、選択器は 20m 先の敵からも逃げるようになった。
# 並びは「変わらないものが前、変わるものが後ろ」（設計書 127 §5）: 決まり、（印）、目標の階層（format_activity が
# 小目標の前にも印を置く）、いまの状態。印はキャッシュの境目で、ほかのアダプターは取り除く。決まりは
# 状態の後ろにあり、先頭から同じ部分が全体の 26% しかなかった（2026-10-09 の記録の中央値）
TOOL_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者として、自分の手で操作しています。
今の小目標に向けて、次に呼ぶ道具を 1 つ選んでください。

## 決まり
- 行動の道具には intent（今やろうとしていること、配信者の口調の 1 文、日本語）を必ず書く。配信で
  見え、そのまま読み上げる（例: 北の森で原木を集めてくるね）
- 見張りの質問（watch）は、行動の途中で起きうることを確かめたいときだけ添える。「<on_yes>: <質問>」
  の文字列（例: wake: Has a hostile mob come within 6 m?）。質問は 1 秒ごとに速い判断モデルに聞く
  はい/いいえで、英語で、状態（action / self / surroundings / mobs / needs）から答えられることを
  中立に書く（急ぎや優先の言葉は書かない）。on_yes は stop（止める）、wake（止めて考え直す）、
  maybe_done（止める。完了はワールドで確かめる）。完了の判定には使われない
- 同じ失敗を繰り返さない。断られた・失敗した理由を読んで、別の手を選ぶ。2 回続けて通らなければ、
  ask_advisor（出ているとき）で原因と別の手を聞く
- 「目の前のブロック」「目の前に落ちている物」に要る物があれば、遠くへ探しに行く前にそれを使う
  （一番近いものの座標をそのまま dig などに使える）。原木は木の幹だけを集める: find_blocks の tree: false
  は村の畑の縁や家の柱など人が作った物（not_trees は目の前にあるその数）で、壊すと畑の水が流れ出る。
  水や溶岩に接したブロックは掘れない（断られる）ので、別の木を探す。自分で置いた原木（placed_by_me）と、
  葉が散って何にも触れずに浮いた幹（leftover: true。tree は false でも取り残しなので）は集めてよい
- 石・土・砂利・鉱石などの地形は、「採掘の場所」の近くから掘る（around と find_blocks の quarry: true。
  毎回別の所を掘ると、家のまわりが穴だらけになる）。鉱石へ下りるときは、前に掘った階段の底から続ける
- 分からないことは検索の道具で調べてから動く（すぐ返る。1 ステップ 5 回まで）。検索語は日本語でも
  英語でもよく、名前でも目的でもよい。返った座標や名前をそのまま使う
  - search_abilities: やり方が分からない・初めてのこと（例: 畑を作る、水を渡る、肉を焼く）
  - item_info: 物の取り方・要る道具・使い道（例: 鉄、iron_ingot）。uses_of: その素材で作れる物
  - find_foods: 食べ物が要るとき、いま手に入る所（持ち物・チェスト・見える動物・熟した作物の満腹度。足りない満腹度と比べる）と、食べられる物の取り方（レシピ、かまど、畑、狩り）。食べ物を探しに出る前、食べられるか分からない物は eat の前に
  - search_facility: 自分の施設（畑、植林場、建物）の場所と中身。行く・使う前に
  - search_memory: 前に見た場所（鉱石、木、動物、チェストの中身）。遠くへ探しに行く前に
- 夜に家の中にいるときは、外に出る道具は断られる
- 座標は整数で、符号つき。z=-284 は -284 と書く（マイナスを落とすと別の場所になり、遠すぎると断られる）。y は足元の高さ
- look_screen（使えるときだけ出ている）は配信の画面を撮り、次の選択に画像として添える。
  文字の状態で分からないとき（地形、何が起きているか）に使う。画像で完了は決めない
- 画像が添えてあれば、それはいま配信に映っている自分の視点
${cache_break}
## 配信者が今していること
$activity

## まわり（ブリッジが今測ったもの）
- 位置: $position
- まわりの形（$legend）:
$grid
- 隣のうち歩いて上がれない（2 段以上の）ところ: $walls / 8、頭の上: $head、足元: $standing_on
- 近くのモブ（attack / flee の entity にはこの id を使う）: $mobs

## ソルバーの提案（参考。従わなくてよい。do_suggestion の id）
$suggestions

## 直近の道具（古い順）
$recent
""")

# 配信者が判断に迷って聞くときの advisor への依頼（docs/design/143）。advisor は道具を呼ばず、助言だけを返す
ADVICE_TEMPLATE = Template("""\
You are the advisor of a Minecraft streamer (an AI) who is playing on her own. She was choosing her next tool,
got stuck (a tool kept failing, or she did not know what to do next) and asked you. Below is exactly what she saw
when she asked, then her question.

${situation}

## Her question
$question

## How to answer
- Read the situation and the recent tool records, find what is really going on, and answer her question
- Say what to do next: which tool with which arguments (use only the tools and the coordinates that appear above),
  and why in one sentence. If the recent tools keep failing for one cause, say the cause and a different way
- Do not call tools. She decides whether to follow you
- Answer in Japanese, in at most 300 characters
""")

# 続けて失敗しているとき（STUCK_FAILURES 回以上）に足す節: 同じ手を繰り返さず、原因を考えて別の方法を選ばせる
STUCK_FAILURES = 2
TOOL_STUCK_SECTION = """
## 行き詰まっている（$why）
$record- 「直近の道具」の失敗の理由を読み、なぜ失敗したかを考える。同じ道具を同じ引数（近い座標）でもう一度は
  呼ばない。言い換えた intent で同じことを繰り返すのも同じ
- 原因ごとの別の方法: 経路がない・遠い → 別の場所や別の木・鉱石を find_blocks / search_memory で探す、
  足場を置く・掘って進む / 材料や道具が足りない → 先にそれを集める・作る / 断られた → 断られた理由の
  ブロックや場所を避ける / 敵が近い → 倒すか離れるか、家に戻る
- 何をすればよいか分からなければ、動く前に search_abilities / item_info / search_facility で調べる
- 原因が分からない・別の手が思いつかないときは、実験や小目標の変更より先に、ask_advisor（出ているとき）で
  聞く。試したことと失敗の理由を question に書く。答えは次の選択に出る
- 原因が分からないまま同じことが通らないときは、実験する。変えるものは 1 回に 1 つだけ:
  0. 家や行き先に着けないときは、先に inspect（座標なし）で自分を見る。closed_space（歩いて出られない閉じた所）と
     home_door（ドアまで何ブロック離れ、何ブロック下か）が分かる。ドアから遠いなら、ドアは原因ではない
  1. diagnostics でブリッジが残した失敗の記録を読み、inspect でそのマス（x, y, z）と自分（座標なし）を見る
     （ブロックの状態 props、隣、届くか、間にある物、スニークしているか、手に持っている物）
  2. 見たことから原因の仮説を 1 つ立て、use_item の押し方を 1 つだけ変えて試す
     （face = 押す面、sneak = スニークの有無、item を省く = 素手、別の位置から）。家のドアは座標の代わりに target: home_door
  3. もう一度 inspect で変わったかを確かめる。変わったらその押し方で続ける（うまくいった手は覚えられて、
     次の同じ場面から候補に出る）。変わらなければ仮説を捨て、別の 1 つを変える
  4. 3 つ試して通らなければ、続けずに別の手（家に戻る・小目標を変える）に切り替える
- 小目標そのものが今は進められないと考えたなら、ask_advisor で聞いてから、家に戻って安全に別のことをする
  （小目標は自分で決め直される）
"""

TOOL_REFUSED_SECTION = """
## しばらく使えない手（同じ場所へ同じ手は、また断られる・着けない。選ばない）
$refused
"""


def _refused_section(refusals: Sequence[dict[str, Any]] | None) -> str:
    """ブリッジが断った・着けなかった手の台帳（理由と何分前か）。なければ空。"""
    if not refusals:
        return ""
    lines = "\n".join(
        f"- {r['tool']} (x={r['x']} y={r['y']} z={r['z']}): {r['reason']}（{r['minutes_ago']} 分前）"
        for r in refusals
    )
    return Template(TOOL_REFUSED_SECTION).substitute(refused=lines)


# 同じ道具が同じ理由で失敗し続けているとき（設計書 51 追記 2）: 引数の数字を変えても同じ。理由と別の方法を示す
TOOL_SAME_CAUSE_SECTION = """
## 同じ理由で失敗し続けている（$tool が $times 回続けて、同じ理由）
- 理由: $reason
- 引数の数字を少し変えても、この理由は変わらない。同じ道具を同じやり方でもう一度は呼ばない
$advice$withheld"""
SAME_CAUSE_FAR = """- 「遠すぎる」と断られ続けているのは、座標を書き間違えているから: 状態の座標（x=… y=… z=…）をそのまま写す。
  マイナス（z=-285 を 285 と書くなど）を落としていないか確かめる
- 座標を自分で書かずに、提案（do_suggestion）の id をそのまま使うか、find_blocks の x, y, z をそのまま使う
"""
SAME_CAUSE_OTHER = """- 理由を読んで、原因を直す手を先にする。分からなければ、提案（do_suggestion）の id をそのまま使うか、
  search_abilities / item_info で調べるか、ask_advisor（出ているとき）で聞く
"""
SAME_CAUSE_WITHHELD = "- この手番では $tool は出していない。ほかの道具を選ぶ\n"


def _same_cause_section(cause: SameCause | None) -> str:
    """同じ理由の失敗が続いている節。なければ空。"""
    if cause is None:
        return ""
    far = "away (at most" in cause.reason or "m away" in cause.reason
    return Template(TOOL_SAME_CAUSE_SECTION).substitute(
        tool=cause.tool,
        times=cause.times,
        reason=cause.reason,
        advice=SAME_CAUSE_FAR if far else SAME_CAUSE_OTHER,
        withheld=Template(SAME_CAUSE_WITHHELD).substitute(tool=cause.tool) if cause.withheld else "",
    )


def _stuck_section(failing: int, stagnant_seconds: float, actions: Sequence[str]) -> str:
    """行き詰まりの節: 続けて失敗しているか、状況が動かない（その間の手と結果を見せる）とき。"""
    why = []
    if failing >= STUCK_FAILURES:
        why.append(f"続けて {failing} 回失敗")
    if stagnant_seconds > 0:
        why.append(f"{stagnant_seconds:.0f} 秒、位置・持ち物・目標の進み具合が変わらない")
    if not why:
        return ""
    record = (
        "その間にした手と結果（古い順）:\n" + "\n".join(f"- {a}" for a in actions) + "\n"
        if stagnant_seconds > 0 and actions
        else ""
    )
    return Template(TOOL_STUCK_SECTION).substitute(why="、".join(why), record=record)


# 同じ手の並びを続けて成功させているとき（技にする機会。docs/design/22 §13）
TOOL_REPEAT_SECTION = """
## 同じ手の並びを繰り返している（$steps を $times 回、成功が続いている）
$advice"""
REPEAT_NEW_SKILL = """\
- この並びは、技にすれば 1 回の呼び出しで続きまで動く（1 手ずつ選ぶより速い）。技にしたほうが速くて
  確かなら write_skill を呼ぶ（name は英小文字と _、what は並びが何をするか。書いたらすぐ 1 回試す）。
  技にしないなら、そのまま続けてよい
"""
REPEAT_KNOWN_SKILL = """\
- この並びには、覚えた技 $skill がある。run_skill で呼ぶと、1 回で続きまで動く
"""


def _repeat_section(steps: Sequence[str], times: int, skill: str) -> str:
    """繰り返しの節: 技にする（まだない）か、ある技を呼ぶ。"""
    if not steps:
        return ""
    advice = (
        Template(REPEAT_KNOWN_SKILL).substitute(skill=skill) if skill else REPEAT_NEW_SKILL
    )
    return Template(TOOL_REPEAT_SECTION).substitute(
        steps=" → ".join(steps), times=times, advice=advice
    )


# 技（docs/design/22）: 道具の選択に足す節（技を使うときだけ）
TOOL_SKILLS_SECTION = """
## 覚えた技（run_skill で呼ぶ。今の小目標に合うものが先）
$skills
- 技は道具をいくつも呼ぶ手順で、1 回の呼び出しで最後まで動く。合う技があれば、道具を 1 つずつ
  呼ぶより先に使う
- 同じ道具の並びを何度も呼んでいるとき、手順にしたほうが確かなときは write_skill で技にする
- 技が失敗したら先に原因を調べる。コードのせいなら同じ名前で write_skill（what に原因と直し方）、
  場面のせいなら道具で整える。直さず呼び直さない
"""

# 生の bot の技を勧める 1 文（docs/design/168）。skill_raw_bot が有効なときだけ、技の節に足す
TOOL_RAW_SKILLS_LINE = """\
- ブリッジの道具にない手が要るとき、同じ断りや失敗が続くときは、write_skill で本物の bot を触る技
  （raw）を書ける。道具の断りは掛からない。成功は expects で世界から確かめられる
"""

# 技を書く（docs/design/22 §3・§4）。決まった文なのでシステム指示に置く（26 §4）
SKILL_SYSTEM = """\
あなたは Minecraft のサバイバルで暮らす AI 配信者の「技」を JavaScript で書きます。技は道具を
組み合わせた手順で、ブリッジのサンドボックスで動き、うまくいったものは覚えて何度も使います。

## 形
export default async function (t, args) {
  // t: 下の API。args: 引数（params のスキーマどおり）
  return t.done('何をしたか')   // 最後は t.done(...) か t.fail('理由')
}

## API（t）。どれも await する
- 行動（A の道具と同じ名前と引数。結果は { ok, result, refused? }。断られても例外にならない）:
  t.goto({x, y, z, range?}), t.dig({x, y, z}), t.place({item, x, y, z}), t.craft({item, times?}),
  t.pickup(), t.attack({entity}), t.flee({entity}), t.eat({item}), t.equip({item}),
  t.smelt({input, count?}), t.deposit({item, count?}), t.withdraw({item, count?}), t.go_home(),
  t.sleep(), t.build_next({name?}), t.move_furniture({x, y, z, to_x?, to_y?, to_z?}),
  t.plant({item?, x?, y?, z?}), t.till({x?, y?, z?}), t.sow({item?, x?, y?, z?}), t.water({x?, y?, z?}), t.wait()
  （plant / till / sow / water は場所を省くとブリッジが設計した施設から選ぶ）
- 調べもの: t.find_blocks({block, radius?}) → { ok, result: [{x, y, z, distance_m, direction, exposed,
  props?, source?: 'map'（前に見た記録）, seen_minutes_ago?}] か文 },
  t.recipe_of({item}), t.find_recipes({query, limit?}), t.find_foods({query?, have?, limit?}), t.how_to_get({item, count?}),
  t.state() → { self: {position, health, food, in_home, ...}, inventory: {名前: 数},
  tool_wear: [{item, left, max}]（道具の残りの耐久。少ないものは折れる前に予備を作る・家へ帰る）, mobs: [{id, name,
  hostile, distance_m, direction}], surroundings, needs, time, action },
  t.block_at({x, y, z}) → { name, props? }（props はブロックの状態で値は文字列: 耕地の moisture
  "0"〜"7"（"7" が湿っている）、作物の age、ドアの open など）
- 判定: t.judge(question, {kind: 'yes_no' | 'choice', options?}) → { answer, confidence }（速い判断
  モデルが今の状態を見て答える。答えがなければ answer: null）
- 記録: t.log(message)（失敗したときに読み返す）
- ないもの: require、fetch、process、setTimeout、ファイル。ソルバーの提案（do_suggestion）も使えない

## 決まり
- 座標を決め打ちしない。t.state() や t.find_blocks() で探すか、引数にする（技は別の場所・別の
  ワールドでも使う）
- 行動の結果の ok を確かめ、だめなら理由を t.log して別の手か t.fail(理由) にする
- ループには上限を付ける。上限: 1 回に行動 40 回、調べもの 200 回、judge 10 回（時間の上限はない）。
  上限を超えると技は失敗になり、覚えられない。繰り返す手順は、上限より前（行動が 36 回ほど）で自分で
  切り上げて t.done する（残りは、技をもう一度呼んで続ける）。await せずに回るループは止められる
- 場所を省いた till / sow / plant は、断られる（マスがもうない、種がない）まで毎回次のマスを選ぶ。
  それらの前に equip は要らない（道具側が持つ）。断られたら（refused）、繰り返しを終えて t.done する
- 行動を同時に呼ばない（Promise.all で行動を並べない。1 つずつ）
- 安全の制約（夜に家の外へ出ない、家を壊さない）は道具の側にあり、断られる。回り道をしない
- 1 つの技は 1 つのこと（狩る、木を切って板材にする、家のまわりに松明を置く）。大きな目標は小さな
  技の組み合わせで進める
- expects: 技が成功したときに世界で成り立つこと。have / stored（item と count。count は数、
  "+N"（始めより N 多い）、"$引数名"）、planted / farmed（item と count。"+N" も）、built（name）、
  placed（where: home）、lit（distance）、
  progress（今の小目標の残りの作業が減る）。done() だけでは成功にならない
- 直すとき: 前のコードと失敗の記録（理由、log、最後の道具）を読み、先に diagnosis に原因（どの
  呼び出しが、どんな文で失敗・断られたか、なぜか）を書き、その原因を直すコードにする。原因を
  書かない、前と同じコードは受け取られない
- コードは 4000 字まで。説明は日本語 1 文
"""


# 生の bot の技（docs/design/168）。raw_bot が有効なときだけ、技を書くシステム指示に足す
SKILL_RAW_BOT = """\

## 生の bot（raw: true の技）
t.* の道具では足りない手（ブリッジにない動き、tick ごとの操作）は、本物の mineflayer の bot を
触る技にして書けます。コードが次のどれかを使うなら raw を true にします。

- t.bot: 本物の mineflayer の bot（bot.entity.position / onGround / velocity、bot.blockAt(vec3)、
  bot.dig(block)、bot.placeBlock(refBlock, faceVector)、bot.equip(item, 'hand')、
  bot.inventory.items()、bot.setControlState('jump' | 'forward' | 'sprint' | 'sneak', bool)、
  bot.clearControlStates()、bot.lookAt(vec3, force?)、bot.waitForTicks(n)、
  bot.on('physicsTick' | 'blockUpdate' | ..., fn)、bot.pathfinder.setGoal(goal)、
  bot.registry、bot._client.write(パケット名, 中身) など）
- t.Vec3: new t.Vec3(x, y, z)
- t.goals: mineflayer-pathfinder の goals（new t.goals.GoalNear(x, y, z, range) など）
- t.sleep(ms): setTimeout の代わり（止められると待ちが終わる）。ほかのタイマーは使えない
- t.goto / t.dig などの道具とも組み合わせられる（同じ上限: 行動 40 回）

## 生の bot の決まり
- 安全の制約は何も掛からない: 家や建物の壁を掘る、溶岩や水に入る、夜に外へ出る手も通る。
  自分で確かめる。反射（敵、溺れ）が働いたときは技が止められ、失敗として返る
- 技が終わる（失敗・止められるときも）と、技が付けたリスナー・タイマー・操作キー・経路の目標・
  掘りは外される。残したいものは行動の結果として世界に残す
- 1 回の上限は 10 分。await せずに回るループは 1 秒で止められる。1 tick は 50 ms
- 生の bot の呼び出しは、失敗の記録の「最後の道具」には載らない。何が起きたか（座標、高さ、
  例外の文）を t.log に書く。直すときは、その log と例外の文を読む
- expects は必須（成功は世界で判定する）。t.done() だけでは成功にならない
"""

SKILL_TEMPLATE = Template("""\
## 書く技
名前: $name
すること: $what

## 配信者が今していること
$activity

## 直近の道具の呼び出し（古い順。手順の手本）
$recent

## ほかの技（重複しない。使えるものは真似る）
$skills
$previous$previous_error""")

SCREEN_REVIEW_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者です。添えた画像は、いま配信に映っている
自分の視点の画面です。下の「配信者が今していること」と見比べてください。

## 配信者が今していること
$activity

## 答えること
- seen: 画面に見えること（1〜2 文。見えたものだけ。見えないことは推測しない）
- matches_goal: 画面の様子が、今の小目標と今やろうとしていることに合っているか
- concern: 気になること（穴に落ちている、同じ所で止まっている、夜なのに外、敵が近い、
  など。なければ空）
- rethink: 今の小目標をやめて考え直すべきか。はっきり食い違うか、危ないときだけ true

注意: 目標を達成したかは画像では決めない（ゲームの状態で確かめている）。画像が暗い・
よく見えないときは、見えないと書いて rethink は false にする。
""")

ACTION_INSTRUCTIONS = (
    "You control a Minecraft survival player working toward the goal in the state. "
    "Choose the single best next primitive action. Stay alive first; otherwise make progress on "
    "the goal. If the state has advice from the planner, follow it unless it would put you in "
    "danger. The state's remember lists lessons from similar situations before; keep them in mind."
)
MOBS_SHOWN = 8
RECENT_ACTIONS_SHOWN = 8  # Jev に見せる直近の行動（ブリッジの observe.mjs HISTORY と同じ）


class GamePromptTemplateBuilder(IGamePromptBuilder):
    """
    ゲームのプロンプトのインフラ側アダプター。

    IGamePromptBuilder を文字列のテンプレートで実装する。LLM へのプロンプトは
    他のプロンプトと同じく日本語。行動の選択器には英語を渡す（評価したときの言語）。
    """

    def __init__(self, raw_bot: bool = False) -> None:
        """raw_bot: 技の書き手に、生の mineflayer の bot を触る書き方を教える（docs/design/168）。"""
        self._raw_bot = raw_bot

    def build_build_design_prompt(
        self,
        character: CharacterProfile,
        name: str,
        brief: str,
        home_note: str,
        builds_note: str = "",
        map_shown: bool = False,
        max_blocks: int = BuildDesign.MAX_BLOCKS,
        previous_error: str = "",
    ) -> str:
        """名前付きの建物の設計を頼むプロンプトを組み立てる（docs/design/25_builds.md）。"""
        error = (
            f"\n## 前回の設計が使えなかった理由\n{previous_error}\n"
            "理由を直して設計し直してください。\n"
            if previous_error
            else ""
        )
        return BUILD_DESIGN_TEMPLATE.substitute(
            name=character.name,
            personality_traits="、".join(character.personality_traits) or "特になし",
            build_name=name,
            brief=brief,
            home_note=home_note,
            builds_note=f"\n## ほかの建物\n{builds_note}\n" if builds_note else "",
            map_anchor=MAP_ANCHOR_NOTE if map_shown else "",
            max_side=BuildDesign.MAX_SIDE,
            max_height=BuildDesign.MAX_HEIGHT,
            max_blocks=max_blocks,
            max_shapes=BuildDesign.MAX_SHAPES,
            previous_error=error,
        )

    def build_check_prompt(
        self, name: str, brief: str, place_note: str, previous_error: str = ""
    ) -> str:
        """custom(name) の判定のコードを書かせるプロンプト（docs/design/42_custom_conditions.md）。"""
        error = (
            f"\n## 前回の判定が使えなかった理由\n{previous_error}\n"
            "理由を直して書き直してください。\n"
            if previous_error
            else ""
        )
        return CHECK_TEMPLATE.substitute(
            check_name=name, brief=brief, place_note=place_note, previous_error=error
        )

    def build_house_design_prompt(
        self,
        character: CharacterProfile,
        site_note: str = "",
        previous_error: str = "",
        provisional: bool = False,
    ) -> str:
        """LLM に小さな家を設計させるプロンプトを組み立てる。"""
        error = (
            f"\n## 前回の設計が使えなかった理由\n{previous_error}\n"
            "条件を守って設計し直してください。\n"
            if previous_error
            else ""
        )
        return HOUSE_DESIGN_TEMPLATE.substitute(
            name=character.name,
            personality_traits="、".join(character.personality_traits) or "特になし",
            min_side=HouseBlueprint.MIN_SIDE,
            max_side=HouseBlueprint.MAX_SIDE,
            min_height=HouseBlueprint.MIN_WALL_HEIGHT,
            max_height=HouseBlueprint.MAX_WALL_HEIGHT,
            site_note=f"\n## 建てる場所\n{site_note}\n" if site_note else "",
            provisional_note=PROVISIONAL_NOTE if provisional else "",
            previous_error=error,
        )

    def build_site_prompt(
        self,
        character: CharacterProfile,
        mission: Mission,
        sites: Sequence[dict[str, Any]],
        previous_error: str = "",
    ) -> str:
        """調べた候補地の表から、街の場所を 1 か所選ばせるプロンプトを組み立てる。"""
        return SITE_TEMPLATE.substitute(
            name=character.name,
            personality_traits="、".join(character.personality_traits) or "特になし",
            mission=mission.text,
            sites="\n".join(f"- {s['id']}: {format_site_facts(s)}" for s in sites),
            theme=NAMING_THEME,
            previous_error=_retry(previous_error, "選び直してください。"),
        )

    def describe_site(self, site: TownSite, facts: dict[str, Any]) -> str:
        """選んだ街の場所の説明（決めたことと、そこの数字）。"""
        lines = [f"街「{site.name}」の場所。{format_site_choice(site, facts.get('stand_y') if facts else None)}"]
        if facts:
            lines.append(f"そこの地形: {format_site_facts(facts)}")
        return "\n".join(lines)

    def build_town_prompt(
        self,
        character: CharacterProfile,
        mission: Mission,
        site: TownSite,
        facts: dict[str, Any],
        previous_error: str = "",
    ) -> str:
        """最終目標の街とは何かを、段階に分けて答えさせるプロンプトを組み立てる。"""
        return TOWN_TEMPLATE.substitute(
            name=character.name,
            mission=mission.text,
            site=self.describe_site(site, facts),
            abilities=ABILITIES,
            conditions=format_conditions(),
            max_stages=MAX_TOWN_STAGES,
            previous_error=_retry(previous_error, "定義し直してください。"),
        )

    def build_stage_prompt(
        self, town: TownDefinition, stage: TownStage, previous_error: str = ""
    ) -> str:
        """段階のまだ判定できない部分を、今の条件で書き直させるプロンプトを組み立てる。"""
        return STAGE_TEMPLATE.substitute(
            text=town.text,
            title=stage.title,
            why=stage.why,
            unresolved="\n".join(f"- {u}" for u in stage.unresolved),
            conditions=format_conditions(),
            current=", ".join(c.describe() for c in stage.conditions) or "なし",
            abilities=ABILITIES,
            previous_error=_retry(previous_error, "書き直してください。"),
        )

    def build_town_map_prompt(
        self,
        town: TownDefinition,
        town_stage: int,
        zones: list[dict[str, Any]],
        base: list[Zone],
        current: Optional[TownMap],
        reason: str,
        previous_error: str = "",
    ) -> str:
        """街の理想図を描かせる・描き直させるプロンプト（docs/design/47 §6）。"""
        rows = {str(r["id"]): r for r in zones}
        letters = {z.id: _zone_letter(z, rows.get(z.id, {})) for z in base}
        fixed = [
            f"- {z.id}: {z.use}（{z.facility or z.note}）"
            for z in base
            if z.fixed and z.use != "road"
        ]
        roads = [z.id for z in base if z.use == "road"]
        if roads:
            fixed.append(f"- 道（{len(roads)} 区画）: {', '.join(roads)}")
        terrain = [f"- {z.id}: {_terrain(rows.get(z.id, {}))}" for z in base if not z.fixed]
        stages = "\n".join(
            f"{i + 1}. [{'済' if i < town_stage else '今' if i == town_stage else '先'}] "
            f"{st.title}: {st.why}"
            for i, st in enumerate(town.stages)
        )
        now = ""
        if current is not None:
            planned = [
                f"- {z.id}: {z.use}" + (f"（{z.facility}）" if z.facility else "")
                + (f" {z.note}" if z.note else "")
                for z in current.zones
                if not z.fixed and z.use != "free"
            ]
            now = (
                f"\n## 今の理想図（描き直す。残す区画もすべて書く）\n考え: {current.concept}\n"
                + ("\n".join(planned) or "- 割り当てなし")
                + "\n"
            )
        return TOWN_MAP_TEMPLATE.substitute(
            text=town.text,
            stages=stages,
            grid=_zone_grid(letters, [z.id for z in base]),
            fixed="\n".join(fixed) or "- なし",
            removable=_removable_section(base),
            clear_rule=CLEAR_RULE if removable_facilities(base) else "",
            terrain="\n".join(terrain) or "- なし",
            current=now,
            reason=reason,
            previous_error=_retry(previous_error, "描き直してください。"),
        )

    def build_naming_prompt(self, things: list[dict[str, str]], taken: list[str]) -> str:
        """建物・施設に呼び名をつけさせるプロンプト（設計書 160）。"""
        return NAMING_TEMPLATE.substitute(
            theme=NAMING_THEME,
            things="\n".join(f"- {t['name']}: {t['what']}" for t in things),
            taken="、".join(taken) or "なし",
        )

    def build_facility_prompt(
        self,
        name: str,
        brief: str,
        town_map: Optional[TownMap],
        zones: list[dict[str, Any]],
        planned: list[str],
        previous_error: str = "",
    ) -> str:
        """施設（畑・植林場）を設計させるプロンプト（docs/design/47 §7）。"""
        if town_map is None:
            where = "\n## 場所\n街の理想図はまだない。家のまわりの平らな地面に作る（zones は書かない）。\n"
        else:
            rows = {str(r["id"]): r for r in zones}
            letters = {z.id: _zone_letter(z, rows.get(z.id, {})) for z in town_map.zones}
            for z in town_map.zones:
                if not z.fixed and z.use != "free":
                    letters[z.id] = _GRID_FIXED.get(z.use, "B").lower()
            usable = [
                z
                for z in town_map.zones
                if not z.fixed
                and z.use in ("free", "farm", "tree_farm")
                and (not z.facility or z.facility == name)
            ]
            choices = [
                f"- {z.id}: {_terrain(rows.get(z.id, {}))}"
                + (f"（予定: {z.use} {z.facility} {z.note}）".replace("  ", " ") if z.facility else "")
                for z in usable
            ]
            plan = (
                f"理想図ではこの施設の区画は {', '.join(planned)}。特に理由がなければそこに作る。"
                if planned
                else "理想図にこの施設の予定はない。空いた区画から選ぶ。"
            )
            where = (
                "\n## 場所（街の理想図。区画は 8 ブロック四方、列 A〜L が西→東、行 1〜12 が北→南）\n"
                f"考え: {town_map.concept}\n"
                f"{_zone_grid(letters, [z.id for z in town_map.zones])}\n"
                "凡例: 大文字は今あるもの（H 家、B 建物、F 畑、W 植林場、M 採掘場）、小文字は予定、"
                "~ 水、! 溶岩、. 平らな空き、^ でこぼこ、T 木が多い、? まだ見ていない\n"
                f"{plan}\n"
                "zones は隣り合って 1 つの四角になる区画（1〜4 個）。選べる区画と地形"
                "（平ら = 高さがそろった列の割合、木・石・水 = 割合、高さ = 家の床からの差）:\n"
                + ("\n".join(choices) or "- なし")
                + "\n"
            )
        return FACILITY_TEMPLATE.substitute(
            name=name,
            brief=brief,
            where=where,
            previous_error=_retry(previous_error, "設計し直してください。"),
        )

    def vocabulary_version(self) -> str:
        """段階のプロンプトに見せる語彙（条件の一覧と、できること）のハッシュ。"""
        text = format_conditions() + ABILITIES
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    def build_plan_prompt(
        self,
        objective: Objective,
        activity: Activity,
        replan_reason: str = "",
        previous_error: str = "",
    ) -> str:
        """大目標の計画（中目標と、その小目標の並び）を書かせるプロンプト（docs/design/50 §18）。"""
        progress = "\n".join(f"  {line}" for line in objective.progress[:12])
        replan = (
            "\n## 前の計画を書き直す理由\n"
            f"{replan_reason}\n"
            "同じやり方を繰り返さない。原因になった小目標の前に要るものを前提に足す、別の入手方法にする、"
            "段階の順を変える。今の計画は「今していること」の中目標に出ている\n"
            if replan_reason
            else ""
        )
        error = (
            f"\n## 前回の出力が使えなかった理由\n{previous_error}\n計画を書き直してください。\n"
            if previous_error
            else ""
        )
        return PLAN_TEMPLATE.substitute(
            objective=f"{objective.describe()}\n理由: {objective.reason or '（なし）'}"
            + (f"\n今の進み具合:\n{progress}" if progress else ""),
            activity=format_activity(activity),
            town_map=format_town_map(activity.town_map),
            replan=replan,
            conditions=format_conditions(),
            previous_error=error,
        )

    def build_goal_system(self) -> str:
        """目標の決定の決まり（毎回同じ文。システム指示に渡す）。"""
        return GOAL_SYSTEM_TEMPLATE.substitute(
            conditions=format_conditions(), all_predicates=format_predicates(list(GoalPredicate))
        )

    def build_goal_prompt(
        self,
        blueprint: HouseBlueprint | None,
        activity: Activity,
        goal_ended_because: str,
        recent_messages: Sequence[ConversationMessage],
        predicates: Sequence[GoalPredicate],
        previous_error: str = "",
        failure_record: Sequence[str] = (),
        offered: Sequence[Candidate] = (),
        retry_allowed: bool = True,
        job_status: Sequence[ConditionStatus] = (),
        death_report: str = "",
        facility_memory: str = "",
    ) -> str:
        """LLM に次の目標を決めさせるプロンプトを組み立てる。"""
        error = (
            f"\n## 前回の出力が使えなかった理由\n{previous_error}\n"
            "使える目標の形で、実行できる目標とリストの編集を選び直してください。\n"
            if previous_error
            else ""
        )
        return GOAL_TEMPLATE.substitute(
            predicates=", ".join(p.value for p in predicates),
            blueprint=_format_blueprint(blueprint, activity.observation),
            activity=format_activity(activity, with_ids=True),
            recent_messages=format_messages(tuple(recent_messages)),
            reason=goal_ended_because or "なし",
            failure=_format_failure(failure_record, offered, retry_allowed) + _format_death(death_report),
            previous_error=error,
            jobs=_format_job_status(job_status),
            town_map=format_town_map(activity.town_map),
            facility_memory=_format_facility_memory(facility_memory),
        )

    def build_advice_prompt(self, question: str, situation: str) -> str:
        """配信者が判断に迷って advisor に聞くプロンプト（docs/design/143）。"""
        return ADVICE_TEMPLATE.substitute(situation=situation, question=question.strip())

    def build_tool_prompt(
        self,
        activity: Activity,
        state: dict[str, Any],
        suggestions: Sequence[Candidate],
        recent_tools: Sequence[ToolOutcome],
        skills: Sequence[SkillInfo] | None = None,
        failing: int = 0,
        stagnant_seconds: float = 0.0,
        stagnant_actions: Sequence[str] = (),
        repeated: Sequence[str] = (),
        repeated_times: int = 0,
        repeated_skill: str = "",
        same_cause: SameCause | None = None,
    ) -> str:
        """配信者に道具を 1 つ選ばせるプロンプトを組み立てる（設計書 21 §6）。"""
        around = state.get("surroundings") or {}
        me = (state.get("self") or {}).get("position") or {}
        mobs = [
            f"#{m['id']} {m['name']}{'（敵）' if m.get('hostile') else ''} {m['distance_m']}m "
            f"{m.get('direction', '')}（高さの差 {m.get('height_diff_m', 0)}）"
            for m in state.get("mobs", [])
        ]
        return TOOL_TEMPLATE.substitute(
            cache_break=CACHE_BREAK,
            activity=format_activity(activity, cache_break=True),
            position=f"{me.get('x')}, {me.get('y')}, {me.get('z')}" if me else "不明",
            legend=around.get("legend", ""),
            grid="\n".join(f"    {row}" for row in around.get("grid", [])) or "    （なし）",
            walls=around.get("walls_around", "?"),
            head="ふさがっている" if around.get("head_blocked") else "空いている",
            standing_on=around.get("standing_on") or "不明",
            mobs="、".join(mobs) or "なし",
            suggestions="\n".join(
                f"- {c.action_id}"
                + (f"（{c.description.get('distance')}m）" if c.description.get("distance") else "")
                for c in suggestions
            )
            or "- なし",
            recent="\n".join(_format_tool(o) for o in recent_tools) or "- なし",
        ) + (
            Template(TOOL_SKILLS_SECTION).substitute(
                skills="\n".join(f"- {s.describe()}" for s in skills) or "- まだない"
            )
            + (TOOL_RAW_SKILLS_LINE if self._raw_bot else "")
            if skills is not None
            else ""
        ) + _refused_section(state.get("refusals")) + _stuck_section(failing, stagnant_seconds, stagnant_actions) + _same_cause_section(same_cause) + _repeat_section(repeated, repeated_times, repeated_skill)

    def build_skill_system(self) -> str:
        """技を書くシステム指示（API と書き方。docs/design/22）。"""
        return SKILL_SYSTEM + SKILL_RAW_BOT if self._raw_bot else SKILL_SYSTEM

    def build_skill_prompt(
        self,
        name: str,
        what: str,
        activity: Activity,
        recent_tools: Sequence[ToolOutcome],
        skills: Sequence[SkillInfo],
        previous: SkillInfo | None = None,
        last_failure: str = "",
        previous_error: str = "",
    ) -> str:
        """技を書く（直す）本文。"""
        prev = ""
        if previous is not None:
            prev = (
                f"\n## 直す前の版（v{previous.version}、成功 {previous.successes}/{previous.uses}）\n"
                f"説明: {previous.description}\nexpects: "
                f"{json.dumps(previous.expects, ensure_ascii=False)}\n```js\n{previous.code}\n```\n"
                f"前の失敗: {last_failure or previous.last_failure or 'なし'}\n"
            )
        error = (
            f"\n## 前に書いたものが受け取られなかった理由\n{previous_error}\n"
            if previous_error
            else ""
        )
        return SKILL_TEMPLATE.substitute(
            name=name,
            what=what,
            activity=format_activity(activity),
            recent="\n".join(_format_tool(o) for o in recent_tools) or "- なし",
            skills="\n".join(f"- {s.describe()}" for s in skills) or "- まだない",
            previous=prev,
            previous_error=error,
        )

    def build_screen_review_prompt(self, activity: Activity) -> str:
        """配信の画面と、配信者が今していることを見比べさせるプロンプト。"""
        return SCREEN_REVIEW_TEMPLATE.substitute(activity=format_activity(activity))

    def build_action_context(
        self, goal: Goal, observation: GameObservation
    ) -> tuple[dict[str, Any], str]:
        """選択器に渡す状態（目標、進み具合、体が必要とするもの、周り）と指示を組み立てる。"""
        s = observation.state
        status = observation.goal
        state = {
            "goal": goal.describe(),
            "goal_reason": goal.reason,
            **({"approach": goal.approach} if goal.approach else {}),
            **({"advice": goal.advice} if goal.advice else {}),
            # 前に似た状況で学んだこと（docs/design/35。確かめていない）
            **({"remember": list(goal.lessons)} if goal.lessons else {}),
            "progress": list(status.lines) if status else [],
            "blocked": list(status.blocked) if status else [],
            "needs": list(observation.needs),
            "self": {
                "health": observation.health,
                "food": observation.food,
                "time": format_time_en(s.get("time", {})),
                "in_home": observation.inside_home,
                "held_item": s.get("self", {}).get("held_item"),
                "equipment": {
                    part: item
                    for part, item in (s.get("self", {}).get("equipment") or {}).items()
                    if item
                },
            },
            "inventory": s.get("inventory", {}),
            "nearby_mobs": [
                {k: m[k] for k in ("name", "hostile", "distance_m") if k in m}
                for m in s.get("mobs", [])[:MOBS_SHOWN]
            ],
            "recent_actions": s.get("recent_actions", [])[-RECENT_ACTIONS_SHOWN:],
            # まわりの目印（ベッド、ドア、作業台…）: 要約した同じ書式を毎回渡す（docs/design/28）
            "nearby": [
                {
                    "kind": n.get("kind"),
                    "count": n.get("count"),
                    "distance_m": (n.get("nearest") or {}).get("distance_m"),
                    "direction": (n.get("nearest") or {}).get("direction"),
                    "owner": n.get("owner") or "not ours",
                }
                for n in s.get("nearby") or []
            ],
        }
        return state, ACTION_INSTRUCTIONS


def _format_job_status(statuses: Sequence[ConditionStatus]) -> str:
    """一番上の大目標の中目標の、今の判定（docs/design/31。見直しの材料）。中目標がなければ空。"""
    if not statuses:
        return ""
    lines = ["", "## 中目標の今の状態（一番上の大目標。見直しの材料）"]
    for st in statuses:
        if st.met:
            lines.append(f"- {st.spec.describe()}: 済み")
            continue
        detail = "; ".join(line.strip() for line in st.lines[:2])
        if st.impossible:
            detail = f"{detail}; " if detail else ""
            detail += "今は手に入らない: " + ", ".join(st.impossible)
        step = f" → 次の一歩: {st.next_step.describe()}" if st.next_step else ""
        lines.append(f"- {st.spec.describe()}: まだ" + (f"（{detail}）" if detail else "") + step)
    return "\n".join(lines) + "\n"


def _format_failure(
    record: Sequence[str], offered: Sequence[Candidate], retry_allowed: bool
) -> str:
    """うまくいかなかった小目標の記録（docs/design/27 §3）。失敗でなければ空。"""
    if not record and not offered:
        return ""
    lines = ["", "## うまくいかなかった小目標の記録（古い順。行動 (確信度) → 結果）"]
    lines += [f"- {line}" for line in record] or ["- なし"]
    lines.append("最後に出ていた候補:")
    lines += [f"- {_format_offered(c)}" for c in offered] or ["- なし"]
    if not retry_allowed:
        lines.append("この小目標は続けて失敗している。やり直しは選べない（change にする）")
    return "\n".join(lines) + "\n"


def _format_death(report: str) -> str:
    """死んだときの様子（docs/design/105）。死んだ直後だけ。教訓を必ず書かせる。"""
    if not report:
        return ""
    return (
        "\n## 死んだときの様子（ブリッジの記録。復活して状況が変わっているので、これだけが死ぬ前の事実）\n"
        f"{report}\n"
        "持ち物は死んだ場所に落ちて 5 分で消える。まず原因を分析（diagnosis）し、同じ死に方をしないために"
        "次からどうするか（いつ・どこで何をやめる／先にする）を、教訓（lesson と lesson_when）に必ず書く。"
        "教訓は一般的な 1 文（座標は書かない）で、似た状況で思い出される\n"
    )


def _format_offered(c: Candidate) -> str:
    d = c.description
    facts = [
        f"{d['distance']}m" if d.get("distance") is not None else "",
        "行ったことがある方角" if d.get("been_there") else "",
        f"{d['seen_minutes_ago']} 分前に見た" if d.get("seen_minutes_ago") is not None else "",
    ]
    facts = [f for f in facts if f]
    return c.action_id + (f"（{'、'.join(facts)}）" if facts else "")


def _format_facility_memory(memory: str) -> str:
    """目標の決定に見せる「作った施設」の節（空なら節ごと出さない）。"""
    if not memory:
        return ""
    return (
        "\n## 作った施設（記憶。大目標のリストか家が変わった）\n"
        f"{memory}\n"
        "大目標の完了条件の施設が、ここにある同じ種類の完成した施設（家の近く）で済んでいるなら、"
        "同じ種類をもう 1 つ作らずに satisfy（id と by）で済みにする。\n"
    )


def _format_blueprint(b: HouseBlueprint | None, obs: GameObservation | None) -> str:
    if b is None:
        return "なし（前に建てた家が完成していて、拠点になっている）"
    materials = b.material_counts()
    counts = ", ".join(f"{k.value} {v}" for k, v in materials.items())
    planks = next((v for k, v in materials.items() if k.value == "planks"), 0)
    if planks:
        # 板材の数を原木の数と取り違えて have(log, 71) を決めた（原木 18 本で足りる: 2026-10-09）
        counts += f"。planks {planks} は原木 {-(-planks // 4)} 本ぶん（原木 1 本で 4 枚）"
    size = f"{b.width}x{b.depth}、壁の高さ {b.wall_height}"
    build = obs.state.get("build") if obs else None
    if obs is not None and obs.house_complete:
        progress = "完成済み"
    elif build:
        site = "建設地は決定済み" if build.get("origin") else "建設地は未決定"
        progress = f"{build['placed']}/{build['total']} ブロック設置済み、{site}"
        if build.get("foundation"):
            progress += f"、足元の地面が抜けていて土があと {build['foundation']} ブロック要る"
    else:
        progress = "未着手"
    return f"「{b.name}」{size}（必要ブロック: {counts}）- {b.concept}\n進み具合: {progress}"


def _retry(previous_error: str, ask: str) -> str:
    return f"\n## 前回の答えが使えなかった理由\n{previous_error}\n{ask}\n" if previous_error else ""


TOOL_RESULT_CHARS = 300
ADVICE_RESULT_CHARS = 800  # advisor の答えは調べものの結果より長く、切ると読めなくなる


def _format_tool(o: ToolOutcome) -> str:
    """直近の道具 1 つ: 呼び出し、意図、結果（長い調べものの結果は切る）。"""
    status = "断られた" if o.refused else "成功" if o.ok else "失敗"
    stopped = f"（見張り: {o.stopped_by}）" if o.stopped_by else ""
    intent = f"「{o.call.intent}」" if o.call.intent else ""
    limit = ADVICE_RESULT_CHARS if o.call.name == ASK_ADVISOR else TOOL_RESULT_CHARS
    result = o.result if len(o.result) <= limit else o.result[:limit] + "…"
    return f"- {o.call.describe()}{intent}: {status}{stopped} {result}"
