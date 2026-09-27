"""stream_context（今していることの見え方）のテスト。"""

from ailoveshen.domain.value_objects import Activity, GameObservation
from ailoveshen.infrastructure.adapters.prompts.stream_context import format_activity


def test_the_farm_and_what_remembered_blocks_give_are_shown():
    """畑の小麦に気づかなかった（2026-09-27）: 畑と、覚えている場所で取れる物を出す。"""
    obs = GameObservation(
        state={
            "farm": {"wheat": {"planted": 12, "ripe": 8, "ages": [3, 7], "mature": 7}},
            "memory": {
                "places": [
                    {
                        "kind": "wheat",
                        "count": 12,
                        "gives": ["wheat", "wheat_seeds"],
                        "direction": "north",
                        "distance_m": 230,
                        "minutes_ago": 5,
                    }
                ]
            },
        },
        candidates=(),
        health=20,
        food=20,
    )
    text = format_activity(Activity(observation=obs))
    assert "家のまわりの畑: wheat 12 マス（熟した 8、育ち具合 3〜7/7）" in text
    assert "wheat 12（→ wheat・wheat_seeds、" in text
