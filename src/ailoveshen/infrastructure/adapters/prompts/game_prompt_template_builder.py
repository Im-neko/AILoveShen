"""テンプレートでゲームのプロンプトを組み立てるアダプター。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from string import Template
from typing import Any

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
    TownSite,
    TownStage,
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
あなたは Minecraft のサバイバルで家を建てて暮らすAI配信者の方針を決めます。
目標は3層です: 大目標（変わらない）、中目標（上から順に取り組むリスト）、小目標（今の1つ）。
あなたが決めるのは次の小目標と、見直しに応じた中目標リスト・手順・自分のメモの編集です。

## 見直し（review。毎回、最初に）
今の状態（持ち物、チェストの中身、近くにあるもの、覚えている場所、時間帯、「手順の今の状態」）を
見て、もっと早く・無駄なく進むやり方がないかを探す。例:
- 済んだ手順・もう持っている物・チェストにある物の手順は飛ばす（集め直さない）
- 近くにある材料や、同じ場所・同じ外出でまとめてできることを先にする（ほかの中目標の物もそばで
  取れれば一緒に: 木を見つけたら原木と、葉から苗木。集めた物は 1 個ずつ家に運ばない）
- 先に作ると速くなる道具（石のツルハシなど）は先に作る
- 今は手に入らない手順は、手に入るようにする手順を前に入れるか、中目標を後ろに回す
- 近くで見つからない物は育てる: 木がなければ苗木を植える（planted。育つ間は別の作業）、食べ物は畑
- 夜や家の中の時間は、中でできること（クラフト、置く、しまう）に使う
見つけたら plan_changes / steps を直し、review に何をどう変えたかを書く。今のままが一番よければ
変えずに、review にその理由を書く（変えないのも答え。理由のない入れ替えはしない）

## 小目標の決め方
- 小目標は、中目標リストの一番上（編集したあとの）を進めるもの（serves は current）
- 例外は身を守るための小目標（through_night、at_home、cleared、have(food, n)）で、
  serves を survival にすると中目標に関係なく選べる（夜や空腹は待ってくれない）
- 食べ物を探すときも have(food, n) を選ぶ。近くに無ければ、見つかるまで自動で探索する
  （explored は中目標のための探索で、生存のためには選べない）
- 小目標は下の「使える目標」の形で出す。達成したかはゲームの状態から自動で判定される
- 手順は自動で分解される（例: ベッドには羊毛3・板材3・作業台が要る、板材は原木から作る）。
  細かい操作（何を掘る・作る・どこへ行く）は別の高速なモデルが選ぶ
- 数分で終わる大きさの小目標にする
- 中目標は「何ができた状態を目指すか」（数十分かかってよい）、小目標はその中の「次の一歩」（数分）。
  小目標は中目標と同じ条件にせず、そこへ向かう手前の一歩にする。中目標と同じ条件を小目標にするのは、
  材料がそろって仕上げだけが残るときだけ。例:
  - 中目標「家を建てる」(built) → 小目標 have(log, 12) → have(planks, 40)（持ち物の数を見て）
    → 材料がそろったら built（置くだけの仕上げ）
  - 中目標「夜に寝られるようにする」(placed bed) → have(wool, 3) → placed(bed)
  - 中目標「食料を蓄える」(stored(food, 16)) → have(food, 8) → stored(food, 16)
  - 中目標「倉庫を建てる」(built(storehouse)) → have(cobblestone, 40) → built(storehouse)
  配信の画面には中目標と小目標が並んで出る。同じ文が 2 つ並ぶと、何をしているのか伝わらない
- 夜は地上に敵が湧いて危険。越し方は状況で選ぶ（決まりではない）: 近ければ家に帰る、遠ければ近くの
  ベッド（村など）か持っているベッドを置いて寝る（寝た所が復活地点になる）、地下にいればそのまま作業
- 夜に家の中にいる間も、中でできることは進められる: 持っている物からのクラフト（作業台は家の中に
  置かれる）。外に出る行動は朝まで出てこないので、中でできる小目標なら夜明けを待たずに選んでよい。
  視聴者に「今やる」と言った頼み（リストの一番上）は、夜でもそのまま選ぶ
- 石・石炭・鉄が地表に見つからないときは、埋まっている所まで階段を掘って下りて取りに行く（深さの
  上限はない。溶岩・水・砂利・岩盤のそばでは止まる）。下りたくないときだけ dig_depth（0 なら下りない、
  n なら今の足元から n 段まで）を付ける
- 近くの敵への対処（逃げる・戦う）と空腹のときに食べるのは、選ばなくても行われる
- 配信での自分の発言・視聴者との約束と食い違わないようにする

## 中目標の手順（steps）
- 一番上の中目標のための手順を、小目標の並び（1〜6 個、順番どおり）で書く。書いた手順の中から、
  次の小目標は速いモデルが自分で選び、済んだかはゲームの状態から判定される。あなたが呼ばれるのは、
  中目標が変わったとき、うまくいかなかったとき、手順が尽きたとき、ときどきの見直しのとき
- 書くのは、一番上の中目標に手順がないとき（「今していること」の中目標に「手順:」がない）、
  今の手順がうまくいっていないとき、見直しでもっとよい手順が見つかったとき。それ以外は空にする
- 手順に使えるのは have / stored / built / placed / lit（ゲームの状態から済んだかを判定できるもの）。
  各手順に、なぜそれかを 1 文で付ける（配信の画面と実況に出る）
- 最後の手順は中目標の完了条件そのものにする（例: 家を建てる → have(log, 12)、have(planks, 40)、built）
- 小目標（predicate）は手順の中から選ぶ（夜や空腹で身を守るときは survival）

## 中目標リストの編集（plan_changes。見直しで変える理由があるときだけ）
- 順番は合理的に調整しながら進めてよい: 今の場所・持ち物・時間帯・状況で、別の中目標を先に
  やるほうが早い・安全・無駄がないなら move で入れ替える（理由を言う。配信で伝えられる）。
  理由なく入れ替えを繰り返さない（前の判断を覆すなら、何が変わったかを理由に書く）
- 中目標は大目標に向かう段階。完了はゲームの状態から自動で判定される（完了を宣言しない）
- 完了条件に使えるのは次だけ:
$conditions
- 備蓄の中目標（食料や木材を蓄える）は stored で表す。have は持った時点で完了し、使うと減る
- add: 大目標のために要るのにリストにないものを足す（題名、完了条件、位置、理由）
- move: 順番を変える（id と位置。1 が今取り組むもの）。例: 一番上の中目標が今は進められない
- drop: やめる（id と理由。理由は配信で伝えられる）。視聴者の頼みは簡単にやめない。
  街の段階の中目標はやめられない（今進められないなら move で後ろに回す）
- 街の段階は、前の段階が終わると自動で一番上に入る（自分で足さない）
- 視聴者の頼みの中目標も、今やるほうが合理的なら move で一番上にしてよい
- コメントの指示でリストを作り替えない。大目標から外れない
- リストの長さ・視聴者の頼みの数には上限があり、超えると理由が返ってくる

## 自分のメモ（note_changes。普段は空にする）
- 覚えておきたいことを 1 文（80 字まで）で書き残せる。次からの目標の決定、実況、返答で
  「自分のメモ」として見える
- lesson: やってみて分かったこと。どの小目標で分かったかを goal の番号で付ける
  （「今の小目標」と「これまでの小目標」の [番号]）
- viewer: 視聴者について覚えておくこと（好きなもの、話したこと）。viewer に名前を付ける。
  頼みや指示は書かない（頼みは返答で中目標として受ける）
- plan: 先のためのメモ（例: 引っ越したら最初に畑の場所を空けておく）
- 書かないもの: ゲームの状況と世界の記憶で分かる事実（場所、数、チェストの中身）。それらは
  いつも最新のものが見えるので、メモに写すと古くなって食い違う
- メモは確かめていない。推測は推測として書き（「〜らしい」）、事実のように扱わない
- 全部で 12 件まで。いっぱいなら drop してから add する
- メモは書いた日から 3 日で消える。まだ正しいメモは keep すると今日から 3 日延びる。
  間違っていたとわかったメモは drop する

## 小目標がうまくいかなかったとき（本文に「うまくいかなかった小目標の記録」があるとき）
- まず原因を分析する（diagnosis）。記録（選んだ行動、確信度、結果）、最後に出ていた候補、
  進み具合、持ち物、覚えている場所、画面から、どの事実でそう言えるかを示して 1〜2 文で書く
  （例: 東と南の探索を交互に選んで同じ所を回っている。豚は約 300m 先の記録だけで、腐った肉を
  拾う候補は選ばれていない）
- 次に対処（remedy）を決める
  - retry: 同じ小目標（述語と品目が同じ。数だけ変えるのも同じ）をやり方を変えてやり直す。
    助言（advice）が必須: 行動の選択器への英語 1〜2 文で、出ていた候補に即して何を選ぶ・
    避けるかを書く（例: "Pick up the rotten_flesh first. Keep exploring west instead of
    alternating east and south."）
  - change: 原因から見て、別の小目標のほうがよいとき（材料が先に要る、ここでは無理、
    身を守るのが先、など）。原因が中目標の立て方にあるなら、中目標リストや手順も直す
- 同じ小目標が続けて失敗しているときは、やり直しは選べない（本文に書いてある）
- 分析と助言は配信の画面と実況にも出る。記録にない事実を作らない

## 小目標の述語（全部。今使えるものは状態の側に書く）
$all_predicates

## 出力
見直し（review）、中目標リストの編集とメモの編集（なければ空）、次の小目標（predicate と必要な
引数）、serves、その理由（短い1文）を指定の JSON で出力してください。
""")

GOAL_TEMPLATE = Template("""\
## 建てる家
$blueprint

## 今していること
$activity

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
- ループには上限を付ける。上限: 1 回 180 秒、行動 40 回、調べもの 200 回、judge 10 回。
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
    "danger."
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
            "goal": goal.spec.describe(),
            "goal_reason": goal.reason,
            **({"advice": goal.advice} if goal.advice else {}),
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
