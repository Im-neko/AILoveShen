"""テンプレートでゲームのプロンプトを組み立てるアダプター。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from string import Template
from typing import Any, Optional

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.use_cases.goal_vocabulary import MAX_TOWN_STAGES
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

## ヒント
- 配信で完成まで見せられる大きさが良い
- あなたらしさが伝わる名前とコンセプトにする
$previous_error
## 出力
指定された JSON だけを出力してください。
""")

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
  - hollow_box: 外殻だけ（床・壁・屋根の 6 面）。中は触らないので、部屋の中は clear で空ける
  - clear: 空気にする（入口、窓、部屋の中）
  - door: ドア（from が下のマス。上のマスも自動でドアになる）。壁の中に置く
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
Minecraft のサバイバル（1.21.4）で、中目標の完了を世界から判定するコードを書きます。
判定の名前: $check_name

## 中目標（頼まれたこと）
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
  - w.block(x, y, z) → { name, solid, props? } か null（読み込まれていない）。name は "oak_log" など
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

SITE_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者「$name」（性格: $personality_traits）です。
配信の大目標は「$mission」です。街を作る場所を決めるため、最初の家のまわりの候補地を
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
$previous_error
## 出力
指定の JSON で出力してください。
""")

TOWN_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者「$name」の方針を決めます。
配信の大目標は「$mission」です。この「街」とは何か、いつ完成したと言えるかを定義してください。
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
凡例: H 家、B 建物、F 畑、W 植林場、M 採掘場、P 囲い、X トラップ（今あるもの。固定）、~ 水、
! 溶岩（使えない。固定）、. 平らな空き、^ でこぼこ、T 木が多い、? まだ見ていない

## 固定の区画（変えない。areas に入れない）
$fixed

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
- 道は要るなら施設をつなぐ細い列。全部は埋めない（空きを残す）
- 固定の区画は変えない。まだ見ていない区画にも割り当ててよい
$previous_error
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
  全部のマスを耕して種をまくと完成。区画の平らな地面に収まる大きさにする（1 区画は 8 x 8）
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

_GRID_FIXED = {
    "home": "H",
    "building": "B",
    "farm": "F",
    "tree_farm": "W",
    "mine": "M",
    "pen": "P",
    "trap": "X",
}


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
    return "\n".join(lines) + "\n"


STAGE_TEMPLATE = Template("""\
配信の大目標の「街」は、次のように定義してある:
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
目標は 3 層: 大目標（変わらない）、中目標（上から順に取り組むリスト）、小目標（今の 1 つ）。
決めるのは次の小目標と、見直しに応じた中目標リスト・手順・自分のメモの編集です。

## 方針: 効率を先に
- 大目標に直接関係しなくても、先に作ると後が速く・安全になるもの（良い道具・防具、畑、植林場、
  備蓄、明かり、作業台やかまどの置き場）は、要るなら中目標に足して先にやってよい（大目標への近道）
- 同じ物を何度も遠くへ取りに行く、同じ作業を繰り返す、待ち時間ができた、は効率化の合図
- 目安は、作る手間がこの先それで浮く手間より小さいこと。理由は review に書く（配信で伝わる）

## 見直し（review。毎回、最初に）
今の状態（持ち物、チェスト、目の前の物、近くにあるもの、覚えている場所、時間帯、「手順の今の
状態」）から、もっと早く・無駄なく進むやり方を探す。確かめること:
- 済んだ手順、持っている物・チェストにある物の手順は飛ばす（集め直さない）
- 目の前の材料と落ちている物を先に使う（遠くへ探しに行くのはそのあと）
- 同じ外出で取れるものは束（together）にする（ほかの中目標の物も。木を見つけたら原木と、葉から
  苗木）。合わなくなった束（片方だけ遠い、順番が要る）はほどく。集めた物を 1 個ずつ家に運ばない
- 先に作ると速くなる道具（石のツルハシなど）は先に作る
- 何度も要る物・近くにない物は増やす仕組みを作る（木は植林場、食べ物は畑: facility で作る・別の名前で増やす）
- 待つ時間（作物が育つ間、夜、家の中）は、そばでできること（畑の空きを埋める、クラフト、置く、しまう）に使う
- 今は手に入らない手順には、手に入るようにする手順を前に入れるか、中目標を後ろに回す
見つけたら plan_changes / steps を直し、何をどう変えたかを review に書く。今のままが一番よければ
変えずに理由を書く（変えないのも答え。理由のない入れ替えはしない）

## 小目標の決め方
- 小目標は、編集したあとのリストの一番上の中目標を進めるもの（serves: current）。別の中目標を今
  やるなら move で一番上にする。ほかの中目標の物をついでに取るなら together に入れる
