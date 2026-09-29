"""Stagnation（位置・持ち物・小目標の進み具合が変わらない時間、docs/design/51）のテスト。"""

from dataclasses import replace

from ailoveshen.application.use_cases.stagnation import Stagnation
from tests.unit.application.test_play_use_cases import _obs


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def _state(x=0, inventory=None):
    return {"self": {"position": {"x": x, "y": 70, "z": 0}, "health": 20, "food": 20}, "inventory": inventory or {}}


def test_nothing_changes_for_ten_seconds_after_two_actions_is_stagnant():
    clock = Clock()
    s = Stagnation(10, clock)
    obs = _obs()
    assert s.observe(_state(), obs) == 0  # 最初の状態
    s.record("goto(x=1, y=2, z=3) (1.00) → ok: arrived")
    clock.now = 8
    assert s.observe(_state(), obs) == 0  # まだ 10 秒たたない
    s.record("dig(x=1, y=2, z=3) (1.00) → failed: refused")
    clock.now = 16
    assert s.observe(_state(), obs) == 16
    assert len(s.actions()) == 2


def test_one_long_action_is_not_stagnation_and_a_change_starts_over():
    clock = Clock()
    s = Stagnation(10, clock)
    obs = _obs()
    s.observe(_state(), obs)
    s.record("goto → ok")
    clock.now = 30
    assert s.observe(_state(), obs) == 0  # 手が 1 つしかない
    s.record("dig → ok")
    clock.now = 40
    assert s.observe(_state(), obs) > 0
    clock.now = 50
    assert s.observe(_state(inventory={"oak_log": 1}), obs) == 0  # 持ち物が変わった
    assert s.actions() == []
    s.record("a")
    s.record("b")
    clock.now = 55
    moved = replace(obs, goal=replace(obs.goal, remaining=obs.goal.remaining - 1))
    assert s.observe(_state(inventory={"oak_log": 1}), moved) == 0  # 進み具合が変わった


def test_waiting_and_sleeping_are_not_counted():
    clock = Clock()
    s = Stagnation(10, clock)
    obs = _obs()
    s.observe(_state(), obs)
    for line in ("wait inside until morning (0.90) → ok: waited", "sleep in the bed (0.90) → ok: slept", "wait() (1.00) → ok"):
        s.record(line)
    clock.now = 60
    assert s.observe(_state(), obs) == 0


def test_zero_seconds_turns_it_off():
    clock = Clock()
    s = Stagnation(0, clock)
    obs = _obs()
    s.observe(_state(), obs)
    s.record("a")
    s.record("b")
    clock.now = 999
    assert s.observe(_state(), obs) == 0
