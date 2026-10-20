"""覚えた候補（docs/design/52）のテスト: LLM が選んでうまくいった手を覚え、似た状況で Jev の選択肢に出す。"""

from unittest.mock import Mock

from ailoveshen.application.ports.output.learned_candidate_store import ILearnedCandidateStore
from ailoveshen.application.use_cases.learned_candidates import MATCH_KEYS, LearnedCandidates, offers_of, learnable
from ailoveshen.application.use_cases.step_picker import LEARNED_PREFIX, StepPicker
from ailoveshen.domain.value_objects import (
    ActionDecision,
    Candidate,
    GameObservation,
    GoalStatus,
    ToolCall,
)
from ailoveshen.infrastructure.adapters.storage.json_learned_candidate_store import JsonLearnedCandidateStore

SITUATION = {
    "goal": "have",
    "item": "wheat",
    "place": "outside",
    "offers": "wait",
    "time": "day",
    "body": "ok",
}
GOAL = "have(wheat, 24)"
HOE = ToolCall("eat", {"item": "wooden_hoe"}, intent="クワを作り直す")


class MemoryStore(ILearnedCandidateStore):
    def __init__(self):
        self.saved = None

    def load(self):
        return self.saved

    def save(self, learned, next_id):
        self.saved = (list(learned), next_id)


def _obs(inventory=None, remaining=5, met=False) -> GameObservation:
    return GameObservation(
        state={"inventory": inventory or {}},
        candidates=(),
        health=20.0,
        food=20,
        goal=GoalStatus(met=met, remaining=remaining),
    )


def _learn(book, call=HOE, before=None, after=None, situation=SITUATION):
    book.begin(situation, call, _obs(before), GOAL, ok=True)
    book.settle(_obs(after if after is not None else {"wooden_hoe": 1}), GOAL)


def test_only_calls_without_coordinates_that_are_not_waiting_are_remembered():
    assert not learnable(ToolCall("craft", {"item": "wooden_hoe"}))  # ソルバーが候補に出す
    assert learnable(ToolCall("eat", {"item": "bread"}))
    assert learnable(ToolCall("smelt", {"input": "raw_iron"}))
    assert not learnable(ToolCall("withdraw", {"item": "wheat_seeds", "count": 4}))  # ブリッジの候補が数を決める
    assert not learnable(ToolCall("deposit", {"item": "oak_log"}))
    assert learnable(ToolCall("till", {}))
    assert not learnable(ToolCall("till", {"x": 1, "y": 64, "z": 2}))  # 場所が要る
    assert not learnable(ToolCall("dig", {"x": 1, "y": 64, "z": 2}))
    assert not learnable(ToolCall("goto", {"x": 1, "y": 64, "z": 2}))
    assert not learnable(ToolCall("wait"))
    assert not learnable(ToolCall("do_suggestion", {"id": "craft stick x1"}))  # すでにブリッジの候補
    assert not learnable(ToolCall("attack", {"entity": 5}))


def test_a_step_that_changed_the_inventory_is_remembered_and_recalled_in_the_same_situation():
    store = MemoryStore()
    book = LearnedCandidates(store)
    _learn(book)
    (move,) = book.recall(SITUATION)
    assert move.tool == "eat" and move.args == {"item": "wooden_hoe"} and move.worked == 1
    assert move.intent == "クワを作り直す"
    assert store.saved[0][0].id == move.id  # 保存する
    # 目標・対象・場所が同じなら思い出す（時刻や体の様子は問わない）
    assert book.recall({**SITUATION, "time": "night", "body": "hungry"}) == [move]
    assert book.recall({**SITUATION, "item": "log"}) == []
    assert book.recall({**SITUATION, "place": "home"}) == []
    assert book.recall({**SITUATION, "offers": "dig+explore"}) == []  # 選択肢の種類が違う場面には出さない
    assert book.recall({**SITUATION, "goal": "placed"}) == []


