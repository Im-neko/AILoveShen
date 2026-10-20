"""技が失敗したときの案内（設計書 194）: 直すか、やり直すかを選べる材料。"""

from ailoveshen.application.use_cases.skill_feedback import SkillHistory
from ailoveshen.domain.value_objects import SkillRun


def run(ok=False, ended="fail", reason="no free cell"):
    return SkillRun(name="place", version=1, ok=ok, ended=ended, reason=reason)


def test_success_and_refusal_say_nothing_and_a_refusal_is_not_counted():
    h = SkillHistory()
    assert h.feedback("place", run(ok=True, ended="done", reason=""), False) == ""
    assert h.feedback("place", run(ended="refused", reason="failed 5 times"), False) == ""
    assert "1 of 2 runs succeeded" in h.feedback("place", run(), False)


def test_each_kind_of_failure_gets_its_own_advice():
    h = SkillHistory()
    assert "bug in the code" in h.feedback("a", run(ended="error", reason="boom"), False)
    assert "time limit" in h.feedback("b", run(ended="stopped", reason="ran longer than 10 minutes"), False)
    assert "not a bug" in h.feedback("c", run(ended="stopped", reason="reflex: zombie"), False)
    assert "gave up by itself" in h.feedback("d", run(ended="fail"), False)
    assert "world did not match expects" in h.feedback("e", run(ended="done", reason="expects not met: x"), False)


def test_a_skill_that_worked_before_may_just_be_the_situation():
    h = SkillHistory()
    for _ in range(3):
        h.feedback("place", run(ok=True, ended="done", reason=""), False)
    text = h.feedback("place", run(), False)
    assert "3 of 4 runs succeeded (last success 1 run ago)" in text
    assert "may be the situation" in text


def test_the_same_failure_in_a_row_says_to_change_something():
    h = SkillHistory()
    h.feedback("place", run(), False)
    assert "Change something" in h.feedback("place", run(), False)
    assert "3 times in a row" in h.feedback("place", run(), False)
    # 別の理由になれば数え直す
    assert "in a row" not in h.feedback("place", run(reason="other cause"), False)


def test_never_succeeded_after_several_runs_leans_to_fixing():
    h = SkillHistory()
    for reason in ("a", "b"):
        h.feedback("place", run(reason=reason), False)
    assert "never succeeded in 3 runs" in h.feedback("place", run(reason="c"), False)


def test_a_trial_says_try_skill_and_a_saved_skill_says_write_skill():
    h = SkillHistory()
    assert "try_skill again" in h.feedback("t", run(ended="error"), True)
    assert "write_skill with the same name" in h.feedback("s", run(ended="error"), False)


def test_a_rewrite_starts_the_record_over_but_keeps_the_same_cause_streak():
    h = SkillHistory()
    for _ in range(3):
        h.feedback("place", run(), False)
    h.rewritten("place")
    text = h.feedback("place", run(), False)
    assert "0 of 1 runs succeeded" in text  # 前のコードの 0 of 3 は引きずらない
    assert "4 times in a row" in text  # 書き直しても同じ理由で失敗している


def test_a_reflex_stop_in_the_dark_says_get_out_of_danger_instead_of_run_it_again():
    h = SkillHistory()
    stopped = run(ended="stopped", reason="reflex: zombie 5m")
    assert "run it again as it is" in h.feedback("a", stopped, False, "day")
    assert "run it again as it is" in h.feedback("b", stopped, False, "dawn")
    for phase in ("dusk", "night"):
        text = h.feedback(f"c{phase}", stopped, False, phase)
        assert f"it is {phase}" in text and "run it again as it is" not in text and "wait for the day" in text
