"""松明と食べ物のストックを、生活インフラの段階で変える（docs/design/84）。"""

import pytest

from ailoveshen.application.use_cases.goal_chooser import (
    GoalChooser,
    current_tier,
    food_options,
    hungry_option,
    survival_options,
    torch_options,
)
from ailoveshen.domain.value_objects import (
    GameObservation,
    GoalPredicate,
    GoalSpec,
    Supply,
    SupplyTier,
)
from ailoveshen.factories.game import create_supply_tiers
from ailoveshen.infrastructure.config import load_settings
from ailoveshen.domain.value_objects import Job
from tests.unit.application.test_goal_layers import STONE, _checked, _plan
from tests.unit.application.test_play_use_cases import LOG, FakeJev, _session

EARLY = SupplyTier("early", torch=Supply(8, 16, 16), food=Supply(12, 24, 24))
SETTLED = SupplyTier(
    "settled", torch=Supply(16, 16, 32), food=Supply(40, 80, 160), after=("food",)
)
MINING = SupplyTier(
    "mining",
    torch=Supply(32, 64, 128),
    food=Supply(100, 160, 320),
    after=("lumber",),
    or_mine=True,
)
TIERS = (EARLY, SETTLED, MINING)


def _obs(food=0, torch=0, food_stored=0, torch_stored=0, chest=False, inventory=None, food_items=None, stored_items=None, **kwargs):
    """food / food_stored は満腹度。個数は指定がなければ 5 点 1 個で数える。"""
    state = {
        "supplies": {
            "food": food // 5 if food_items is None else food_items,
            "food_points": food,
            "food_stored": food_stored // 5 if stored_items is None else stored_items,
            "food_points_stored": food_stored,
            "torch": torch,
            "torch_stored": torch_stored,
        },
        "inventory": inventory if inventory is not None else {"torch": torch},
    }
    return GameObservation(
        state=state, candidates=(), health=20.0, food=20, chest_in_home=chest, **kwargs
    )


def _spec(item, count, predicate=GoalPredicate.HAVE):
    return GoalSpec(predicate, item=item, count=count)


class TestSupplyValues:
    def test_a_supply_keeps_low_below_carry_below_stock(self):
        Supply(8, 16, 16)
        for low, carry, stock in ((0, 1, 1), (9, 8, 16), (4, 16, 8)):
            with pytest.raises(ValueError):
                Supply(low, carry, stock)

    def test_a_tier_needs_a_name(self):
        with pytest.raises(ValueError):
            SupplyTier("", torch=Supply(1, 1, 1), food=Supply(1, 1, 1))

    def test_the_configured_tiers_are_valid_and_the_first_is_always_reached(self):
        tiers = create_supply_tiers(load_settings().minecraft.supplies)

        assert [t.name for t in tiers][:1] == ["early"]
        assert tiers[0].reached((), False)
        assert len({t.name for t in tiers}) == len(tiers)

    def test_names_must_be_unique(self):
        same = {"name": "a", "torch": {"low": 1, "carry": 1, "stock": 1}, "food": {"low": 1, "carry": 1, "stock": 1}}
        with pytest.raises(ValueError, match="unique"):
            create_supply_tiers([same, same])


class TestCurrentTier:
    def test_nothing_done_is_the_first_tier(self):
        """ベンチマークは定石が空: いつも最初の段階。"""
        assert current_tier(TIERS, (), False) is EARLY

    def test_a_done_playbook_step_moves_to_the_tier_that_names_it(self):
        assert current_tier(TIERS, ("shelter", "food"), False) is SETTLED
        assert current_tier(TIERS, ("shelter", "food", "lumber"), False) is MINING

    def test_a_designed_mine_means_mining_even_if_the_playbook_is_not_done(self):
        assert current_tier(TIERS, (), True) is MINING
        # `or_mine` のない段階は、鉱山だけでは着かない
        assert current_tier((EARLY, SETTLED), (), True) is EARLY

    def test_no_tiers_means_no_stock(self):
        assert current_tier((), ("food",), True) is None
        assert torch_options(_obs(), None) == []
        assert food_options(_obs(), None) == []


