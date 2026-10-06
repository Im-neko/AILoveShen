"""夜と、家がある夕方は決まった手順（Jev にも聞かない）。ただし腹ぺこのときは決めつけない。"""

from ailoveshen.application.use_cases.goal_chooser import survival_options
from ailoveshen.domain.value_objects import Supply, SupplyTier
from ailoveshen.domain.value_objects import GameObservation, GoalPredicate, GoalSpec


def _obs(**kwargs) -> GameObservation:
    return GameObservation(state={}, candidates=(), health=20.0, food=20, **kwargs)


def _forced(obs: GameObservation) -> list[GoalPredicate]:
    return [o.spec.predicate for o in survival_options(obs) if o.forced]


def test_night_outside_is_through_night_without_asking():
    assert _forced(_obs(time_phase="night", has_home=True)) == [GoalPredicate.THROUGH_NIGHT]
    assert _forced(_obs(time_phase="night", has_home=False)) == [GoalPredicate.THROUGH_NIGHT]


def test_dusk_with_a_home_is_going_home_without_asking():
    assert _forced(_obs(time_phase="dusk", has_home=True)) == [GoalPredicate.AT_HOME]


def test_dusk_already_inside_the_home_is_staying_through_the_night():
    """もう家の中の夕方: at_home は済みで選べない。外の作業を選ばせず、夜が明けるまで家で過ごす（2026-09-30 18:08）。"""
    obs = _obs(time_phase="dusk", has_home=True, inside_home=True)
    assert _forced(obs) == [GoalPredicate.THROUGH_NIGHT]
    assert GoalPredicate.AT_HOME not in [o.spec.predicate for o in survival_options(obs)]
    hungry = _obs(time_phase="dusk", has_home=True, inside_home=True, needs=("hunger: food 4",))
    assert _forced(hungry) == []  # 腹ぺこなら食べる小目標と比べる


def test_dusk_without_a_home_is_left_to_the_choice():
    """家を建てている最中かもしれない: 夕方は決めつけない。"""
    assert _forced(_obs(time_phase="dusk", has_home=False)) == []


def test_underground_and_day_have_no_routine():
    assert _forced(_obs(time_phase="night", underground=True)) == []
    assert _forced(_obs(time_phase="day", has_home=True)) == []


def test_starving_is_weighed_against_the_night():
    assert _forced(_obs(time_phase="night", has_home=True, needs=("hunger: food 4",))) == []


def test_inside_the_home_at_night_nothing_is_forced():
    """家の中では、家でできる作業や敵を倒しに出る cleared を選べる（決まった手順は安全な所へ行くまで）。"""
    assert _forced(_obs(time_phase="night", has_home=True, inside_home=True)) == []


# 食べ物は 8 個を下回ったら 16 個まで、松明は 16 本を下回ったら 32 本まで（段階の切り替えは test_supply_tiers.py）
TIER = SupplyTier("test", torch=Supply(16, 32, 32), food=Supply(40, 80, 80))


def test_a_forced_torch_comes_before_the_night_routine():
    """暗くて松明がなく作れない外の夜: 先に松明（暗いと外の行動は止まる）。"""
    obs = GameObservation(
        state={"inventory": {"coal": 1, "stick": 2}},
        candidates=(),
        health=20.0,
        food=20,
        time_phase="night",
        has_home=True,
        needs=("dark here",),
    )
    options = survival_options(obs, TIER)
    forced = [o.spec.item for o in options if o.forced]
    assert forced[:1] == ["torch"]


# 2026-09-30 の持ち物（土 447 と丸石 320 で 36 スロットが埋まった）
FULL = {
    "dirt": 447, "cobblestone": 320, "granite": 35, "diorite": 33, "andesite": 7, "gravel": 13,
    "white_tulip": 33, "orange_tulip": 8, "light_gray_wool": 1, "white_wool": 1, "stone_sword": 1,
    "wooden_sword": 1, "torch": 9, "coal": 3, "oak_log": 2, "oak_planks": 2, "raw_copper": 7,
}