- 例外は身を守る小目標（through_night、at_home、cleared、have(food, n)）で、serves: survival なら
  中目標に関係なく選べる（夜や空腹は待ってくれない）
- 食べ物を探すときも have(food, n) を選ぶ。近くになければ、畑や狩りなど手に入れる方法を考える
- 中目標は「何ができた状態か」（数十分）、小目標はその手前の「次の一歩」（数分）。同じ条件にするのは
  材料がそろって仕上げだけのとき（配信の画面に 2 つ並ぶので、同じ文では伝わらない）。例:
  家を建てる (built) → have(log, 12) → have(planks, 40) → built、
  食料を蓄える (stored(food, 16)) → have(food, 8) → stored(food, 16)
- 分解（ベッドには羊毛 3・板材 3・作業台）と細かい操作（何を掘る・どこへ行く）は自動。小目標は
  下の述語の形で出す（達成はゲームの状態から判定）
- 夜は地上が危険。近ければ家、遠ければ近くのベッドか持っているベッドを置いて寝る（そこが復活地点）、
  地下ならそのまま作業。夜に家の中にいる間は、中でできること（クラフトなど）を選んでよい。
  視聴者に「今やる」と言った頼み（一番上）は夜でも選ぶ
- 石・石炭・鉄が地表にないときは階段を掘って下りる（深さの上限なし。溶岩・水・砂利・岩盤で止まる）。
  下りたくないときだけ dig_depth（0: 下りない、n: 今の足元から n 段まで）
- 近くの敵への対処と、空腹のときに食べるのは選ばなくても行われる
- 配信での自分の発言・視聴者との約束と食い違わない。「pointed out in chat」で考え直すときは、
  返事で約束したことを今やるか中目標に足す。しないなら reason に理由を書く（黙って戻らない）

## 中目標の手順（steps）
- 一番上の中目標の手順を、小目標の並び（1〜6 個、順番どおり）で書く。次にどれをやるかは速いモデルが
  選び、済んだかはゲームの状態で判定される
- 書くのは、手順がない（「今していること」の中目標に「手順:」がない）、うまくいっていない、見直しで
  もっとよい手順が見つかったとき。それ以外は空にする
- 手順は完了条件の述語で書き、各手順に理由を 1 文付ける（配信に出る）。最後は中目標の完了条件そのもの
- 束: together で 4 つまで同時に追える（全部そろって終わり）。同じ外出・場所・道具で取れるものを
  まとめ（例: 森へ → have(log, 16) と planted(sapling, 4)）、approach に回り方を 1 文。順番が要る
  もの（作業台 → ツルハシ）は別の手順。束の手順を小目標にするときは together と approach も書く

## 中目標リストの編集（plan_changes。変える理由があるときだけ）
- 中目標は大目標に向かう段階と、そのための基盤（上の方針）。完了はゲームの状態で判定される
  （完了を宣言しない）
- 完了条件に使えるのは次だけ:
$conditions
- 蓄える中目標は stored で表す（have は持った時点で完了し、使うと減る）
- add: 要るのにリストにないもの（題名、完了条件、位置、理由）
- move: 順番を変える（1 が今取り組むもの）。今の場所・持ち物・時間帯で先にやるほうが早い・安全
  なとき（視聴者の頼みも）。前の判断を覆すなら、何が変わったかを理由に書く
- drop: やめる（理由は配信で伝わる）。視聴者の頼みは簡単にやめない
- 街の段階は、前の段階が終わると自動で一番上に入る（自分で足さない）。街の段階の中目標はやめられない
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
    中目標の立て方にあるなら、リストや手順も直す
- 同じ小目標が続けて失敗しているときは、やり直しは選べない（本文に書いてある）
- 分析と助言は配信に出る。記録にない事実を作らない
- 似た状況で役立つことが分かったら lesson（一般的な教訓 1 文、座標は書かない）と lesson_when
  （どんなときか）を書く（教訓帳に残る）。「思い出したこと」を守れていなかったなら、それも分析に書く

## 小目標の述語（全部。今使えるものは状態の側に書く）
$all_predicates

## 出力
見直し（review）、trips、中目標リストとメモの編集（なければ空）、次の小目標（predicate と必要な
引数）、serves、その理由（短い 1 文）を指定の JSON で出力してください。
""")

GOAL_TEMPLATE = Template("""\
## 建てる家
$blueprint

## 今していること
$activity
$town_map
## 今使える小目標
$predicates
$steps

## 最近の会話（配信での自分の発言と視聴者のコメント）
$recent_messages

