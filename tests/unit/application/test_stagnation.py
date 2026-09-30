"""Stagnation（位置・持ち物・小目標の進み具合が変わらないまま手に時間がかかる、docs/design/51）のテスト。"""

from dataclasses import replace

from ailoveshen.application.use_cases.stagnation import Stagnation
from tests.unit.application.test_play_use_cases import _obs


def _state(x=0, inventory=None):
    return {"self": {"position": {"x": x, "y": 70, "z": 0}, "health": 20, "food": 20}, "inventory": inventory or {}}


def test_nothing_changes_while_two_actions_take_ten_seconds_is_stagnant():
    s = Stagnation(10)
    obs = _obs()
    assert s.observe(_state(), obs) == 0  # 最初の状態
    s.record("goto(x=1, y=2, z=3) (1.00) → ok: arrived", 6)
    assert s.observe(_state(), obs) == 0  # まだ 10 秒たたない
    s.record("dig(x=1, y=2, z=3) (1.00) → failed: refused", 6)
    assert s.observe(_state(), obs) == 12
    assert len(s.actions()) == 2


def test_the_time_the_model_takes_to_choose_is_not_counted():
    """耕す→種をまく: 手は 1〜2 秒で、選ぶのに何十秒かかっても、動かないとは言わない。"""
    s = Stagnation(10)
    obs = _obs()
    s.observe(_state(), obs)
    s.record("equip(item=wooden_hoe) (1.00) → ok: holding wooden_hoe", 0.4)
    s.observe(_state(), obs)  # ここまでに LLM が何十秒かけても、時計は見ない
    s.record("till() (1.00) → ok: tilled farmland", 2.5)
    assert s.observe(_state(), obs) == 0


def test_one_long_action_is_not_stagnation_and_a_change_starts_over():
    s = Stagnation(10)
    obs = _obs()
    s.observe(_state(), obs)
    s.record("goto → ok", 30)
    assert s.observe(_state(), obs) == 0  # 手が 1 つしかない
    s.record("dig → ok", 5)
    assert s.observe(_state(), obs) > 0
    assert s.observe(_state(inventory={"oak_log": 1}), obs) == 0  # 持ち物が変わった
    assert s.actions() == []
    s.record("a", 6)
    s.record("b", 6)
    moved = replace(obs, goal=replace(obs.goal, remaining=obs.goal.remaining - 1))
    assert s.observe(_state(inventory={"oak_log": 1}), moved) == 0  # 進み具合が変わった


def test_waiting_and_sleeping_are_not_counted():
    s = Stagnation(10)
    obs = _obs()
    s.observe(_state(), obs)
    for line in ("wait inside until morning (0.90) → ok: waited", "sleep in the bed (0.90) → ok: slept", "wait() (1.00) → ok"):
        s.record(line, 60)
    assert s.observe(_state(), obs) == 0


def test_zero_seconds_turns_it_off():
    s = Stagnation(0)
    obs = _obs()
    s.observe(_state(), obs)
    s.record("a", 99)
    s.record("b", 99)
    assert s.observe(_state(), obs) == 0


def test_waiting_steps_are_not_counted_however_their_line_starts():
    """Jev が選んだ待ちは「do_suggestion(id=wait) …」で始まる。行の頭ではなく、手の動詞で数えない。"""
    s = Stagnation(10)
    obs = _obs()
    s.observe(_state(), obs)
    for _ in range(4):
        s.record("do_suggestion(id=wait) (0.90) → ok: waited (time 13206)", 10.0, waiting=True)
    assert s.observe(_state(), obs) == 0
    assert s.actions() == []
