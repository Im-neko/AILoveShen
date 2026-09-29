"""
配信者（Gemini）が呼べる道具の定義と、呼び出しの検証（docs/design/21_tool_control.md）。

道具の中身はブリッジ（`minecraft-bridge/src/tools.mjs`）にあり、ここは名前・説明・引数の形だけを
持つ。モデルに依存しない JSON Schema で書く（Gemini の型はアダプターに閉じ込める）。行動の道具は
`intent`（今やろうとしていること）が必須で、見張りの質問（`watch`）を添えられる。
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.text_generator import ToolChoice, ToolSpec
from ailoveshen.domain.value_objects import (
    MAX_WATCH_QUESTION_CHARS,
    MAX_WATCH_QUESTIONS,
    ToolCall,
    WatchAction,
    WatchQuestion,
)

LOOK_SCREEN = "look_screen"
SEARCH_FACILITY = "search_facility"
SEARCH_MEMORY = "search_memory"
ITEM_INFO = "item_info"
SEARCH_ABILITIES = "search_abilities"
SEARCH_TOOLS = (SEARCH_FACILITY, SEARCH_MEMORY, ITEM_INFO, SEARCH_ABILITIES)
RUN_SKILL = "run_skill"
WRITE_SKILL = "write_skill"
SKILL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


class ToolCallError(ValueError):
    """道具の呼び出しが使えない（知らない道具、intent がない）。理由を配信者に返す。"""


def _int(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _str(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


_POS = {"x": _int("x 座標"), "y": _int("y 座標（足元の高さ）"), "z": _int("z 座標")}

# 行動の道具ぜんぶに付くので短く保つ（書き方は道具の選択のプロンプトの「決まり」に 1 回だけ書く）。
# 1 つは「<on_yes>: <質問>」の文字列（例: wake: Has a hostile mob come within 6 m?）
_WATCH = {
    "type": "array",
    "maxItems": MAX_WATCH_QUESTIONS,
    "items": {"type": "string", "description": "<stop|wake|maybe_done>: <English yes/no question>"},
}

# 名前 -> (説明, 固有の引数, 必須の引数)
ACTION_TOOLS: dict[str, tuple[str, dict[str, Any], list[str]]] = {
    "do_suggestion": (
        "ソルバーの提案（一覧の id）をそのまま実行する。提案は参考で、従わなくてよい",
        {"id": _str("提案の id（一覧のとおり）")},
        ["id"],
    ),
    "goto": (
        "その位置へ歩く（経路探し）。遠くても着くまで歩く（時間では止まらない。夕方・夜になった、空腹・"
        "体力が危ない、考え直しでは止まる）。途中に長い水（12m 以上）があれば、ボートを持っていれば"
        "自分で乗って渡る",
        {**_POS, "range": _int("この距離まで近づけばよい（既定 1）")},
        ["x", "y", "z"],
    ),
    "dig": (
        "その位置のブロックを掘って拾う。今の家と建てている家は掘れない（前の家は掘れる）。"
        "草（種がときどき落ちる）、葉（苗木がときどき落ちる）、育った作物（収穫）も掘れる",
        _POS,
        ["x", "y", "z"],
    ),
    "place": (
        "持っているブロックをその位置の空いたセルに置く（隣に面を借りるブロックが要る。チェスト・"
        "作業台・ドア・ベッドの面は開いてしまうので借りない）。足場や、穴から出る段、壁をふさぐのに"
        "使う。家の部屋の中にも何でも置ける（作業台・かまど・チェスト・松明・ランタン…。ドアのすぐ"
        "内側の通り道とベッドの場所は空ける）。松明・看板・旗・はしごは壁に付けられる（家の中なら"
        "床の 1 つ上の、壁に接したセルを選ぶ）",
        {
            "item": _str("置くアイテム"),
            **_POS,
            "on": _str(
                "面を借りる側: floor（下のブロックの上）/ wall（横の壁に付ける）/ ceiling（上から"
                "吊るす）。省略: 壁に付けられる物は壁を先に、ほかは床を先に"
            ),
        },
        ["item", "x", "y", "z"],
    ),
    "craft": (
        "クラフトする。作業台が要るレシピは、近く（32m 以内）の作業台まで歩いて使う。"
        "遠ければ、板材 4 枚で作業台を作って今いる所に置くほうが早いこともある（自分で決める）",
        {"item": _str("作るアイテム"), "times": _int("回数（既定 1）")},
        ["item"],
    ),
    "pickup": ("一番近い落とし物を拾う", {}, []),
    "attack": ("モブと戦う", {"entity": _int("mobs の id")}, ["entity"]),
    "flee": ("モブから離れる", {"entity": _int("mobs の id")}, ["entity"]),
    "eat": ("食べる", {"item": _str("食べ物")}, ["item"]),
    "equip": ("手に持つ", {"item": _str("アイテム")}, ["item"]),
    "smelt": (
        "近くのかまどで精錬する（燃料は持ち物から選ぶ）",
        {"input": _str("材料"), "count": _int("個数")},
        ["input"],
    ),
    "deposit": (
        "家のチェストに入れる",
        {"item": _str("アイテム"), "count": _int("個数")},
        ["item"],
    ),
    "withdraw": (
        "それが入っているチェストから取り出す",
        {"item": _str("アイテム"), "count": _int("個数")},
        ["item"],
    ),
    "go_home": ("家に帰って中に入り、ドアを閉める", {}, []),
    "sleep": ("家のベッドで寝る（夜だけ）", {}, []),
    "move_furniture": (
        "置いてある家具（ベッド、作業台、かまど、チェスト）を拾って置き直す。今の家の家具も動かせるが、"
        "家の中でだけ。ベッドは家の中の空いた所に置く（置き先は書かない）。作業台などは置き先を書けば"
        "そこに、書かなければ家の中の空いた所。中身の入ったチェストは先に withdraw で出す",
        {
            **_POS,
            "to_x": _int("置き先の x（省略可）"),
            "to_y": _int("置き先の y（足元の高さ）"),
            "to_z": _int("置き先の z"),
        },
        ["x", "y", "z"],
    ),
    "build_next": (
        "家の設計図、または名前付きの建物の次のブロックを 1 つ置く（空けるマスなら掘る）。"
        "name を省くと、今の目標が built(name) ならその建物、でなければ家",
        {"name": _str("建物の名前（built(name) の name。省略可）")},
        [],
    ),
    # 植林と畑（設計書 33）。耕す・植えるのは設計した施設のマスだけ（設計書 47 §14）。場所を省くと、
    # 今の小目標の施設か、その作物・苗木の施設の次のマス
    "plant": (
        "苗木を植える（その位置は、設計した植林場のマス: 土か草の上の空いたセル）。位置を省くと"
        "植林場の次のマス。植林場がなければ先に facility(name) で設計する。植えた木は planted(...) で数える",
        {"item": _str("苗木（例 oak_sapling。省略すると持っているもの）"), **_POS},
        [],
    ),
    "till": (
        "クワで土か草のブロックを耕して耕地にする（その位置は、設計した畑のマスの地面のブロック）。"
        "位置を省くと畑の次のマス。畑がなければ先に facility(name) で設計する",
        _POS,
        [],
    ),
    "sow": (
        "耕地に種をまく（wheat_seeds / carrot / potato / beetroot_seeds。その位置は耕地のブロック）。"
        "位置を省くとその作物の畑の空いた耕地。育った作物は dig で収穫し、またまく",
        {"item": _str("種（省略すると持っているもの）"), **_POS},
        [],
    ),
    "wait": ("その場で 10 秒ほど待つ（家の中ならドアを向いて）", {}, []),
    # 設計書 37
    "use_item": (
        "持っているアイテムを使う（右クリック）。entity を書けばそのモブに（小麦で牛を繁殖、ハサミで"
        "羊の毛を刈る、リードをつける）、x,y,z を書けばそのブロックに（骨粉で育てる、バケツで水を汲む・"
        "注ぐ、斧で原木の皮をはぐ）、どちらもなければ向いている方へ（雪玉を投げる、釣り竿を投げる）。"
        "hold 秒押し続ける（弓を引く、盾を構える）。何も変わらなければ失敗。火打石・溶岩バケツ・TNT は"
        "使わない。食べるのは eat、ボートは ride_boat、チェスト・ベッド・かまどはそれぞれの道具",
        {
            "item": _str("使うアイテム"),
            "entity": _int("使う相手の mobs の id（省略可）"),
            **{k: _int(f"使うブロックの {k}（省略可）") for k in ("x", "y", "z")},
            "hold": _int("押し続ける秒数（0〜3、既定 0）"),
        },
        ["item"],
    ),
    "ride_boat": (
        "ボートで水の上を x,z に向かって進む（短い水でも）。水際まで歩いてボートを置き、乗って進み、"
        "岸に着いたら降りてボートを拾う。行き先か向こう岸に着くまで漕ぐ。ボートは"
        "同じ木の板材 5 枚で作業台で作る。goto など遠くへ行く道具も、長い水はボートで自分で渡る",
        {"x": _int("行き先の x"), "z": _int("行き先の z"), "y": _int("行き先の y（省略可）")},
        ["x", "z"],
    ),
    "level": (
        "整地する: where のまわり半径 radius の地面を 1 つの高さにそろえる。出っ張った自然の"
        "土や石を掘り、穴を土で埋める（出っ張りを先に掘るので土が出る）。1 回の呼び出しで、届く"
        "所を近い順に次々にそろえる。家・建物・木・畑・水・置いた物は触らない。完了条件は "
        "leveled(where, radius)",
        {
            "where": _str("home（家の床の高さ）か x,y,z（そこに立つ高さ。地面は y-1）。省略: home"),
            "radius": _int("半径 3〜16（省略: 8）"),
        },
        [],
    ),
}

# 技（docs/design/22_skills.md）。行動の道具と同じく intent と見張りを付け、1 ステップになる
SKILL_TOOLS: dict[str, tuple[str, dict[str, Any], list[str]]] = {
    RUN_SKILL: (
        "覚えた技（一覧の名前）を実行する。技は道具をいくつも呼ぶ手順で、終わると結果（成功・失敗の"
        "理由、記録）が返る",
        {
            "name": _str("技の名前"),
            "args": _str('技の引数（JSON の文字列。例 {"animal": "pig"}。なければ {}）'),
            "version": _int("版（省略: 一番新しい版。直した版が悪いときに前の版を使う）"),
        },
        ["name"],
    ),
    WRITE_SKILL: (
        "新しい技を書く、または失敗した技を直す（同じ名前で書くと次の版）。何度も同じ道具の並びを"
        "呼んでいるとき、手順にしたほうが確かなときに使う。別の呼び出しで書き、すぐ 1 回試す",
        {
            "name": _str("技の名前（英小文字と _、例 hunt_nearest_animal）"),
            "what": _str("技がすること（1〜2 文。直すときは、何を直すか）"),
        },
        ["name", "what"],
    ),
}

QUERY_TOOLS: dict[str, tuple[str, dict[str, Any], list[str]]] = {
    "find_blocks": (
        "近くのそのブロックの位置を近い順に調べる（すぐ返る）。ブロックの状態 props も付く"
        "（耕地の moisture: \"7\" が湿っている、作物の age など）",
        {"block": _str("ブロック名"), "radius": _int("半径（既定 32、最大 64）")},
        ["block"],
    ),
    "recipe_of": (
        "そのアイテムの作り方を全部調べる（バニラのレシピ全部: 作業台、かまど、石切台。材料の"
        "「どれか 1 つ」も。すぐ返る）",
        {"item": _str("アイテム")},
        ["item"],
    ),
    "find_recipes": (
        "レシピを名前で探す（結果か材料の名前の一部。例: lantern、blue、wool。すぐ返る）。"
        "知っている物の正しい名前と作り方を確かめるのに使う",
        {"query": _str("探す語（英語のアイテム名の一部）"), "limit": _int("件数（既定 8）")},
        ["query"],
    ),
    "uses_of": (
        "その素材を使うレシピ（逆引き。すぐ返る）。have: true なら今の持ち物だけで作れる物。"
        "手に入れた物で何が作れるか、何に使うかを知りたいときに使う（名前は item_info で確かめる）",
        {
            "item": _str("素材のアイテム名（英語の正確な名前）"),
            "have": {"type": "boolean", "description": "今の持ち物で作れる物だけ"},
        },
        ["item"],
    ),
    "find_foods": (
        "食べられる物とその取り方（すぐ返る）: 満腹度、レシピ（材料）、かまどで焼く元、畑、狩り、掘る場所。"
        "持っていて食べられる物、今の持ち物で作れる・焼ける物が先。小麦のように食べられない物は出ない。"
        "食べ物を探す・作るとき、何が食べられるか分からないときに使う",
        {
            "query": _str("名前・日本語・材料に含まれる語（例: パン、bread、wheat、beef）。省くと全部から"),
            "have": {"type": "boolean", "description": "持っている物と今の持ち物で作れる・焼ける物だけ"},
            "limit": _int("件数（既定 12、最大 30）"),
        },
        [],
    ),
    "how_to_get": (
        "ソルバーに、手に入れるまでに何が足りないかを聞く（すぐ返る。参考）",
        {"item": _str("アイテムかグループ（log、planks、food など）"), "count": _int("個数")},
        ["item"],
    ),
    # ここから下は Python が扱う（ブリッジには送らない）。検索は docs/design/47 §4
    SEARCH_FACILITY: (
        "自分の施設（家、建物、畑、植林場など）を探す。名前でも目的でもよい（例: 小麦の畑、"
        "原木が取れる所、home）。場所（box、入口、中心、距離と方角）と今の中身が返る（すぐ返る）。"
        "施設へ行く前、施設に何があるかを知りたいときに使う",
        {"query": _str("探すもの（日本語か英語。施設の名前、目的、欲しい物）")},
        ["query"],
    ),
    SEARCH_MEMORY: (
        "覚えている場所（前に見た鉱石、木、動物、作物、チェストの中身、かまど、死んだ場所）を"
        "探す。座標と、何分前に見たかが返る（すぐ返る）。遠くへ探しに行く前に使う",
        {"query": _str("探すもの（日本語か英語。例: iron_ore、鉄、羊、小麦が入ったチェスト）")},
        ["query"],
    ),
    ITEM_INFO: (
        "アイテムかブロックを調べる: 取り方（作る・焼く・掘るブロックと要る道具・落とすモブ・"
        "育てる）、置けるか、食べられるか、何に使うか（すぐ返る）。名前が分からない物、取り方を"
        "知らない物を探しに行く前に使う",
        {"query": _str("調べる物（英語の名前か日本語。例: iron_ingot、鉄、羊毛、パン）")},
        ["query"],
    ),
    SEARCH_ABILITIES: (
        "自分にできること（やり方の帳）を探す: どの道具・目標で、どんな手順で、何に気をつけるか"
        "（すぐ返る）。やり方が分からないとき、初めてのことをする前に使う",
        {"query": _str("したいこと（日本語か英語。例: 畑を作る、水を渡る、肉を焼く、夜を越す）")},
        ["query"],
    ),
    # 画面を撮れるときだけ出す（docs/design/23）
    LOOK_SCREEN: (
        "配信の画面（自分の視点）を撮り、次の選択に画像として添える（すぐ返る）。"
        "文字の状態では分からないとき（地形、何が起きているか）に使う",
        {},
        [],
    ),
}


def tool_specs(can_look: bool = False, skills: bool = False) -> list[ToolSpec]:
    """配信者に渡す道具の一覧。`can_look` が偽なら look_screen を、`skills` が偽なら技の道具を出さない。"""
    specs = []
    actions = {**ACTION_TOOLS, **(SKILL_TOOLS if skills else {})}
    for name, (description, props, required) in actions.items():
        specs.append(
            ToolSpec(
                name=name,
                description=description,
                parameters={
                    "type": "object",
                    "properties": {
                        "intent": _str("今やろうとしていること（配信者の口調の 1 文、日本語）"),
                        **props,
                        "watch": _WATCH,
                    },
                    "required": ["intent", *required],
                },
            )
        )
    for name, (description, props, required) in QUERY_TOOLS.items():
        if name == LOOK_SCREEN and not can_look:
            continue
        specs.append(
            ToolSpec(
                name=name,
                description=description,
                parameters={"type": "object", "properties": props, "required": required},
            )
        )
    return specs


def is_query(name: str) -> bool:
    """調べもの（すぐ返り、ワールドを変えない）の道具か。"""
    return name in QUERY_TOOLS


def parse_tool_call(choice: ToolChoice) -> ToolCall:
    """
    モデルの呼び出しを検証して `ToolCall` にする。使えない見張りの質問は捨ててログに残す
    （質問を失うほうが、行動をやり直させるより軽い）。

    Raises:
        ToolCallError: 知らない道具か、行動の道具に intent がないとき
    """
    name = choice.name
    if name not in ACTION_TOOLS and name not in QUERY_TOOLS and name not in SKILL_TOOLS:
        raise ToolCallError(f"unknown tool {name!r}")
    args = dict(choice.args)
    intent = str(args.pop("intent", "") or "").strip()
    raw_watch = args.pop("watch", None) or []
    if name in QUERY_TOOLS:
        return ToolCall(name=name, args=args)
    if not intent:
        raise ToolCallError(f"{name} needs an intent (what you are trying to do, one sentence)")
    if name in SKILL_TOOLS:
        args = _skill_args(name, args)
    watch: list[WatchQuestion] = []
    for item in raw_watch if isinstance(raw_watch, list) else []:
        try:
            on_yes, _, question = str(item).partition(":")
            watch.append(WatchQuestion(question=question.strip(), on_yes=WatchAction(on_yes.strip())))
        except ValueError as e:
            logger.warning(f"見張りの質問を捨てた（{item!r}）: {e}")
        if len(watch) == MAX_WATCH_QUESTIONS:
            break
    return ToolCall(name=name, args=args, intent=intent, watch=tuple(watch))


def is_skill_tool(name: str) -> bool:
    """技の道具（run_skill / write_skill）か。"""
    return name in SKILL_TOOLS


def _skill_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """技の道具の引数を確かめる（名前の形、引数の JSON）。"""
    skill = str(args.get("name", "")).strip()
    if not SKILL_NAME.match(skill):
        raise ToolCallError("the skill name must be snake_case: a-z, 0-9 and _, 2-40 characters")
    if name == WRITE_SKILL:
        what = str(args.get("what", "")).strip()
        if not what:
            raise ToolCallError("write_skill needs what (what the skill does)")
        return {"name": skill, "what": what}
    raw = args.get("args") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as e:
        raise ToolCallError(f"args must be a JSON object: {e}") from e
    if not isinstance(parsed, dict):
        raise ToolCallError("args must be a JSON object")
    out: dict[str, Any] = {"name": skill, "args": parsed}
    if args.get("version") is not None:
        out["version"] = int(args["version"])
    return out