## 小目標を選び直す理由
$reason
$failure$previous_error
""")

# spikes/primitive_choice_eval.py で測った: 体が必要とするもの（needs）を状態に書くと効いた。
# 指示に優先順位を書くと、選択器は 20m 先の敵からも逃げるようになった。
TOOL_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者として、自分の手で操作しています。
今の小目標に向けて、次に呼ぶ道具を 1 つ選んでください。

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

## 決まり
- 行動の道具には intent（今やろうとしていること、1 文）を必ず書く。配信で見える
- 見張りの質問（watch）は、行動の途中で起きうることを確かめたいときだけ添える。英語で、状態から
  答えられることを中立に書く（急ぎや優先の言葉は書かない）。完了の判定には使われない
  （完了はワールドで確かめる）
- 同じ失敗を繰り返さない。断られた・失敗した理由を読んで、別の手を選ぶ
- 「目の前のブロック」「目の前に落ちている物」に要る物があれば、遠くへ探しに行く前にそれを使う
  （一番近いものの座標をそのまま dig などに使える）
- 分からないことは検索の道具で調べてから動く（すぐ返る。1 ステップ 5 回まで）。検索語は日本語でも
  英語でもよく、名前でも目的でもよい。返った座標や名前をそのまま使う
  - search_abilities: やり方が分からない・初めてのこと（例: 畑を作る、水を渡る、肉を焼く）
  - item_info: 物の取り方・要る道具・使い道（例: 鉄、iron_ingot）。uses_of: その素材で作れる物
  - search_facility: 自分の施設（畑、植林場、建物）の場所と中身。行く・使う前に
  - search_memory: 前に見た場所（鉱石、木、動物、チェストの中身）。遠くへ探しに行く前に
- 夜に家の中にいるときは、外に出る道具は断られる
- 座標は整数。y は足元の高さ
- look_screen（使えるときだけ出ている）は配信の画面を撮り、次の選択に画像として添える。
  文字の状態で分からないとき（地形、何が起きているか）に使う。画像で完了は決めない
- 画像が添えてあれば、それはいま配信に映っている自分の視点
""")

