"""目標の語彙（調査の述語と、街の場所の選択）のテスト。"""

import pytest

from ailoveshen.application.use_cases.goal_vocabulary import (
    goal_schema,
    parse_proposal,
    parse_site,
    parse_spec,
    predicates_now,
    site_schema,
)
from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import (
    GameObservation,
    GoalPredicate,
    GoalSpec,
    MidGoal,
    Mission,
    TownSite,
)

SURVEYED = GoalSpec(GoalPredicate.SURVEYED, count=9)

ROWS = [{"id": "here", "x": 0, "z": 0}, {"id": "E", "x": 96, "z": 0}]


def _obs(survey=None) -> GameObservation:
    return GameObservation(
        state={"survey": survey}, candidates=(), health=20.0, food=20, has_home=True
    )


class TestSurveyed:
    """surveyed: コードが足す調査の中目標と、それを進める小目標。"""

    def test_offered_only_while_a_mid_goal_asks_for_it(self):
        """調査の中目標がある間だけ、小目標として選べる（足した直後で、ブリッジにまだ
        調査の計画がなくても）。"""
        plan = MidGoalPlan(mission=Mission("街"))
        assert GoalPredicate.SURVEYED not in predicates_now(_obs(), plan)
        survey = plan.add("街の場所を探す", (SURVEYED,), prepares_town=True)
        assert GoalPredicate.SURVEYED in predicates_now(_obs(), plan)
        plan.complete(survey.id)
        assert GoalPredicate.SURVEYED not in predicates_now(_obs(), plan)

    def test_parsed_with_its_count(self):
        """小目標の surveyed は数を持つ。中目標の条件にもなれる（コードが足す）。"""
        spec = parse_spec({"predicate": "surveyed", "count": 9})
        assert spec == GoalSpec(GoalPredicate.SURVEYED, count=9)
        MidGoal("m1", "街の場所を探す", (spec,), prepares_town=True)

    def test_the_llm_cannot_write_it_as_a_condition(self):
        """LLM が書く中目標の条件には出さず、書いても断る。"""
        schema = goal_schema([GoalPredicate.HAVE], [])
        condition = schema["properties"]["plan_changes"]["items"]["properties"]["conditions"]
        assert "surveyed" not in condition["items"]["properties"]["predicate"]["enum"]
        with pytest.raises(ValueError, match="surveyed cannot be a condition"):
            parse_proposal(
                {"title": "調べる", "conditions": [{"predicate": "surveyed", "count": 9}]}
            )


class TestSiteChoice:
    """街の場所の選択のテスト。"""

    def test_only_surveyed_sites(self):
        """選べるのは調べた候補地だけ。"""
        assert site_schema(["here", "E"])["properties"]["site_id"]["enum"] == ["here", "E"]
        site = parse_site({"site_id": "E", "reason": "石が多い", "town_name": "いしのまち"}, ROWS)
        assert site == TownSite("E", 96, 0, "石が多い", "いしのまち")
        assert site.moving
        with pytest.raises(ValueError, match="NW is not one of the surveyed sites"):
            parse_site({"site_id": "NW", "reason": "r", "town_name": "n"}, ROWS)

    def test_a_reason_and_a_name_are_needed(self):
        """理由（配信で話す）と街の名前がない選択は断る。"""
        with pytest.raises(ValueError, match="reason"):
            parse_site({"site_id": "here", "reason": " ", "town_name": "n"}, ROWS)
        assert not parse_site({"site_id": "here", "reason": "r", "town_name": "n"}, ROWS).moving


class TestDigDepth:
    """dig_depth: 埋まった石・鉱石まで掘り下げてよい深さ（Gemini が小目標で決める）。"""

    def test_parsed_and_sent_to_the_bridge(self):
        """小目標に付けた深さは、ブリッジに送る形と短い表記に入る。付けなければ入らない。"""
        spec = parse_spec({"predicate": "have", "item": "cobblestone", "count": 3, "dig_depth": 12})
        assert spec.dig_depth == 12
        assert spec.to_dict() == {
            "predicate": "have",
            "item": "cobblestone",
            "count": 3,
            "dig_depth": 12,
        }
        assert spec.describe() == "have(cobblestone, 3, dig_depth=12)"
        plain = parse_spec({"predicate": "have", "item": "cobblestone", "count": 3})
        assert plain.dig_depth is None and "dig_depth" not in plain.to_dict()

    def test_offered_on_the_small_goal_only(self):
        """小目標のスキーマにはあり、中目標の条件（世界から判定する）にはない。"""
        schema = goal_schema([GoalPredicate.HAVE], [])
        assert schema["properties"]["dig_depth"]["maximum"] == 64
        condition = schema["properties"]["plan_changes"]["items"]["properties"]["conditions"]
        assert "dig_depth" not in condition["items"]["properties"]

    def test_negative_is_refused(self):
        """負の深さは断る。"""
        with pytest.raises(ValueError, match="dig_depth"):
            GoalSpec(GoalPredicate.HAVE, item="stone", count=1, dig_depth=-1)


def test_placed_takes_any_furniture_for_the_home():
    """「作業台も部屋に置いたら」を中目標にできる（前は placed(bed, home) だけだった）。"""
    from ailoveshen.application.use_cases.goal_vocabulary import parse_spec
    from ailoveshen.domain.value_objects import GoalPredicate

    spec = parse_spec({"predicate": "placed", "item": "crafting_table"})
    assert (spec.predicate, spec.item, spec.where) == (
        GoalPredicate.PLACED,
        "crafting_table",
        "home",
    )
    assert spec.describe() == "placed(crafting_table, home)"


def test_a_colored_bed_can_be_the_goal():
    """「ベッド青くしない？」: placed(blue_bed, home) が書ける。"""
    from ailoveshen.application.use_cases.goal_vocabulary import parse_spec

    assert (
        parse_spec({"predicate": "placed", "item": "blue_bed"}).describe()
        == "placed(blue_bed, home)"
    )