def test_a_step_that_changed_nothing_or_failed_is_not_remembered():
    book = LearnedCandidates(MemoryStore())
    _learn(book, before={"stick": 2}, after={"stick": 2}, )  # 何も変わらない
    book.begin(SITUATION, HOE, _obs(), GOAL, ok=False)
    book.settle(_obs({"wooden_hoe": 1}), GOAL)  # 失敗した手は見ない
    _learn(book, ToolCall("wait"), after={"x": 1})  # 待つは覚えない
    assert book.learned == ()


def test_progress_of_the_same_small_goal_counts_but_another_goal_does_not():
    book = LearnedCandidates(MemoryStore())
    book.begin(SITUATION, ToolCall("eat", {"item": "bread"}), _obs({"bread": 1}, remaining=5), GOAL, ok=True)
    book.settle(_obs({"bread": 1}, remaining=4), GOAL)  # 持ち物は同じでも進み具合が進んだ
    assert len(book.learned) == 1
    other = LearnedCandidates(MemoryStore())
    other.begin(SITUATION, ToolCall("eat", {"item": "bread"}), _obs({"bread": 1}, remaining=5), GOAL, ok=True)
    other.settle(_obs({"bread": 1}, remaining=1), "have(log, 4)")  # 別の小目標の数は比べない
    assert other.learned == ()


def test_offers_are_the_kinds_of_options_without_the_ones_every_scene_has():
    cands = [Candidate("dig stone at 1,2,3", {"verb": "dig"}), Candidate("explore north", {"verb": "explore"}),
             Candidate("dig stone at 4,5,6", {"verb": "dig"}), Candidate("equip stone_sword", {"verb": "equip"}),
             Candidate("eat bread", {"verb": "eat"}), Candidate("pick up x", {"verb": "pickup"})]
    assert offers_of(cands) == "dig+explore"
    assert offers_of([Candidate("wait", {"verb": "wait"}), Candidate("equip a", {"verb": "equip"})]) == "wait"
    assert offers_of([]) == "none"


def test_quantities_are_not_kept_so_the_same_move_with_another_number_is_one_move():
    book = LearnedCandidates(MemoryStore())
    _learn(book, ToolCall("eat", {"item": "stick", "times": 1}))
    _learn(book, ToolCall("eat", {"item": "stick", "times": 4}), after={"stick": 8})
    (move,) = book.learned
    assert move.args == {"item": "stick"} and move.worked == 2


def test_candidates_saved_by_the_old_way_are_dropped_when_loaded():
    from ailoveshen.domain.value_objects import LearnedCandidate

    old_key = (("goal", "have"), ("item", "food"), ("place", "home"))
    stale = LearnedCandidate(id="m1", situation=old_key, tool="eat", args={"item": "stick"})
    no_longer = LearnedCandidate(id="m2", situation=tuple((k, "") for k in MATCH_KEYS), tool="withdraw", args={"item": "oak_planks"})
    fine = LearnedCandidate(id="m3", situation=tuple((k, "") for k in MATCH_KEYS), tool="eat", args={"item": "wooden_hoe"})
    store = MemoryStore()
    store.saved = ([stale, no_longer, fine], 4)
    book = LearnedCandidates(store)
    book.load()
    assert [m.id for m in book.learned] == ["m3"]
    assert [m.id for m in store.saved[0]] == ["m3"]


def test_the_same_move_again_strengthens_it_instead_of_adding_another():
    book = LearnedCandidates(MemoryStore())
    _learn(book)
    _learn(book, before={}, after={"wooden_hoe": 1})
    (move,) = book.learned
    assert move.worked == 2


def test_a_recalled_move_that_stops_working_is_dropped_and_one_that_works_is_kept():
    book = LearnedCandidates(MemoryStore())
    _learn(book)
    (move,) = book.learned

    def replay(after):
        book.begin(SITUATION, HOE, _obs({"a": 1}), GOAL, ok=True, candidate=book.learned[0])
        book.settle(_obs(after), GOAL)

    replay({"a": 1})  # 何も変わらない: 効かなかった（1 対 1）
    assert book.learned[0].worked == 1 and book.learned[0].failed == 1
    assert book.recall(SITUATION) == [book.learned[0]]  # まだ出す
    replay({"a": 1})  # 2 回目: 効かなかった数が効いた数を超えて消える
    assert book.learned == () and book.recall(SITUATION) == []
    assert move.id  # 消えたのは 1 件だけ


