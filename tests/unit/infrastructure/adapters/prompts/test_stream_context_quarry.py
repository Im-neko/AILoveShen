"""採掘の場所の行（docs/design/64_quarry.md）。"""

from ailoveshen.infrastructure.adapters.prompts.stream_context import _format_around, _format_quarry


def test_the_quarry_and_its_stairs_are_shown():
    [line] = _format_quarry(
        {
            "x": 1,
            "y": 64,
            "z": 2,
            "distance_m": 5,
            "direction": "E",
            "shafts": [{"top": {"x": 3, "y": 70, "z": 3}, "bottom": {"x": 3, "y": 60, "z": 6}}],
        }
    )
    assert "採掘の場所" in line and "1,64,2" in line
    assert "入口 3,70,3 → 底 y 60" in line


def test_no_quarry_shows_nothing():
    assert _format_quarry({}) == []


def test_a_block_nearest_in_the_quarry_is_marked():
    around = {
        "radius": 8,
        "dy": 2,
        "blocks": [
            {
                "name": "stone",
                "count": 4,
                "quarry": True,
                "nearest": {"x": 7, "y": 70, "z": 0, "distance_m": 6.5, "direction": "E", "dy": 0},
            }
        ],
        "items": [],
    }
    assert "採掘の場所の中の、一番近いもの" in _format_around(around)[0]
