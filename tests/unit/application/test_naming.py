"""NameGiver: 建物・施設の呼び名（設計書 160）。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.naming import NameGiver
from ailoveshen.domain.exceptions import TextGenerationError


def _giver(builds, facilities, titles, clock=None):
    bridge = AsyncMock()
    bridge.builds.return_value = builds
    bridge.facilities.return_value = facilities

    async def set_titles(t):
        return {"set": list(t), "refused": {}}

    bridge.set_titles.side_effect = set_titles
    generator = AsyncMock()
    generator.generate_json.return_value = {"titles": [{"name": n, "title": t} for n, t in titles.items()]}
    builder = Mock()
    builder.build_naming_prompt.return_value = "prompt"
    giver = NameGiver(bridge, generator, builder, **({"clock": clock} if clock else {}))
    return giver, bridge, generator, builder


@pytest.mark.asyncio
async def test_unnamed_builds_and_facilities_get_a_title_and_named_ones_do_not():
    giver, bridge, generator, builder = _giver(
        builds=[{"name": "workshop", "purpose": "a forge"}, {"name": "annex", "purpose": "x", "title": "影の離れ"}],
        facilities=[
            {"name": "wheat_farm", "kind": "farm", "purpose": "wheat"},
            {"name": "workshop", "kind": "build"},  # 建物の取り込み: 上の建物と同じ
            {"name": "oak_grove", "kind": "tree_farm", "title": "黄昏の森"},
        ],
        titles={"workshop": "キンコウの工房", "wheat_farm": "黄昏の畑", "ghost": "x"},
    )

    done = await giver.name_missing()

    assert done == ["workshop", "wheat_farm"]  # 知らない名前（ghost）は付けない
    things, taken = builder.build_naming_prompt.call_args.args
    assert [t["name"] for t in things] == ["workshop", "wheat_farm"]
    assert sorted(taken) == ["影の離れ", "黄昏の森"]
    bridge.set_titles.assert_awaited_once_with({"workshop": "キンコウの工房", "wheat_farm": "黄昏の畑"})


@pytest.mark.asyncio
async def test_nothing_missing_asks_nothing_and_a_failure_is_retried_later():
    giver, bridge, generator, _ = _giver([{"name": "a", "purpose": "", "title": "x"}], [], {})
    assert await giver.name_missing() == []
    generator.generate_json.assert_not_called()

    now = [0.0]
    giver, bridge, generator, _ = _giver([{"name": "workshop", "purpose": ""}], [], {}, clock=lambda: now[0])
    generator.generate_json.side_effect = TextGenerationError("down")
    assert await giver.name_missing() == []
    assert await giver.name_missing() == []  # 失敗したものは、しばらくまた聞かない
    assert generator.generate_json.await_count == 1
    now[0] = 601.0
    generator.generate_json.side_effect = None
    generator.generate_json.return_value = {"titles": [{"name": "workshop", "title": "均衡の工房"}]}
    assert await giver.name_missing() == ["workshop"]


@pytest.mark.asyncio
async def test_a_title_that_is_too_long_or_already_taken_is_not_used():
    giver, bridge, _, _ = _giver(
        [{"name": "a", "purpose": ""}, {"name": "b", "purpose": ""}, {"name": "c", "purpose": "", "title": "影の塔"}],
        [],
        {"a": "あ" * 25, "b": "影の塔"},
    )
    assert await giver.name_missing() == []
    bridge.set_titles.assert_not_called()