class TestFoodByTier:
    """食べ物は満腹度で数える。目標は個数なので、足りない満腹度を 1 個 5 点として個数にする。"""

    def test_early_asks_for_little_and_only_when_nearly_out(self):
        assert food_options(_obs(food=12, time_phase="day"), EARLY) == []
        o = food_options(_obs(food=11, food_items=3, time_phase="day"), EARLY)[0]
        # 24 点まであと 13 点 = 3 個ぶん: 手持ち 3 個に足して 6 個
        assert (o.spec, o.forced) == (_spec("food", 6), False)

    def test_early_with_nothing_is_forced_to_24_points_not_more(self):
        o = food_options(_obs(food=0, time_phase="day"), EARLY)[0]
        assert (o.spec, o.forced) == (_spec("food", 5), True)  # 24 点 / 5 = 5 個

    def test_the_same_number_of_items_can_be_few_or_many_points(self):
        """じゃがいも 8 個（満腹度 8）は足りない、焼いた肉 8 個（64）は足りる。"""
        potatoes = _obs(food=8, food_items=8, time_phase="day")
        beef = _obs(food=64, food_items=8, time_phase="day")
        assert food_options(potatoes, SETTLED)  # 40 点を下回る
        assert food_options(beef, SETTLED) == []
        o = food_options(potatoes, SETTLED)[0]
        assert o.spec == _spec("food", 8 + 15)  # あと 72 点 = 15 個 → 手持ち 8 個に足す

    def test_a_later_tier_asks_for_more(self):
        o = food_options(_obs(food=30, food_items=6, time_phase="day"), SETTLED)[0]
        assert o.spec == _spec("food", 6 + 10)  # 80 点まであと 50 点 = 10 個
        o = food_options(_obs(food=90, food_items=18, time_phase="day"), MINING)[0]
        assert o.spec == _spec("food", 18 + 14)  # 160 点まであと 70 点 = 14 個

    def test_the_home_chest_counts_toward_the_low_mark(self):
        """手持ち 30 点 + チェスト 60 点 = 90 点は、下限 80 を下回らない。チェストがなければ（家にない）手持ちだけ。"""
        low = SupplyTier("t", torch=Supply(1, 1, 1), food=Supply(80, 160, 160))
        assert food_options(_obs(food=30, food_stored=60, chest=True, time_phase="day"), low) == []
        assert food_options(_obs(food=30, food_stored=60, chest=False, time_phase="day"), low)

    def test_food_is_taken_from_the_chest_before_going_out(self):
        low = SupplyTier("t", torch=Supply(1, 1, 1), food=Supply(80, 160, 160))
        obs = _obs(food=10, food_stored=300, chest=True, time_phase="day", inside_home=True)
        [o] = food_options(obs, low)
        # 手に 160 点まであと 150 点 = 30 個。チェストには 60 個 → 手持ち 2 個に 30 個足す
        assert (o.spec, o.forced) == (_spec("food", 2 + 30), False)
        # 夜・家の外・手持ちが十分あるときは出さない
        assert food_options(_obs(food=10, food_stored=300, chest=True, time_phase="night", inside_home=True), low) == []
        assert food_options(_obs(food=10, food_stored=300, chest=True, time_phase="day", inside_home=False), low) == []
        assert food_options(_obs(food=90, food_stored=300, chest=True, time_phase="day", inside_home=True), low) == []

    def test_a_reserve_above_the_hand_is_stored_in_the_chest(self):
        tier = SupplyTier("t", torch=Supply(1, 1, 1), food=Supply(40, 80, 160))
        obs = _obs(food=80, food_stored=20, chest=True, time_phase="day")
        [o] = food_options(obs, tier)
        # 備蓄 80 点まであと 60 点 = 12 個: チェストの 4 個に足して 16 個
        assert o.spec == _spec("food", 16, GoalPredicate.STORED)
        # 手に十分持っていないうちは貯めない／貯まっていれば出さない／チェストがなければ出さない
        assert food_options(_obs(food=60, food_stored=20, chest=True, time_phase="day"), tier) == []
        assert food_options(_obs(food=80, food_stored=80, chest=True, time_phase="day"), tier) == []
        assert food_options(_obs(food=80, food_stored=20, chest=False, time_phase="day"), tier) == []


