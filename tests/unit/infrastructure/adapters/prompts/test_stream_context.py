"""stream_context（今していることの見え方）のテスト。"""

from ailoveshen.domain.value_objects import Activity, GameObservation
from ailoveshen.infrastructure.adapters.prompts.stream_context import format_activity


def test_the_farm_and_what_remembered_blocks_give_are_shown():
    """畑の小麦に気づかなかった（2026-09-27）: 畑と、覚えている場所で取れる物を出す。"""
    obs = GameObservation(
        state={
            "farm": {
                "wheat": {
                    "planted": 12,
                    "ripe": 8,
                    "ages": [3, 7],
                    "mature": 7,
                    "free": 4,
                    "farms": ["wheat_farm", "home_farm"],
                }
            },
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
    assert "畑: wheat wheat_farm・home_farm 12 マス（熟した 8、育ち具合 3〜7/7、空き 4）" in text
    assert "wheat 12（→ wheat・wheat_seeds、" in text
    assert "乾いた耕地" not in text


def test_dry_farmland_is_told_only_when_there_is_some():
    obs = GameObservation(
        state={
            "farm": {
                "wheat": {"planted": 8, "ripe": 0, "dry": 5, "ages": [0, 2], "mature": 7, "free": 0}
            }
        },
        candidates=(),
        health=20,
        food=20,
    )
    text = format_activity(Activity(observation=obs))
    assert "wheat 8 マス（熟した 0、育ち具合 0〜2/7、乾いた耕地 5）" in text
    assert "乾いた耕地は水が 4 マス以内になく、育ちが遅い）" in text


def test_dusk_says_how_long_until_night_not_only_until_morning():
    """「夕方（朝まで約 9 分）」を「夜が明けた」と読まれた（2026-09-27）。"""
    from ailoveshen.infrastructure.adapters.prompts.stream_context import format_time, format_time_en

    dusk = {"phase": "dusk", "time_of_day": 12600}
    assert format_time(dusk) == "夕方（暗くなってきた。夜になるまで約 0 分、そのあと朝まで約 9 分）"
    assert format_time_en(dusk).startswith("dusk (night in 0 minutes")
    assert format_time({"phase": "night", "time_of_day": 18000}) == "夜（朝まで約 5 分）"
