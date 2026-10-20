"""死んだときの様子（設計書 105）: 死ぬ前の状況を教訓の条件にし、目標の決定の文にする。"""
from ailoveshen.application.use_cases.death_report import death_situation, describe_death
from ailoveshen.application.use_cases.goal_vocabulary import goal_schema
from ailoveshen.domain.value_objects import GoalPredicate
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import _format_death

REPORT = {
    "phase": "night",
    "place": "outside",
    "from_home_m": 48,
    "seconds": 38,
    "health_before": 20,
    "damage": {"skeleton": 3, "zombie": 10, "unknown": 2.5},
    "hits": 7,
    "doing": ["level the ground", "the reflex"],
}


def test_the_situation_before_the_death_not_the_one_after_respawn():
    assert death_situation(REPORT) == {"time": "night", "place": "outside", "body": "hurt"}
    # 知らない値は入れない（教訓のキーは決まった語彙だけ）
    assert death_situation({"phase": "later", "place": "mars"}) == {"body": "hurt"}
    assert death_situation(None) == {} and death_situation({}) == {}


def test_the_report_names_when_where_how_far_what_hit_and_what_it_was_doing():
    text = describe_death(REPORT)
    assert "夜、家の外で死んだ（家のドアから約 48 m）" in text
    assert "体力 20 から38 秒で7 回受けた" in text
    # 多い順
    assert text.index("zombie 10") < text.index("skeleton 3")
    assert "原因不明（遠くからの矢・爆発など） 2.5" in text
    assert "そのときの行動: level the ground → the reflex" in text
    assert describe_death(None) == "" and describe_death({}) == ""


def test_the_death_block_in_the_prompt_asks_for_a_lesson():
    text = _format_death(describe_death(REPORT))
    assert "## 死んだときの様子" in text and "夜、家の外で死んだ" in text
    assert "lesson" in text and "必ず" in text
    assert _format_death("") == ""


def test_the_schema_is_the_same_after_a_death_and_the_code_checks_the_lesson_and_the_retry():
    """スキーマは呼び出しごとに変えない（設計書 177: 違うとキャッシュが全部外れる）。教訓とやり直しは答えのあとに確かめる。"""
    import pytest

    from ailoveshen.application.use_cases.goal_vocabulary import parse_decision
    from ailoveshen.application.use_cases.play import _check_remedy
    from ailoveshen.domain.value_objects import GoalSpec

    failed = GoalSpec(GoalPredicate.HAVE, item="stone", count=3)
    schema = goal_schema(after_failure=True)
    assert {"diagnosis", "remedy"} <= set(schema["required"]) and "lesson" not in schema["required"]
    assert schema["properties"]["remedy"]["enum"] == ["retry", "change"]
    assert goal_schema(after_failure=True) == schema

    change = parse_decision(
        {"diagnosis": "d", "remedy": "change", "predicate": "have", "item": "oak_log", "count": 4, "serves": "current"}
    )
    with pytest.raises(ValueError, match="you died: write a lesson"):
        _check_remedy(change, failed, 1, retry_allowed=False, died=True)
    _check_remedy(change, failed, 1, retry_allowed=False, died=False)
    retry = parse_decision(
        {"diagnosis": "d", "remedy": "retry", "advice": "a", "predicate": "have", "item": "stone", "count": 3,
         "serves": "current"}
    )
    with pytest.raises(ValueError, match="retry is not available"):
        _check_remedy(retry, failed, 1, retry_allowed=False)
    _check_remedy(retry, failed, 1, retry_allowed=True)
