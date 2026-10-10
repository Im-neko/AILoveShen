"""
実行している 1 手を、視聴者向けの日本語の動作にする（小目標の欄に出す。docs/design/91）。

小目標の欄は述語（`built()` → 「家を建てる」）ではなく、いま実際にしている動作（「原木を集める」
「ベッドを置く」「家に帰る」）を出す。LLM の一文（intent）や候補の英語の名前に左右されないよう、
実行する手（道具の呼び出しと、ブリッジの候補の verb）から決まった規則で作る。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from ailoveshen.application.use_cases.item_names import item_name
from ailoveshen.domain.value_objects import Candidate, ToolCall

DIRECTIONS = {"north": "北", "south": "南", "east": "東", "west": "西"}
_BUILD_TARGET = re.compile(r"^(?P<block>\S+) of the (?:house|build (?P<build>.+))$")


def _thing(d: dict[str, Any]) -> str:
    return item_name(d.get("item") or d.get("target"))


def _dig(d: dict[str, Any]) -> str:
    target = str(d.get("target") or d.get("block") or "")
    if target == "log" or target.endswith("_log"):
        return f"{item_name(target)}を集める"
    return f"{item_name(target)}を掘る" if target else "ブロックを掘る"


def _place_plan(d: dict[str, Any], cid: str) -> str:
    match = _BUILD_TARGET.match(str(d.get("target") or ""))
    build = (match.group("build") if match else d.get("build")) or None
    what = f"「{build}」" if build else "家"
    progress = f"（{d['progress']}）" if d.get("progress") else ""
    if cid.startswith("clear "):
        return f"{what}の場所のブロックをどける{progress}"
    block = item_name(match.group("block")) if match else ""
    return f"{what}づくり: {block}を置く{progress}" if block else f"{what}づくりを続ける{progress}"


def _smelt(d: dict[str, Any], cid: str) -> str:
    if cid.startswith("take "):
        return f"かまどから{_thing(d)}を取り出す"
    return f"{_thing(d)}を精錬する"


def _with(template: str) -> Callable[[dict[str, Any], str], str]:
    return lambda d, cid: template.format(thing=_thing(d))


# ブリッジの候補の verb → 動作。minecraft-bridge/src/candidates.mjs の verb は全部ここにある
# （test_action_label.py が突き合わせる）
_BY_VERB: dict[str, Callable[[dict[str, Any], str], str]] = {
    "attack": _with("{thing}と戦う"),
    "bed_here": lambda d, cid: "ここにベッドを置いて寝る",
    "climb_down": lambda d, cid: "掘って柱から降りる",
    "climb_out": lambda d, cid: "土を積んで閉じた所から出る",
    "step_off": lambda d, cid: "縁から飛び降りる",
    "craft": _with("{thing}をクラフトする"),
    "defrag": lambda d, cid: "チェストの中をまとめる",
    "deposit": _with("チェストに{thing}を入れる"),
    "dig": lambda d, cid: _dig(d),
    "dig_down": _with("{thing}まで掘り下がる"),
    "dig_in": lambda d, cid: "地面に潜って朝を待つ",
    "dig_mine": lambda d, cid: "採掘場を掘る",
    "dig_out": lambda d, cid: "壁を掘って閉じた所から出る",
    "eat": _with("{thing}を食べる"),
    "equip": _with("{thing}を持つ"),
    "exit_wall": lambda d, cid: "壁を掘って外へ出る",
    "explore": lambda d, cid: f"{DIRECTIONS.get(str(d.get('target')), '')}へ探検する",
    "flee": _with("{thing}から逃げる"),
    "go_home": lambda d, cid: "家に帰る",
    "goto_memory": lambda d, cid: "畑へ向かう",
    "goto_pos": lambda d, cid: "採掘場へ向かう",
    "leave_burrow": lambda d, cid: "穴から出る",
    "level": lambda d, cid: "畑の地面を整地する" if d.get("facility") else "整地する",
    "reset_build": lambda d, cid: "建物の敷地を更地にする",
    "make_mine": lambda d, cid: "採掘場をつくる",
    "move_placed": lambda d, cid: "家具を置き直す",
    "pickup": _with("{thing}を拾う"),
    "place_at": _with("{thing}を置く"),
    "plant": lambda d, cid: "苗木を植える",
    "place_bed": lambda d, cid: "ベッドを置く",
    "place_chest": lambda d, cid: "チェストを置く",
    "place_plan": _place_plan,
    "place_station": _with("{thing}を置く"),
    "place_torch": lambda d, cid: "松明を置く",
    "place_torch_at": lambda d, cid: "暗い所に松明を置く",
    "place_water": lambda d, cid: "畑に水を置く",
    "plug_hole": lambda d, cid: "掘った穴をふさぐ",
    "reach_land": lambda d, cid: "陸まで泳ぐ",
    "ride_boat": lambda d, cid: "ボートで進む",
    "recover_items": lambda d, cid: "死んだ場所へ持ち物を取りに行く",
    "repair_wall": lambda d, cid: "家の壁の穴をふさぐ",
    "sleep": lambda d, cid: "ベッドで寝る",
    "sow": lambda d, cid: "種をまく",
    "sleep_at": lambda d, cid: "近くのベッドで寝る",
    "smelt": _smelt,
    "stash": lambda d, cid: "持ち物をチェストに預ける",
    "surface": lambda d, cid: "地上に出る",
    "survey": lambda d, cid: "街の候補地を調べる",
    "till": lambda d, cid: "畑を耕す",
    "tidy": lambda d, cid: "いらない物をチェストに片づける",
    "toss": lambda d, cid: "いらない物を捨てる",
    "use_item": lambda d, cid: f"{item_name(d.get('item'))}を使う" if d.get("item") else "アイテムを使う",
    "wait": lambda d, cid: "待つ",
    "wake_sleeper": _with("寝ている{thing}を起こす"),
    "wall_in": lambda d, cid: "土で囲って朝を待つ",
    "withdraw": _with("チェストから{thing}を取り出す"),
}
CANDIDATE_VERBS = frozenset(_BY_VERB)


# 道具を直接呼んだとき（do_suggestion と run_skill は別に扱う）。引数から分かる範囲で
_BY_TOOL: dict[str, Callable[[dict[str, Any]], str]] = {
    "goto": lambda a: "目的地へ向かう",
    "dig": lambda a: "建物を壊す" if a.get("demolish") else "ブロックを掘る",
    "place": lambda a: f"{item_name(a.get('item'))}を置く" if a.get("item") else "ブロックを置く",
    "craft": lambda a: f"{item_name(a.get('item'))}をクラフトする" if a.get("item") else "クラフトする",
    "pickup": lambda a: "落とし物を拾う",
    "attack": lambda a: "モブと戦う",
    "flee": lambda a: "モブから逃げる",
    "eat": lambda a: f"{item_name(a.get('item'))}を食べる" if a.get("item") else "食べる",
    "equip": lambda a: f"{item_name(a.get('item'))}を持つ" if a.get("item") else "持ち替える",
    "smelt": lambda a: f"{item_name(a.get('input'))}を精錬する" if a.get("input") else "精錬する",
    "deposit": lambda a: f"チェストに{item_name(a.get('item'))}を入れる" if a.get("item") else "チェストに入れる",
    "toss": lambda a: "いらない物を捨てる",
    "withdraw": lambda a: f"チェストから{item_name(a.get('item'))}を取り出す" if a.get("item") else "チェストから取り出す",
    "go_home": lambda a: "家に帰る",
    "sleep": lambda a: "ベッドで寝る",
    "move_furniture": lambda a: "家具を置き直す",
    "build_next": lambda a: f"「{a['name']}」づくりを続ける" if a.get("name") else "家づくりを続ける",
    "plant": lambda a: "苗木を植える",
    "till": lambda a: "畑を耕す",
    "sow": lambda a: "種をまく",
    "water": lambda a: "畑に水を置く",
    "wait": lambda a: "待つ",
    "use_item": lambda a: f"{item_name(a.get('item'))}を使う" if a.get("item") else "アイテムを使う",
    "ride_boat": lambda a: "ボートで進む",
    "level": lambda a: "整地する",
}
DIRECT_TOOLS = frozenset(_BY_TOOL)


def action_label(call: ToolCall, candidates: tuple[Candidate, ...] = ()) -> str:
    """
    いま実行する手の、視聴者向けの動作（例: 「原木を集める」）。決められない手は空で、
    呼ぶ側は目標の述語の表示のままにする。

    Args:
        call: 実行する道具の呼び出し
        candidates: 今の候補（`do_suggestion` の id から verb を引く）
    """
    if call.name == "run_skill":
        name = call.args.get("name")
        return f"技「{name}」を実行する" if name else ""
    if call.name == "do_suggestion":
        found: Optional[Candidate] = next(
            (c for c in candidates if c.action_id == call.args.get("id")), None
        )
        if found is None:
            return ""
        build = _BY_VERB.get(str(found.description.get("verb")))
        return build(found.description, found.action_id) if build else ""
    label = _BY_TOOL.get(call.name)
    return label(call.args) if label else ""
