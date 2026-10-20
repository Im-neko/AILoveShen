"""自分がやったこと（ObjectivePlan.history）のテスト。"""

from ailoveshen.domain.entities import HISTORY_KEPT, ObjectivePlan
from ailoveshen.domain.value_objects import Deed, GoalPredicate, GoalSpec, Mission


class TestObjectivePlanHistory:
    """自分がやったこと（終わった大目標の記録）のテスト。"""

    def _plan(self, **kw):
        return ObjectivePlan(mission=Mission("街にしていく"), **kw)

    def test_finished_objectives_are_remembered_beyond_the_recent_ones(self):
        """最近終わったものは `finished_shown` 件まで。履歴は全部残り、日と断念の理由も持つ。"""
        plan = self._plan(finished_shown=1)
        cond = (GoalSpec(GoalPredicate.BUILT),)
        for title in ("家", "畑", "倉庫"):
            plan.add(title, cond)
        plan.note_day(2)
        plan.complete("m1")
        plan.note_day(5)
        plan.complete("m2")
        plan.drop("m3", "場所がない")

        assert [g.title for g in plan.finished] == ["倉庫"]
        assert [(d.title, d.done, d.day, d.because) for d in plan.history] == [
            ("家", True, 2, "its conditions hold"),
            ("畑", True, 5, "its conditions hold"),
            ("倉庫", False, 5, "場所がない"),
        ]

    def test_the_day_is_unknown_until_observed_and_the_oldest_are_forgotten(self):
        """日が分からないうちは None。上限を超えたら古いものから消す。"""
        plan = self._plan()
        plan.add("家", (GoalSpec(GoalPredicate.BUILT),))
        plan.complete("m1")
        assert plan.history[0].day is None

        plan.restore([], [], 1, history=[Deed(f"t{i}", True) for i in range(HISTORY_KEPT + 5)])
        plan.add("新しい", (GoalSpec(GoalPredicate.BUILT),))
        plan.complete(plan.current.id)
        assert len(plan.history) == HISTORY_KEPT
        assert plan.history[-1].title == "新しい" and plan.history[0].title == "t6"
