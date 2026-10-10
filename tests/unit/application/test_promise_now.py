"""「今やる」と約束したことを、次の小目標の決定が守る（docs/design/159）。"""

import pytest

from ailoveshen.application.use_cases.goal_vocabulary import GoalDecision, Serves
from ailoveshen.application.use_cases.play import _check_promised, _settle_promises
from ailoveshen.domain.entities import ObjectivePlan, PlaySession, PromisedNow
from ailoveshen.domain.value_objects import (
    ConditionStatus,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    HouseBlueprint,
    Mission,
    Side,
)

BED = GoalSpec(GoalPredicate.PLACED, item="bed", where="home", count=1)
LOG = GoalSpec(GoalPredicate.HAVE, item="log", count=3)
NIGHT = GoalSpec(GoalPredicate.THROUGH_NIGHT)
FOOD = GoalSpec(GoalPredicate.HAVE, item="food", count=8)


def _decision(spec, serves=Serves.CURRENT, together=()):
    return GoalDecision(spec=spec, reason="r", serves=serves, together=together)


def _session() -> PlaySession:
    plan = ObjectivePlan(mission=Mission("m"))
    return PlaySession(blueprint=HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2), plan=plan)


PROMISE = PromisedNow("neko", "家にベッドを置く", (BED,), job=True)
OPEN = (ConditionStatus(BED, False),)


def test_waiting_through_the_night_breaks_a_promise_to_do_it_now():
    with pytest.raises(ValueError, match="you told neko you would do .*now.*placed\\(bed, 1, home\\)"):
        _check_promised(_decision(NIGHT, Serves.SURVIVAL), (PROMISE,), OPEN)
    with pytest.raises(ValueError, match="choose a small goal that does it"):
        _check_promised(_decision(LOG), (PROMISE,), OPEN)


def test_the_small_goal_that_does_it_passes_and_so_does_survival():
    _check_promised(_decision(BED), (PROMISE,), OPEN)
    _check_promised(_decision(LOG, together=(BED,)), (PROMISE,), OPEN)
    _check_promised(_decision(FOOD, Serves.SURVIVAL), (PROMISE,), OPEN)  # 飢えを先にする


def test_the_next_step_toward_the_job_counts_and_a_job_that_cannot_progress_is_not_enforced():
    step = GoalSpec(GoalPredicate.HAVE, item="bed", count=1)
    gathering = (ConditionStatus(BED, False, next_step=step),)
    _check_promised(_decision(step), (PROMISE,), gathering)
    _check_promised(_decision(NIGHT, Serves.SURVIVAL), (PROMISE,), (ConditionStatus(BED, False, impossible=("no wool",)),))
    _check_promised(_decision(NIGHT, Serves.SURVIVAL), (PROMISE,), (ConditionStatus(BED, True),))


def test_an_objective_promise_wants_a_small_goal_that_serves_the_top_objective():
    objective = PromisedNow("neko", "家を広くする", (BED,), job=False)
    _check_promised(_decision(LOG, Serves.CURRENT), (objective,), ())
    with pytest.raises(ValueError, match="objective at the top"):
        _check_promised(_decision(NIGHT, Serves.SURVIVAL), (objective,), ())


def test_a_kept_promise_is_forgotten_and_a_missed_one_is_given_up_after_a_few_decisions(caplog):
    session = _session()
    session.promise_now(PROMISE)
    _settle_promises(session, _decision(BED), OPEN)
    assert session.promised_now == ()

    session.promise_now(PROMISE)
    for _ in range(2):
        _settle_promises(session, _decision(LOG), OPEN)
        assert session.promised_now == (PROMISE,)
    _settle_promises(session, _decision(LOG), OPEN)
    assert session.promised_now == ()


def test_an_urgent_rethink_is_not_held_by_the_night_wait():
    from ailoveshen.domain.value_objects import Goal

    session = _session()
    session.set_goal(Goal(NIGHT, "夜を越す"), "night")
    session.observe(
        GameObservation(state={}, candidates=(), health=20.0, food=20, time_phase="night", has_home=True)
    )
    session.request_rethink("a plain one")
    assert session.defers_rethink()
    session.request_rethink("place the bed now", urgent=True)
    assert not session.defers_rethink()
    session.set_goal(Goal(LOG, "x"), "day")  # 決め直した: 考え直しは使われた
    assert not session.rethink_reason