# 技（docs/design/22）: 道具の選択に足す節（技を使うときだけ）
TOOL_SKILLS_SECTION = """
## 覚えた技（run_skill で呼ぶ。今の小目標に合うものが先）
$skills
- 技は道具をいくつも呼ぶ手順で、1 回の呼び出しで最後まで動く。合う技があれば、道具を 1 つずつ
  呼ぶより先に使う
- 同じ道具の並びを何度も呼んでいるとき、手順にしたほうが確かなときは write_skill で技にする
- 技が失敗したら理由を読む。直せそうなら同じ名前で write_skill（what に何を直すか）、合わない場面
  なら道具で進める
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
  t.plant({item?, x?, y?, z?}), t.till({x?, y?, z?}), t.sow({item?, x?, y?, z?}), t.wait()
  （plant / till / sow は場所を省くとブリッジが家のまわりから選ぶ）
- 調べもの: t.find_blocks({block, radius?}) → { ok, result: [{x, y, z, distance_m, direction, exposed}] か文 },
  t.recipe_of({item}), t.find_recipes({query, limit?}), t.how_to_get({item, count?}),
  t.state() → { self: {position, health, food, in_home, ...}, inventory: {名前: 数}, mobs: [{id, name,
  hostile, distance_m, direction}], surroundings, needs, time, action },
  t.block_at({x, y, z}) → { name }
- 判定: t.judge(question, {kind: 'yes_no' | 'choice', options?}) → { answer, confidence }（速い判断
  モデルが今の状態を見て答える。答えがなければ answer: null）
- 記録: t.log(message)（失敗したときに読み返す）
- ないもの: require、fetch、process、setTimeout、ファイル。ソルバーの提案（do_suggestion）も使えない

## 決まり
- 座標を決め打ちしない。t.state() や t.find_blocks() で探すか、引数にする（技は別の場所・別の
  ワールドでも使う）
- 行動の結果の ok を確かめ、だめなら理由を t.log して別の手か t.fail(理由) にする
- ループには上限を付ける。上限: 1 回に行動 40 回、調べもの 200 回、judge 10 回（時間の上限はない）。
  await せずに回るループは止められる
- 行動を同時に呼ばない（Promise.all で行動を並べない。1 つずつ）
- 安全の制約（夜に家の外へ出ない、家を壊さない）は道具の側にあり、断られる。回り道をしない
- 1 つの技は 1 つのこと（狩る、木を切って板材にする、家のまわりに松明を置く）。大きな目標は小さな
  技の組み合わせで進める
- expects: 技が成功したときに世界で成り立つこと。have / stored（item と count。count は数、
  "+N"（始めより N 多い）、"$引数名"）、planted / farmed（item と count。"+N" も）、built（name）、
  placed（where: home）、lit（distance）、
  progress（今の小目標の残りの作業が減る）。done() だけでは成功にならない
- 直すとき: 前のコードと失敗の記録（理由、log、最後の道具）を読み、原因を直す。同じ失敗を
  繰り返さない
- コードは 4000 字まで。説明は日本語 1 文
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
RECENT_ACTIONS_SHOWN = 3


class GamePromptTemplateBuilder(IGamePromptBuilder):
    """
    ゲームのプロンプトのインフラ側アダプター。

    IGamePromptBuilder を文字列のテンプレートで実装する。LLM へのプロンプトは
    他のプロンプトと同じく日本語。行動の選択器には英語を渡す（評価したときの言語）。
    """

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
        self, character: CharacterProfile, site_note: str = "", previous_error: str = ""
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
            previous_error=_retry(previous_error, "選び直してください。"),
        )

    def describe_site(self, site: TownSite, facts: dict[str, Any]) -> str:
        """選んだ街の場所の説明（決めたことと、そこの数字）。"""
        lines = [f"街「{site.name}」の場所。{format_site_choice(site)}"]
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
        """大目標の街とは何かを、段階に分けて答えさせるプロンプトを組み立てる。"""
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
            f"- {z.id}: {z.use}（{z.facility or z.note}）" for z in base if z.fixed
        ]
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
            terrain="\n".join(terrain) or "- なし",
            current=now,
            reason=reason,
            previous_error=_retry(previous_error, "描き直してください。"),
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
        step_status: Sequence[ConditionStatus] = (),
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
            failure=_format_failure(failure_record, offered, retry_allowed),
            previous_error=error,
            steps=_format_step_status(step_status),
            town_map=format_town_map(activity.town_map),
        )

    def build_tool_prompt(
        self,
        activity: Activity,
        state: dict[str, Any],
        suggestions: Sequence[Candidate],
        recent_tools: Sequence[ToolOutcome],
        skills: Sequence[SkillInfo] | None = None,
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
            activity=format_activity(activity),
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
            if skills is not None
            else ""
        )

    def build_skill_system(self) -> str:
        """技を書くシステム指示（API と書き方。docs/design/22）。"""
        return SKILL_SYSTEM

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


def _format_step_status(statuses: Sequence[ConditionStatus]) -> str:
    """一番上の中目標の手順の、今の判定（docs/design/31。見直しの材料）。手順がなければ空。"""
    if not statuses:
        return ""
    lines = ["", "## 手順の今の状態（一番上の中目標。見直しの材料）"]
    for st in statuses:
        if st.met:
            lines.append(f"- {st.spec.describe()}: 済み")
            continue
        detail = "; ".join(line.strip() for line in st.lines[:2])
        if st.impossible:
            detail = f"{detail}; " if detail else ""
            detail += "今は手に入らない: " + ", ".join(st.impossible)
        lines.append(f"- {st.spec.describe()}: まだ" + (f"（{detail}）" if detail else ""))
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


def _format_offered(c: Candidate) -> str:
    d = c.description
    facts = [
        f"{d['distance']}m" if d.get("distance") is not None else "",
        "行ったことがある方角" if d.get("been_there") else "",
        f"{d['seen_minutes_ago']} 分前に見た" if d.get("seen_minutes_ago") is not None else "",
    ]
    facts = [f for f in facts if f]
    return c.action_id + (f"（{'、'.join(facts)}）" if facts else "")


def _format_blueprint(b: HouseBlueprint | None, obs: GameObservation | None) -> str:
    if b is None:
        return "なし（前に建てた家が完成していて、拠点になっている）"
    counts = ", ".join(f"{k.value} {v}" for k, v in b.material_counts().items())
    size = f"{b.width}x{b.depth}、壁の高さ {b.wall_height}"
    build = obs.state.get("build") if obs else None
    if obs is not None and obs.house_complete:
        progress = "完成済み"
    elif build:
        site = "建設地は決定済み" if build.get("origin") else "建設地は未決定"
        progress = f"{build['placed']}/{build['total']} ブロック設置済み、{site}"
    else:
        progress = "未着手"
    return f"「{b.name}」{size}（必要ブロック: {counts}）- {b.concept}\n進み具合: {progress}"


def _retry(previous_error: str, ask: str) -> str:
    return f"\n## 前回の答えが使えなかった理由\n{previous_error}\n{ask}\n" if previous_error else ""


TOOL_RESULT_CHARS = 300


def _format_tool(o: ToolOutcome) -> str:
    """直近の道具 1 つ: 呼び出し、意図、結果（長い調べものの結果は切る）。"""
    status = "断られた" if o.refused else "成功" if o.ok else "失敗"
    stopped = f"（見張り: {o.stopped_by}）" if o.stopped_by else ""
    intent = f"「{o.call.intent}」" if o.call.intent else ""
    result = o.result if len(o.result) <= TOOL_RESULT_CHARS else o.result[:TOOL_RESULT_CHARS] + "…"
    return f"- {o.call.describe()}{intent}: {status}{stopped} {result}"
