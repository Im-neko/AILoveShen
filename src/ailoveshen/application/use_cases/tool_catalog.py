"""
配信者（Gemini）が呼べる道具の定義と、呼び出しの検証（docs/design/21_tool_control.md）。

道具の中身はブリッジ（`minecraft-bridge/src/tools.mjs`）にあり、ここは名前・説明・引数の形だけを
持つ。モデルに依存しない JSON Schema で書く（Gemini の型はアダプターに閉じ込める）。行動の道具は
`intent`（今やろうとしていること）が必須で、見張りの質問（`watch`）を添えられる。
"""

from __future__ import annotations

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


class ToolCallError(ValueError):
    """道具の呼び出しが使えない（知らない道具、intent がない）。理由を配信者に返す。"""


def _int(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _str(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


_POS = {"x": _int("x 座標"), "y": _int("y 座標（足元の高さ）"), "z": _int("z 座標")}

_WATCH = {
    "type": "array",
    "maxItems": MAX_WATCH_QUESTIONS,
    "description": (
        "この行動の間、1 秒ごとに速い判断モデルに聞く、はい/いいえの質問（任意、最大 3 つ）。"
        "質問は英語で、状態（action / self / surroundings / mobs / needs）から答えられることを"
        "中立に書く。急ぎや優先を表す言葉は書かない。"
        "on_yes: stop（止める）、wake（止めて考え直す）、"
        "maybe_done（止める。完了はワールドで確かめる）"
    ),
    "items": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "maxLength": MAX_WATCH_QUESTION_CHARS,
                "description": "例: Has a hostile mob come within 6 m?",
            },
            "on_yes": {"type": "string", "enum": [a.value for a in WatchAction]},
        },
        "required": ["question", "on_yes"],
    },
}

# 名前 -> (説明, 固有の引数, 必須の引数)
ACTION_TOOLS: dict[str, tuple[str, dict[str, Any], list[str]]] = {
    "do_suggestion": (
        "ソルバーの提案（一覧の id）をそのまま実行する。提案は参考で、従わなくてよい",
        {"id": _str("提案の id（一覧のとおり）")},
        ["id"],
    ),
    "goto": (
        "その位置へ歩く（経路探し）。48m より遠ければ 1 回に 48m だけ進む",
        {**_POS, "range": _int("この距離まで近づけばよい（既定 1）")},
        ["x", "y", "z"],
    ),
    "dig": (
        "その位置のブロックを掘って拾う。今の家と建てている家は掘れない（前の家は掘れる）",
        _POS,
        ["x", "y", "z"],
    ),
    "place": (
        "持っているブロックをその位置の空いたセルに置く（隣に面を借りるブロックが要る）。"
        "足場や、穴から出る段、壁をふさぐのに使う",
        {"item": _str("置くアイテム"), **_POS},
        ["item", "x", "y", "z"],
    ),
    "craft": (
        "クラフトする。作業台が要るレシピは、近くに作業台が要る",
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
    "build_next": (
        "家の設計図、または名前付きの建物の次のブロックを 1 つ置く（空けるマスなら掘る）。"
        "name を省くと、今の目標が built(name) ならその建物、でなければ家",
        {"name": _str("建物の名前（built(name) の name。省略可）")},
        [],
    ),
    "wait": ("その場で 10 秒ほど待つ（家の中ならドアを向いて）", {}, []),
}

QUERY_TOOLS: dict[str, tuple[str, dict[str, Any], list[str]]] = {
    "find_blocks": (
        "近くのそのブロックの位置を近い順に調べる（すぐ返る）",
        {"block": _str("ブロック名"), "radius": _int("半径（既定 32、最大 64）")},
        ["block"],
    ),
    "recipe_of": ("レシピを調べる（すぐ返る）", {"item": _str("アイテム")}, ["item"]),
    "how_to_get": (
        "ソルバーに、手に入れるまでに何が足りないかを聞く（すぐ返る。参考）",
        {"item": _str("アイテムかグループ（log、planks、food など）"), "count": _int("個数")},
        ["item"],
    ),
    # Python が扱う（ブリッジには送らない）。画面を撮れるときだけ出す（docs/design/23）
    LOOK_SCREEN: (
        "配信の画面（自分の視点）を撮り、次の選択に画像として添える（すぐ返る）。"
        "文字の状態では分からないとき（地形、何が起きているか）に使う",
        {},
        [],
    ),
}


def tool_specs(can_look: bool = False) -> list[ToolSpec]:
    """配信者に渡す道具の一覧。`can_look` が偽なら look_screen を出さない。"""
    specs = []
    for name, (description, props, required) in ACTION_TOOLS.items():
        specs.append(
            ToolSpec(
                name=name,
                description=description,
                parameters={
                    "type": "object",
                    "properties": {
                        "intent": _str("今やろうとしていること（1 文、日本語。配信で見える）"),
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
    if name not in ACTION_TOOLS and name not in QUERY_TOOLS:
        raise ToolCallError(f"unknown tool {name!r}")
    args = dict(choice.args)
    intent = str(args.pop("intent", "") or "").strip()
    raw_watch = args.pop("watch", None) or []
    if name in QUERY_TOOLS:
        return ToolCall(name=name, args=args)
    if not intent:
        raise ToolCallError(f"{name} needs an intent (what you are trying to do, one sentence)")
    watch: list[WatchQuestion] = []
    for item in raw_watch if isinstance(raw_watch, list) else []:
        try:
            watch.append(
                WatchQuestion(
                    question=str(item.get("question", "")), on_yes=WatchAction(item.get("on_yes"))
                )
            )
        except (ValueError, AttributeError) as e:
            logger.warning(f"見張りの質問を捨てた（{item!r}）: {e}")
        if len(watch) == MAX_WATCH_QUESTIONS:
            break
    return ToolCall(name=name, args=args, intent=intent, watch=tuple(watch))
