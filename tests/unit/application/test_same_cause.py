"""同じ理由の失敗が続いているのを見つける（設計書 51 追記 2）。"""
from ailoveshen.application.use_cases.same_cause import (
    SAME_CAUSE_MIN,
    SAME_CAUSE_WITHHOLD,
    reason_key,
    same_cause_run,
)
from ailoveshen.domain.value_objects import Activity, ToolCall, ToolOutcome
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import GamePromptTemplateBuilder


def _out(tool="dig", result="refused: the block at 54,77,285 is 575.7m away (at most 32m)", ok=False, **args):
    return ToolOutcome(call=ToolCall(tool, args or {"x": 54, "y": 77, "z": 285}), ok=ok, result=result, seconds=0.0)


def _lookup(name: str) -> bool:
    return name in {"find_blocks", "search_memory"}


def test_numbers_are_not_part_of_the_cause():
    assert reason_key("the block at 54,77,-285 is 575.7m away (at most 32m)") == reason_key(
        "the block at 55,78,284 is 12m away (at most 32m)"
    )
    assert reason_key("no path") != reason_key("no pickaxe")


def test_three_failures_for_the_same_reason_are_named_and_the_fourth_withholds_the_tool():
    runs = [_out(result=f"refused: the block at {54 + i},77,285 is {575 + i}.7m away (at most 32m)") for i in range(4)]
    assert same_cause_run(runs[: SAME_CAUSE_MIN - 1]) is None
    three = same_cause_run(runs[:SAME_CAUSE_MIN])
    assert (three.tool, three.times, three.withheld) == ("dig", 3, False)
    four = same_cause_run(runs[:SAME_CAUSE_WITHHOLD])
    assert (four.times, four.withheld) == (4, True)


def test_a_success_a_different_reason_or_a_stop_by_the_watch_breaks_the_run():
    base = [_out() for _ in range(3)]
    assert same_cause_run([*base, _out(ok=True, result="dug")]) is None
    assert same_cause_run([*base, _out(result="failed: No path to the goal!")]) is None
    watched = _out()
    object.__setattr__(watched, "stopped_by", "the watch said stop")
    assert same_cause_run([*base, watched]) is None
    # 前に違う理由があっても、末尾の同じ理由だけを数える
    assert same_cause_run([_out(result="failed: other"), *base]).times == 3


def test_lookups_between_the_failures_neither_count_nor_break_the_run():
    """調べては同じ手を繰り返す、が一番多い形。"""
    seq = []
    for _ in range(3):
        seq += [_out(), _out("find_blocks", "[]", ok=True)]
    assert same_cause_run(seq) is None  # 調べものを数えない指定がなければ、成功が間にあって切れる
    cause = same_cause_run(seq, _lookup)
    assert cause is not None and cause.times == 3


def test_the_suggestion_tool_is_never_withheld():
    runs = [_out("do_suggestion", "failed: No path to the goal!", id=f"go to oak_log #{i}") for i in range(6)]
    cause = same_cause_run(runs)
    assert cause.times == 6 and not cause.withheld


def test_the_prompt_names_the_reason_and_says_to_check_the_minus_sign_for_a_far_refusal():
    builder = GamePromptTemplateBuilder()
    runs = [_out() for _ in range(4)]
    prompt = builder.build_tool_prompt(Activity(), {"surroundings": {}, "mobs": []}, (), tuple(runs), same_cause=same_cause_run(runs))
    assert "同じ理由で失敗し続けている（dig が 4 回続けて" in prompt
    assert "575.7m away" in prompt and "マイナス" in prompt
    assert "dig は出していない" in prompt
    none = builder.build_tool_prompt(Activity(), {"surroundings": {}, "mobs": []}, (), ())
    assert "同じ理由で失敗し続けている" not in none
    other = [_out(result="failed: No path to the goal!") for _ in range(3)]
    prompt = builder.build_tool_prompt(Activity(), {"surroundings": {}, "mobs": []}, (), tuple(other), same_cause=same_cause_run(other))
    assert "No path" in prompt and "座標を自分で書かずに" not in prompt and "出していない" not in prompt