def test_a_replayed_move_that_fails_to_run_counts_at_once():
    book = LearnedCandidates(MemoryStore())
    _learn(book)
    book.begin(SITUATION, HOE, _obs(), GOAL, ok=False, candidate=book.learned[0])
    assert book.learned[0].failed == 1


def test_the_book_keeps_at_most_max_learned_dropping_the_weakest():
    book = LearnedCandidates(MemoryStore(), max_learned=2)
    for item in ("a", "b", "c"):
        _learn(book, ToolCall("eat", {"item": item}))
    _learn(book, ToolCall("eat", {"item": "c"}), after={"z": 1})  # c は強くなる
    assert sorted(m.args["item"] for m in book.learned) == ["b", "c"]


def test_the_book_is_saved_and_loaded_across_restarts(tmp_path):
    path = tmp_path / "learned_candidates.json"
    book = LearnedCandidates(JsonLearnedCandidateStore(path))
    _learn(book)
    again = LearnedCandidates(JsonLearnedCandidateStore(path))
    again.load()
    assert again.learned == book.learned
    assert again.recall(SITUATION)[0].args == {"item": "wooden_hoe"}
    _learn(again, ToolCall("eat", {"item": "stone_hoe"}))
    assert {m.id for m in again.learned} == {"l1", "l2"}  # 次の id の番号も続く


def test_shown_limits_how_many_candidates_are_offered_best_first():
    book = LearnedCandidates(MemoryStore(), shown=2)
    for item in ("a", "b", "c"):
        _learn(book, ToolCall("eat", {"item": item}))
    _learn(book, ToolCall("eat", {"item": "c"}), after={"z": 1})
    assert [m.args["item"] for m in book.recall(SITUATION)][0] == "c"
    assert len(book.recall(SITUATION)) == 2


class TestOfferedToJev:
    def test_a_learned_candidate_is_an_option_and_running_it_calls_the_tool(self):
        import asyncio

        book = LearnedCandidates(MemoryStore())
        _learn(book)
        (move,) = book.learned
        selector = Mock()
        option = f"{LEARNED_PREFIX}eat(item=wooden_hoe)"

        async def select(state, offered, instructions):
            assert option in [c.action_id for c in offered]
            return ActionDecision(option, 0.9)

        selector.select = select
        picker = StepPicker(selector)
        picked = asyncio.run(
            picker.pick(
                {"goal": GOAL},
                "pick one",
                [Candidate("wait", {"verb": "wait"})],
                [],
                True,
                learned=[move],
            )
        )
        assert picked.learned == move
        assert picked.call.name == "eat" and picked.call.args == {"item": "wooden_hoe"}
        assert picked.call.intent == "クワを作り直す"


DOOR = ToolCall("use_item", {"target": "home_door", "face": "south", "sneak": False}, intent="素手でドアを押す")


def test_a_click_that_changed_the_world_is_remembered_even_when_inventory_and_progress_stay():
    """ドアを開けても持ち物も小目標の残りも変わらない（家に入るのは次の手）。use_item は何も変わらなければ失敗するので、成功そのものが「変わった」印。"""
    store = MemoryStore()
    book = LearnedCandidates(store)
    book.begin(SITUATION, DOOR, _obs({"oak_log": 3}, remaining=1), GOAL, ok=True)
    book.settle(_obs({"oak_log": 3}, remaining=1), GOAL)
    (door,) = book.recall(SITUATION)
    assert door.tool == "use_item" and door.args == {"target": "home_door", "face": "south", "sneak": False}


def test_a_tool_that_does_not_fail_when_nothing_changed_still_needs_a_change_to_be_remembered():
    book = LearnedCandidates(MemoryStore())
    book.begin(SITUATION, HOE, _obs({"oak_log": 3}, remaining=1), GOAL, ok=True)
    book.settle(_obs({"oak_log": 3}, remaining=1), GOAL)
    assert book.recall(SITUATION) == []
