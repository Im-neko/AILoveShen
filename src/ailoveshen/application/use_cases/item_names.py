"""アイテム・ブロックの視聴者向けの日本語の名前（Minecraft の日本語版に合わせる）。"""

from __future__ import annotations

# 視聴者向けの名前。ないものは英語の ID のまま
ITEM_NAMES = {
    "log": "原木",
    "planks": "板材",
    "food": "食べ物",
    "bed": "ベッド",
    "white_bed": "白いベッド",
    "wool": "羊毛",
    "white_wool": "白い羊毛",
    "door": "ドア",
    "stick": "棒",
    "crafting_table": "作業台",
    "furnace": "かまど",
    "chest": "チェスト",
    "torch": "松明",
    "coal": "石炭",
    "charcoal": "木炭",
    "cobblestone": "丸石",
    "stone": "石",
    "iron_ore": "鉄鉱石",
    "raw_iron": "鉄の原石",
    "iron_ingot": "鉄インゴット",
    "wooden_sword": "木の剣",
    "stone_sword": "石の剣",
    "iron_sword": "鉄の剣",
    "wooden_pickaxe": "木のツルハシ",
    "stone_pickaxe": "石のツルハシ",
    "iron_pickaxe": "鉄のツルハシ",
    "wooden_axe": "木の斧",
    "stone_axe": "石の斧",
    "shield": "盾",
    "bread": "パン",
    "cooked_beef": "ステーキ",
    "cooked_porkchop": "焼き豚",
    "cooked_mutton": "焼き羊肉",
    "cooked_chicken": "焼き鳥",
    "wheat": "小麦",
    "wheat_seeds": "小麦の種",
    "bucket": "バケツ",
    "water_bucket": "水入りバケツ",
}


# 木の種類（oak_log → オークの原木）。名前の頭が木の種類で、あとが下の部位のとき
WOODS = {
    "oak": "オーク",
    "spruce": "トウヒ",
    "birch": "シラカバ",
    "jungle": "ジャングル",
    "acacia": "アカシア",
    "dark_oak": "ダークオーク",
    "mangrove": "マングローブ",
    "cherry": "サクラ",
}
WOOD_PARTS = {
    "log": "の原木",
    "planks": "の板材",
    "sapling": "の苗木",
    "door": "のドア",
    "fence": "の柵",
    "fence_gate": "の柵のゲート",
    "slab": "のハーフブロック",
    "stairs": "の階段",
    "boat": "のボート",
    "leaves": "の葉",
}


def item_name(name: str | None) -> str:
    """アイテム・ブロックの ID を視聴者向けの日本語にする。知らないものは英語のまま（_ を空白に）。"""
    if not name:
        return ""
    if name in ITEM_NAMES:
        return ITEM_NAMES[name]
    for wood, label in WOODS.items():
        prefix = f"{wood}_"
        if name.startswith(prefix) and name[len(prefix) :] in WOOD_PARTS:
            return label + WOOD_PARTS[name[len(prefix) :]]
    return name.replace("_", " ")
