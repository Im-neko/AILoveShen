"""建物・施設の呼び名が、ボードとプロンプトに出る（設計書 160）。識別子は変えない。"""

from ailoveshen.domain.value_objects import (
    Activity,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    Mission,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import format_activity
from ailoveshen.presentation.web.goal_board import goal_label, goals_snapshot

WORKSHOP = GoalSpec(GoalPredicate.BUILT, name="workshop")
FARM = GoalSpec(GoalPredicate.FACILITY, name="wheat_farm")
ON_BUILD = GoalSpec(GoalPredicate.PLACED, item="chest", where="build:workshop", count=1)


def _obs(titles=()) -> GameObservation:
    return GameObservation(
        state={"self": {"health": 20}},
        candidates=(),
        health=20.0,
        food=20,
        goal=GoalStatus(met=False, remaining=5),
        titles=titles,
    )


def test_the_board_shows_the_title_and_keeps_the_name_without_one():
    titles = (("workshop", "キンコウの工房"), ("wheat_farm", "黄昏の畑"))
    activity = Activity(mission=Mission("街"), goal=Goal(WORKSHOP, "x"), observation=_obs(titles))
    assert goals_snapshot(activity)["goal"]["label"] == "「キンコウの工房」を建てる"

    plain = Activity(mission=Mission("街"), goal=Goal(WORKSHOP, "x"), observation=_obs())
    assert goals_snapshot(plain)["goal"]["label"] == "「workshop」を建てる"
    # スナップショットの外では、呼び名を持ち越さない
    assert goal_label(FARM) == "wheat_farm を作る"
    assert goals_snapshot(Activity(goal=Goal(FARM, "x"), observation=_obs(titles)))["goal"]["label"] == "黄昏の畑 を作る"
    assert goals_snapshot(Activity(goal=Goal(ON_BUILD, "x"), observation=_obs(titles)))["goal"]["label"] == "「キンコウの工房」にチェストを置く"


def test_the_prompt_lists_the_titles_and_says_to_speak_them_but_to_keep_names_in_goals():
    activity = Activity(
        mission=Mission("街"), goal=Goal(WORKSHOP, "x"), observation=_obs((("workshop", "キンコウの工房"),))
    )
    text = format_activity(activity)
    assert "workshop=キンコウの工房" in text
    assert "話すときはこの呼び名で言う" in text and "元の名前を使う" in text
    assert "built(workshop)" in text  # 目標の書き方は識別子のまま
    assert "呼び名" not in format_activity(Activity(mission=Mission("街"), observation=_obs()))
