"""Repetition（同じ手の並びが続いたら技にする機会を見せる、docs/design/22 §13）のテスト。"""

from unittest.mock import AsyncMock

from ailoveshen.application.use_cases.play import _fitting_skill, _verb_of
from ailoveshen.application.use_cases.repetition import NUDGES, Repeat, Repetition
from ailoveshen.domain.value_objects import Activity, Candidate, SkillInfo, ToolCall, ToolOutcome
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def _record(r, verbs, ok=True):
    for v in verbs:
        r.record(v, ok)


def test_till_then_sow_three_times_is_a_repeat():
    r = Repetition()
    _record(r, ["till", "sow"] * 2)
    assert r.find() is None
    _record(r, ["till", "sow"])
    assert r.find() == Repeat(("till", "sow"), 3)


def test_equip_and_wait_are_not_part_of_the_pattern():
    r = Repetition()
    for _ in range(3):
        _record(r, ["equip", "till", "wait", "sow"])
    assert r.find() == Repeat(("till", "sow"), 3)


def test_a_failure_cuts_the_repeat_and_a_new_goal_starts_over():
    r = Repetition()
    _record(r, ["till", "sow"] * 2)
    r.record("till", False)
    _record(r, ["sow", "till", "sow"])
    assert r.find() is None
    _record(r, ["till", "sow"] * 3)
    assert r.find() is not None
    r.reset()
    assert r.find() is None


def test_one_step_needs_more_repeats_than_a_sequence():
    r = Repetition()
    _record(r, ["dig"] * 5)
    assert r.find() is None  # 木を 5 本切るのはふつう
    r.record("dig", True)
    assert r.find() == Repeat(("dig",), 6)


def test_a_pattern_starting_mid_way_is_found_by_its_tail_and_other_steps_break_it():
    r = Repetition()
    _record(r, ["goto", "till", "sow", "till", "sow", "till", "sow"])
    assert r.find() == Repeat(("till", "sow"), 3)
    r = Repetition()
    _record(r, ["till", "sow", "till", "sow", "till", "dig", "sow"])
    assert r.find() is None


def test_the_same_pattern_is_shown_a_few_times_and_again_after_the_cooldown():
    clock = Clock()
    r = Repetition(clock)
    _record(r, ["till", "sow"] * 3)
    for _ in range(NUDGES):
        assert r.find() is not None
    assert r.find() is None  # 無視されても、何度も見せない
    clock.now = 3600
    assert r.find() is not None


def test_verbs_come_from_the_candidates_for_do_suggestion_and_not_from_skills_or_lookups():
    candidates = [
        Candidate("till the ground of the farm f with a hoe", {"verb": "till"}),
        Candidate("sow wheat_seeds on the farm f", {"verb": "sow"}),
    ]

    def outcome(name, args):
        return ToolOutcome(ToolCall(name, args), True, "ok", 1.0)

    assert _verb_of(outcome("do_suggestion", {"id": "sow wheat_seeds on the farm f"}), candidates) == "sow"
    assert _verb_of(outcome("do_suggestion", {"id": "gone"}), candidates) is None
    assert _verb_of(outcome("till", {}), candidates) == "till"
    assert _verb_of(outcome("run_skill", {"name": "farm"}), candidates) is None
    assert _verb_of(outcome("find_blocks", {"block": "dirt"}), candidates) is None
    assert _verb_of(None, candidates) is None


def test_a_skill_that_fits_the_goal_item_and_needs_no_arguments_is_named():
    no_args = {"type": "object", "properties": {}}
    farm = SkillInfo("sow_farm", "畑を耕して種をまく", 1, params=no_args, expects={"predicate": "farmed", "item": "wheat", "count": "+8"})
    needs = SkillInfo(
        "sow_n",
        "n マス",
        1,
        params={"type": "object", "properties": {"n": {}}, "required": ["n"]},
        expects={"predicate": "farmed", "item": "wheat"},
    )
    goal = AsyncMock()
    goal.spec.item = "wheat"
    assert _fitting_skill([needs, farm], goal) == "sow_farm"
    goal.spec.item = "carrot"
    assert _fitting_skill([needs, farm], goal) == ""
    assert _fitting_skill([farm], None) == ""


def test_the_tool_prompt_shows_the_repeat_and_says_to_write_or_run_a_skill():
    builder = GamePromptTemplateBuilder()
    state = {"surroundings": {}, "mobs": []}
    quiet = builder.build_tool_prompt(Activity(), state, (), (), skills=[])
    assert "繰り返している" not in quiet
    new = builder.build_tool_prompt(
        Activity(), state, (), (), skills=[], repeated=("till", "sow"), repeated_times=3
    )
    assert "till → sow を 3 回" in new and "write_skill" in new
    known = builder.build_tool_prompt(
        Activity(), state, (), (), skills=[], repeated=("till", "sow"), repeated_times=3, repeated_skill="sow_farm"
    )
    assert "sow_farm" in known and "run_skill" in known and "write_skill を呼ぶ" not in known