class TestHungry:
    def test_hungry_goal_fills_the_bar_and_reaches_the_low_mark(self):
        obs = _obs(food=10, food_items=2, time_phase="day", needs=("hunger urgent (5/20)",))
        o = hungry_option(obs, EARLY)
        assert o.spec == _spec("food", 2 + 2)  # 20 点まであと 10 点 = 2 個
        assert "満腹度" in o.reason
        # 段階の下限が 20 を超えるなら、そこまで
        assert hungry_option(_obs(food=0, food_items=0), MINING).spec == _spec("food", 20)
        # 段階がなければ 20 点ぶん
        assert hungry_option(_obs(food=0, food_items=0), None).spec == _spec("food", 4)


    def test_hungry_underground_with_no_food_goes_up_instead_of_searching(self):
        """洞窟には動物がいない: 地下で空腹、食べ物を 1 個も持っていなければ、探さず地上へ（家へ）。"""
        hungry = ("hunger urgent (5/20)",)
        forced = [o for o in survival_options(_obs(food=0, food_items=0, torch=8, underground=True, has_home=True, needs=hungry), EARLY) if o.forced]
        assert forced and {o.spec for o in forced} == {GoalSpec(GoalPredicate.AT_HOME)}
        assert any("食べ物がない" in o.reason for o in forced)
        # 食べ物を持っていれば、それを食べる食べ物集めのまま。地上、家なしも同じ
        for kw in ({"food": 5, "food_items": 1, "underground": True, "has_home": True}, {"underground": False, "has_home": True}, {"underground": True, "has_home": False}):
            kw = {"food": 0, "food_items": 0, "torch": 8, **kw}
            options = survival_options(_obs(needs=hungry, **kw), EARLY)
            assert not any(o.spec.predicate == GoalPredicate.AT_HOME for o in options), kw
            assert any(o.spec.predicate == GoalPredicate.HAVE and o.spec.item == "food" for o in options), kw


class TestTorchByTier:
    def test_the_mining_tier_wants_a_stack_in_hand(self):
        """採掘の段階: 32 本を下回ったら、1 スタック（64 本）まで。材料があれば作るだけ。"""
        obs = _obs(torch=20, inventory={"torch": 20, "coal": 16, "stick": 16})
        [o] = torch_options(obs, MINING)
        assert (o.spec, o.forced) == (_spec("torch", 64), True)
        # 材料がなければ、集める小目標（強制しない）
        [o] = torch_options(_obs(torch=20), MINING)
        assert (o.spec, o.forced) == (_spec("torch", 64), False)

    def test_the_early_tier_is_satisfied_with_eight(self):
        assert torch_options(_obs(torch=8), EARLY) == []
        [o] = torch_options(_obs(torch=7), EARLY)
        assert o.spec == _spec("torch", 16)

    def test_torches_in_the_chest_count_and_can_be_taken(self):
        obs = _obs(torch=0, torch_stored=64, chest=True, time_phase="day", inside_home=True)
        [o] = torch_options(obs, MINING)
        assert (o.spec, o.forced) == (_spec("torch", 64), False)
        # 暗くて手に 0 本のときは、チェストにあっても作る・取る小目標を強制する
        dark = _obs(torch=0, torch_stored=64, chest=True, needs=("dark here",))
        [o] = torch_options(dark, MINING)
        assert o.forced

    def test_dark_with_no_torch_makes_up_to_the_stock_not_four(self):
        """暗くて 0 本で引き返すとき、4 本では 40 m で尽きて、また止まった（2026-10-03）: 材料があれば備蓄の数まで。"""
        # 材料があれば、その分を作る（石炭 3 つ、棒は十分: 12 本）。備蓄の数が上限
        obs = _obs(torch=0, needs=("dark here",), inventory={"coal": 3, "stick": 9})
        [o] = torch_options(obs, MINING)
        assert (o.spec, o.forced) == (_spec("torch", 12), True)
        # チェストにあるぶんを超えては求めない
        obs = _obs(torch=0, torch_stored=40, chest=True, needs=("dark here",))
        [o] = torch_options(obs, MINING)
        assert o.spec == _spec("torch", 40)
        # 材料もチェストもなければ、備蓄（128）でなく手に持つ数（64）まで集める
        [o] = torch_options(_obs(torch=0, needs=("dark here",)), MINING)
        assert (o.spec, o.forced) == (_spec("torch", 64), True)
        # 暗くないときは、これまでどおり手に持つ数（carry）まで
        [o] = torch_options(_obs(torch=20), MINING)
        assert o.spec == _spec("torch", 64)

    def test_underground_with_few_torches_and_nothing_to_make_them_goes_up(self):
        """洞窟の中では松明の材料が集められないことが多い: 4 本より少なく、作る材料もなければ、地上に出る（家へ）。"""
        [o] = torch_options(_obs(torch=0, underground=True), MINING)
        assert (o.spec, o.forced) == (GoalSpec(GoalPredicate.AT_HOME), True)
        [o] = torch_options(_obs(torch=3, torch_stored=64, chest=True, underground=True), MINING)
        assert o.spec == GoalSpec(GoalPredicate.AT_HOME) and "家のチェストに 64 本" in o.reason
        # 材料が手元にあれば、そこで作る。4 本以上持っていれば、そのまま掘り進める
        [o] = torch_options(_obs(torch=0, underground=True, inventory={"coal": 2, "stick": 4}), MINING)
        assert o.spec.predicate == GoalPredicate.HAVE
        assert torch_options(_obs(torch=8, underground=True), EARLY) == []

    def test_the_alarm_level_does_not_depend_on_the_tier(self):
        [o] = torch_options(_obs(torch=2), EARLY)
        assert "2 本しかない" in o.reason


