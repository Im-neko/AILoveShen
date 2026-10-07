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


def test_the_schema_after_a_death_requires_a_lesson_and_offers_no_retry():
    predicates = [GoalPredicate.HAVE]
    died = goal_schema(predicates, [], after_failure=True, retry_allowed=False, died=True)
    assert {"diagnosis", "remedy", "lesson"} <= set(died["required"])
    assert died["properties"]["remedy"]["enum"] == ["change"]
    assert died["properties"]["lesson"]["description"].startswith("Required")
    # 行き詰まった失敗は、教訓は任意のまま
    stuck = goal_schema(predicates, [], after_failure=True)
    assert "lesson" not in stuck["required"]
    assert stuck["properties"]["lesson"]["description"].startswith("Optional")