def _chest_obs(inventory: dict, **kwargs) -> GameObservation:
    return GameObservation(
        state={"inventory": inventory}, candidates=(), health=20.0, food=20, **kwargs
    )


def test_a_home_without_a_chest_offers_one_once_the_inventory_fills_up():
    """整地で土や石が溜まり続け、預ける先がなくて食料も拾えなくなった（2026-09-30）: 強制はせず、選べるようにする。"""
    options = [o for o in survival_options(_chest_obs(FULL, has_home=True)) if o.spec.item == "chest"]
    assert len(options) == 1
    assert options[0].spec.predicate == GoalPredicate.PLACED and options[0].spec.where == "home"
    assert not options[0].forced


def test_no_chest_is_offered_with_room_left_a_chest_or_no_home():
    assert not [o for o in survival_options(_chest_obs({"dirt": 64}, has_home=True)) if o.spec.item == "chest"]
    assert not [o for o in survival_options(_chest_obs(FULL, has_home=True, chest_in_home=True)) if o.spec.item == "chest"]
    assert not [o for o in survival_options(_chest_obs(FULL, has_home=False)) if o.spec.item == "chest"]


def test_at_home_sent_back_as_met_inside_the_home_at_dusk_names_through_night():
    """`at_home` は済みで差し戻される: 家にいたいなら through_night（外の作業が通って死んだ、2026-09-30）。"""
    from types import SimpleNamespace

    from ailoveshen.application.use_cases.play import _stay_note

    at_home = SimpleNamespace(spec=GoalSpec(GoalPredicate.AT_HOME))
    torch = SimpleNamespace(spec=GoalSpec(GoalPredicate.HAVE, item="torch", count=4))
    inside = _obs(time_phase="dusk", has_home=True, inside_home=True)
    assert "through_night" in _stay_note(at_home, inside)
    assert "through_night" in _stay_note(at_home, _obs(time_phase="night", has_home=True, inside_home=True))
    assert _stay_note(torch, inside) == ""
    assert _stay_note(at_home, _obs(time_phase="day", has_home=True, inside_home=True)) == ""
    assert _stay_note(at_home, _obs(time_phase="dusk", has_home=True, inside_home=False)) == ""


def _stock_obs(points: int, **kwargs) -> GameObservation:
    """points: 手持ちの食べ物の満腹度（個数は 5 点 1 個で数える）。"""
    return GameObservation(
        state={"supplies": {"food": points // 5, "food_points": points, "torch": 20}},
        candidates=(),
        health=20.0,
        food=20,
        **kwargs,
    )


def test_a_low_food_stock_is_topped_up_by_day_before_getting_hungry():
    """食べ物が満腹度 40 を下回ったら、空腹になる前に昼のうちに 80 まで集める。1 個もなければ聞かずに選ぶ。"""
    some = [o for o in survival_options(_stock_obs(25), TIER) if o.spec.item == "food"]
    assert [(o.spec.count, o.forced) for o in some] == [(5 + 11, False)]  # 手持ち 5 個 + あと 55 点 = 11 個
    none = [o for o in survival_options(_stock_obs(0), TIER) if o.spec.item == "food"]
    assert [(o.spec.count, o.forced) for o in none] == [(16, True)]
    assert not [o for o in survival_options(_stock_obs(40), TIER) if o.spec.item == "food"]


def test_the_food_stock_is_not_offered_at_night_underground_or_when_already_hungry():
    for kwargs in (
        {"time_phase": "dusk"},
        {"time_phase": "night"},
        {"underground": True},
        {"needs": ("hunger urgent (5/20)",)},
    ):
        items = [o.spec.count for o in survival_options(_stock_obs(0, **kwargs), TIER) if o.spec.item == "food"]
        assert 16 not in items, kwargs
    # 空腹のときは、満タン（20 点）にして段階の下限（40 点）に届くぶん
    hungry = survival_options(_stock_obs(0, needs=("hunger urgent (5/20)",)), TIER)
    assert [o.spec.count for o in hungry if o.spec.item == "food"] == [8]
