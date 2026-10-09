"""モデルに見せる道具（tool_catalog.py）と、ブリッジが実行できる道具（tools.mjs）が食い違わないことの確認。

食い違うと、モデルが選べる道具がいつも `unknown tool` と断られるか、ブリッジにあってモデルに見せない
道具ができる（docs/design/90）。
"""

import re
from pathlib import Path

from ailoveshen.application.use_cases.tool_catalog import (
    ACTION_TOOLS,
    ASK_ADVISOR,
    LOOK_SCREEN,
    QUERY_TOOLS,
    SEARCH_TOOLS,
)

TOOLS_MJS = Path(__file__).resolve().parents[3] / "minecraft-bridge" / "src" / "tools.mjs"


def _bridge_list(name: str) -> list[str]:
    """tools.mjs の `export const NAME = [ ... ]` の名前の一覧。"""
    source = TOOLS_MJS.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\]", source, re.DOTALL)
    assert match, f"{name} が tools.mjs に見つからない（書き方を変えたなら、このテストの正規表現を直す）"
    return re.findall(r"'([a-z_]+)'", match.group(1))


def test_action_tools_match_the_bridge():
    bridge = _bridge_list("ACTION_TOOLS")
    assert len(bridge) == len(set(bridge)), "ブリッジの ACTION_TOOLS に重複がある"
    assert set(ACTION_TOOLS) == set(bridge)


def test_query_tools_match_the_bridge():
    """検索の道具と look_screen、ask_advisor は Python が処理する（ブリッジには送らない）。残りはブリッジの調べもの。"""
    bridge = _bridge_list("QUERY_TOOLS")
    assert len(bridge) == len(set(bridge)), "ブリッジの QUERY_TOOLS に重複がある"
    assert set(QUERY_TOOLS) - set(SEARCH_TOOLS) - {LOOK_SCREEN, ASK_ADVISOR} == set(bridge)
