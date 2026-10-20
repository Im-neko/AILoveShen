"""技が動いている間の経過の 1 行（設計書 186）。"""

from ailoveshen.application.use_cases.skill_progress import skill_note, start_position


def _state(y=40, x=10, seconds=14, log=("step 3 now",)):
    return {"self": {"position": {"x": x, "y": y, "z": -5}}, "skill": {"name": "s", "seconds": seconds, "log": list(log)}}


def test_it_says_how_far_the_skill_went_and_its_last_log_lines():
    start = start_position(_state(y=60, x=10))
    note = skill_note("stair_down", _state(y=40, x=14, log=["a", "b", "c"]), start)
    assert note.startswith("技「stair_down」を実行して14秒")
    assert "今 x=14 y=40 z=-5" in note and "始めから20ブロック下りた" in note and "横に4m動いた" in note
    assert "技のログ: b / c" in note  # 最後の 2 行


def test_it_is_empty_when_no_skill_is_running_and_tolerates_a_missing_position():
    assert skill_note("s", {"self": {}}, None) == ""
    note = skill_note("s", {"skill": {"seconds": 3}}, None)
    assert note == "技「s」を実行して3秒"
    assert start_position({}) is None
