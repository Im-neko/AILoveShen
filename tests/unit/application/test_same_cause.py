"""同じ理由の失敗が続いているのを見つける（設計書 51 追記 2）。"""
from ailoveshen.application.use_cases.same_cause import (
    SAME_CAUSE_MIN,
    WITHDRAW_AFTER,
    WITHDRAW_SECONDS,
    HandLedger,
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


MINE = "make a mine down to y -58 near the home and dig it (for diamond)"
NO_SHAFT = "failed: could not design a mine down to y -58 near the home: no safe shaft down to y -58 (the deepest safe one reaches y -3: a cave or a drop under 97,-4,-280)"


def _hand(hand=MINE, result=NO_SHAFT, ok=False):
    return ToolOutcome(call=ToolCall("do_suggestion", {"id": hand}), ok=ok, result=result, seconds=0.0)


class _Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_a_suggestion_that_fails_for_the_same_reason_three_times_is_withdrawn_alone():
    ledger = HandLedger(_Clock())
    for _ in range(WITHDRAW_AFTER - 1):
        ledger.note(_hand())
    assert not ledger.withdrawn(MINE)  # 一度や二度の失敗は外さない
    ledger.note(_hand())
    assert ledger.withdrawn(MINE)
    assert not ledger.withdrawn("go home")  # ほかの提案は残る
    (shown,) = ledger.current()
    assert (shown.hand, shown.times) == (MINE, WITHDRAW_AFTER) and "a cave or a drop" in shown.reason


def test_the_numbers_in_a_suggestion_id_do_not_make_it_another_hand():
    ledger = HandLedger(_Clock())
    for i in range(WITHDRAW_AFTER):
        ledger.note(_hand(f"dig the mine mine_2 ({750 + i}/775 cells dug)", "failed: Took to long to decide path to goal! The goal 113,45,-251"))
    assert ledger.withdrawn("dig the mine mine_2 (754/775 cells dug)")


def test_a_success_a_different_reason_or_damage_does_not_count():
    ledger = HandLedger(_Clock())
    ledger.note(_hand())
    ledger.note(_hand())
    ledger.note(_hand(ok=True, result="dug"))
    ledger.note(_hand())
    assert not ledger.withdrawn(MINE)
    ledger = HandLedger(_Clock())
    ledger.note(_hand())
    ledger.note(_hand(result="failed: No path to the goal!"))
    ledger.note(_hand())
    assert not ledger.withdrawn(MINE)
    ledger = HandLedger(_Clock())
    for _ in range(WITHDRAW_AFTER + 2):
        ledger.note(_hand(result="failed: interrupted: took damage"))
    assert not ledger.withdrawn(MINE)


def test_other_tools_are_not_counted_and_a_withdrawn_hand_comes_back_for_one_try():
    clock = _Clock()
    ledger = HandLedger(clock)
    for _ in range(5):
        ledger.note(_out())
    assert not ledger.current()
    for _ in range(WITHDRAW_AFTER):
        ledger.note(_hand())
    clock.now += WITHDRAW_SECONDS + 1
    assert not ledger.withdrawn(MINE) and not ledger.current()  # 期限が過ぎたら出す
    ledger.note(_hand())  # 同じ理由であと 1 回失敗したら、すぐまた外す
    assert ledger.withdrawn(MINE)


def test_the_prompt_names_the_withdrawn_hand_and_points_to_write_skill_only_when_raw_is_on():
    ledger = HandLedger(_Clock())
    for _ in range(WITHDRAW_AFTER):
        ledger.note(_hand())
    args = (Activity(), {"surroundings": {}, "mobs": []}, (), ())
    raw = GamePromptTemplateBuilder(raw_bot=True).build_tool_prompt(*args, withdrawn=ledger.current())
    assert "提案から外した手" in raw and MINE in raw and "write_skill" in raw and "選び直さない" in raw
    plain = GamePromptTemplateBuilder().build_tool_prompt(*args, withdrawn=ledger.current())
    assert MINE in plain and "write_skill" not in plain.split("提案から外した手")[1]
    assert "提案から外した手" not in GamePromptTemplateBuilder(raw_bot=True).build_tool_prompt(*args)


def _skill(name="descend_stair_raw", result="skill descend_stair_raw v7 failed (done): expects not met", ok=False, tool="run_skill"):
    return ToolOutcome(call=ToolCall(tool, {"name": name}), ok=ok, result=result, seconds=0.0)


def test_a_skill_that_keeps_failing_is_withdrawn_even_when_the_reasons_differ():
    ledger = HandLedger(_Clock())
    reasons = ["expects not met: 13.1 blocks from the goal", "採掘の場所へ行けない", "y が下がらなかった"]
    for why in reasons:
        ledger.note(_skill(result=f"skill descend_stair_raw v7 failed: {why}"))
    assert ledger.withdrawn("skill descend_stair_raw")
    assert not ledger.withdrawn("skill pillar_up_stone_v3")
    (shown,) = ledger.current()
    assert shown.hand == "skill descend_stair_raw" and shown.times == WITHDRAW_AFTER


def test_a_success_or_a_rewrite_brings_the_skill_back_but_a_refused_write_does_not():
    ledger = HandLedger(_Clock())
    for _ in range(WITHDRAW_AFTER):
        ledger.note(_skill())
    assert ledger.withdrawn("skill descend_stair_raw")
    refused = ToolOutcome(ToolCall("write_skill", {"name": "descend_stair_raw"}), False, "the code is the same", 0.0, refused=True)
    ledger.note(refused)
    assert ledger.withdrawn("skill descend_stair_raw")
    ledger.note(_skill(tool="write_skill", ok=False, result="wrote descend_stair_raw v8; trial: failed"))
    assert not ledger.withdrawn("skill descend_stair_raw")  # 新しい版は、前の版の失敗で外さない
    ledger.note(_skill())
    ledger.note(_skill(ok=True, result="succeeded"))
    ledger.note(_skill())
    ledger.note(_skill())
    assert not ledger.withdrawn("skill descend_stair_raw")


def test_the_prompt_tells_how_to_bring_a_withdrawn_skill_back():
    ledger = HandLedger(_Clock())
    for _ in range(WITHDRAW_AFTER):
        ledger.note(_skill())
    args = (Activity(), {"surroundings": {}, "mobs": []}, (), ())
    text = GamePromptTemplateBuilder(raw_bot=True).build_tool_prompt(*args, withdrawn=ledger.current())
    assert "skill descend_stair_raw" in text and "try_skill で試し" in text and "write_skill で保存" in text


class _MemoryStore:
    def __init__(self):
        self.saved = None

    def load(self):
        return self.saved

    def save(self, snapshot):
        import json

        self.saved = json.loads(json.dumps(snapshot))  # JSON を通して戻る形で持つ


def test_withdrawn_hands_survive_a_restart_and_expire_by_the_wall_clock():
    clock, store = _Clock(), _MemoryStore()
    ledger = HandLedger(clock, store)
    for _ in range(WITHDRAW_AFTER):
        ledger.note(_hand())
        ledger.note(_skill())
    assert ledger.withdrawn(MINE) and ledger.withdrawn("skill descend_stair_raw")
    restarted = HandLedger(clock, store)  # 再起動: 同じ保存から戻る
    assert restarted.withdrawn(MINE) and restarted.withdrawn("skill descend_stair_raw")
    assert {h.hand for h in restarted.current()} == {MINE, "skill descend_stair_raw"}
    clock.now += WITHDRAW_SECONDS + 1
    assert not HandLedger(clock, store).withdrawn(MINE)  # 期限は過ぎている


def test_failures_below_the_limit_are_kept_across_a_restart_and_a_rewrite_clears_the_saved_skill():
    clock, store = _Clock(), _MemoryStore()
    ledger = HandLedger(clock, store)
    for _ in range(WITHDRAW_AFTER - 1):
        ledger.note(_hand())
    again = HandLedger(clock, store)
    again.note(_hand())  # 再起動をまたいで 3 回目
    assert again.withdrawn(MINE)
    for _ in range(WITHDRAW_AFTER):
        again.note(_skill())
    again.note(_skill(tool="write_skill", result="wrote"))
    assert not HandLedger(clock, store).withdrawn("skill descend_stair_raw")


def test_an_unreadable_saved_ledger_starts_empty():
    store = _MemoryStore()
    store.saved = {"fails": {"x": "oops"}, "until": {"x": "later"}}
    assert HandLedger(_Clock(), store).current() == ()
