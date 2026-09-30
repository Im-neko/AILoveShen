"""JsonGoalStore のテスト。"""

from ailoveshen.application.ports.output.goal_store import SavedGoal
from ailoveshen.domain.value_objects import Goal, GoalPredicate, GoalSpec
from ailoveshen.infrastructure.adapters.storage.json_goal_store import JsonGoalStore


def _saved() -> SavedGoal:
    goal = Goal(
        GoalSpec(GoalPredicate.HAVE, item="oak_log", count=4),
        reason="板材の材料",
        objective_id="m1",
        diagnosis="木が遠い",
        advice="Go west.",
        review="西に木がある",
        lessons=("木は葉の下",),
        together=(GoalSpec(GoalPredicate.HAVE, item="stick", count=2),),
        approach="ついでに枝も",
    )
    return SavedGoal(goal, "day", 5, 1, 2, 7, 3)


def test_a_saved_goal_comes_back_whole(tmp_path):
    store = JsonGoalStore(tmp_path / "data" / "goal.json")
    saved = _saved()
    store.save(saved)
    assert store.load() == saved


def test_nothing_saved_or_unreadable_gives_none(tmp_path):
    path = tmp_path / "goal.json"
    store = JsonGoalStore(path)
    assert store.load() is None
    path.write_text("{not json", encoding="utf-8")
    assert store.load() is None
    path.write_text('{"format": 99}', encoding="utf-8")
    assert store.load() is None