class TestChooserPicksTheTierFromThePlaybook:
    @pytest.mark.asyncio
    async def test_the_tier_follows_the_done_playbook_steps_and_the_mines(self, bridge):
        # 食べ物が満腹度 20: 序盤の段階（下限 12）では足りていて、定着の段階（下限 40）では足りない
        for done, mines, offered in (((), 0, False), (("food",), 0, True), (("food", "lumber"), 0, True), ((), 1, True)):
            session = _session(plan=_plan(Job(LOG, "原木"), Job(STONE, "石")))
            for name in done:
                session.plan.mark_playbook(name)
            bridge.check.side_effect = _checked(set())
            obs = GameObservation(
                state={
                    "supplies": {"food": 4, "food_points": 20, "torch": 64, "mines": mines},
                    "inventory": {"torch": 64},
                },
                candidates=(),
                health=20.0,
                food=20,
            )
            jev = FakeJev("A")
            chooser = GoalChooser(jev, bridge, supply_tiers=TIERS)
            await chooser.choose(session, obs, "met")
            criteria = len(jev.asked[0][1][0].criteria)
            # 原木・石の 2 つに、足りないときだけ食べ物の在庫が加わる
            assert criteria == (3 if offered else 2), (done, mines)


@pytest.fixture
def bridge():
    from unittest.mock import AsyncMock

    return AsyncMock()


class TestLightForLessons:
    def test_recall_is_told_whether_it_is_dark_and_how_many_torches_are_carried(self):
        from ailoveshen.application.use_cases.play import _light_of

        assert _light_of(_obs(torch=0, needs=("dark here",))) == {"dark_here": True, "torches_carried": 0}
        assert _light_of(_obs(torch=5)) == {"dark_here": False, "torches_carried": 5}


def _home_obs(**kw):
    return GameObservation(
        state={}, candidates=(), health=20, food=20, needs=(), goal=None, has_home=True, chest_in_home=True, **kw
    )


class TestMoreChests:
    """家のチェストが埋まってきたら、チェストを増やす小目標（2026-10-04）。"""

    def test_offered_when_the_free_slots_run_low(self):
        from ailoveshen.application.use_cases.goal_chooser import chest_options

        opts = chest_options(_home_obs(chest_blocks=1, chest_slots=27, chest_free=4, chest_saving=0))
        assert [o.spec.describe() for o in opts] == ["placed(chest, 2, home)"]

    def test_not_offered_when_there_is_room_or_defrag_would_free_enough_or_at_the_cap(self):
        from ailoveshen.application.use_cases.goal_chooser import chest_options

        assert not chest_options(_home_obs(chest_blocks=1, chest_slots=27, chest_free=12, chest_saving=0))
        assert not chest_options(_home_obs(chest_blocks=2, chest_slots=54, chest_free=3, chest_saving=5))
        assert not chest_options(_home_obs(chest_blocks=6, chest_slots=162, chest_free=2, chest_saving=0))
        assert not chest_options(_home_obs())  # ブリッジが数を送っていない
