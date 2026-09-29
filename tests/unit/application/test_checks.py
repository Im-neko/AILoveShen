"""組み込みの述語で書けない頼みの判定 custom(name) のテスト（docs/design/42_custom_conditions.md）。"""

from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.use_cases.checks import CHECK_SCHEMA, CheckWriter
from ailoveshen.application.use_cases.goal_vocabulary import (
    ObjectiveProposal,
    parse_proposal,
    parse_spec,
    predicates_now,
)
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.exceptions import CheckNotWrittenError, GoalRejectedError
from ailoveshen.domain.value_objects import (
    ConditionStatus,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    Mission,
)
from ailoveshen.presentation.web.goal_board import goal_label

SHEEP = GoalSpec(GoalPredicate.CUSTOM, name="sheep_in_pen_3")
CODE = "function check(w) { return { met: false, remaining: 3, lines: [] } }"


def _writer(outputs, reject=()):
    generator = AsyncMock()
    generator.generate_json.side_effect = outputs
    prompts = Mock()
    prompts.build_check_prompt.return_value = "PROMPT"
    bridge = AsyncMock()
    bridge.observe.return_value = GameObservation(
        state={
            "self": {"position": {"x": 10.4, "y": 64, "z": -3.6}},
            "home": {"door": {"x": 1, "y": 64, "z": 1}, "min": {"x": 0, "y": 63, "z": 0}},
        },
        candidates=(),
        health=20,
        food=20,
    )
    bridge.set_condition.side_effect = list(reject) + [{"remaining": 3, "lines": ["0/3"]}] * 3
    bridge.conditions.return_value = [{"name": "flowers_by_door", "description": "d"}]
    return CheckWriter(generator, prompts, bridge), generator, prompts, bridge


def test_custom_needs_a_name_and_parses_it():
    assert parse_spec({"predicate": "custom", "name": "Sheep_In_Pen_3"}) == SHEEP
    with pytest.raises(ValueError, match="custom needs the name"):
        parse_spec({"predicate": "custom"})
    proposal = parse_proposal(
        {"title": "羊を柵に", "conditions": [{"predicate": "custom", "name": "sheep_in_pen_3"}]}
    )
    assert proposal.conditions == (SHEEP,)
    assert goal_label(SHEEP) == "sheep_in_pen_3 を仕上げる"


def test_custom_is_a_small_goal_only_for_its_objective_and_only_with_tools():
    obs = GameObservation(state={}, candidates=(), health=20, food=20, has_home=True)
    plan = ObjectivePlan(mission=Mission("街"))
    assert GoalPredicate.CUSTOM not in predicates_now(obs, plan)
    plan.add("羊を柵に", (SHEEP,))
    assert GoalPredicate.CUSTOM in predicates_now(obs, plan)
    # 候補モードではブリッジが何も出さないので、小目標にしない
    assert GoalPredicate.CUSTOM not in predicates_now(obs, plan, tools=False)


@pytest.mark.asyncio
async def test_the_writer_retries_with_the_bridges_reason_until_it_is_taken():
    writer, generator, prompts, bridge = _writer(
        [{"description": "d1", "code": "x"}, {"description": "three sheep in the pen", "code": CODE}],
        reject=[GoalRejectedError("check sheep_in_pen_3 rejected: the check is already met now")],
    )

    description = await writer.write("sheep_in_pen_3", "羊を柵に集める: 視聴者の頼み")

    assert description == "three sheep in the pen"
    errors = [c.kwargs["previous_error"] for c in prompts.build_check_prompt.call_args_list]
    assert errors[0] == "" and "already met now" in errors[1]
    note = prompts.build_check_prompt.call_args.kwargs["place_note"]
    assert "今の足の位置: 10,64,-4" in note and "ドア 1,64,1" in note
    assert generator.generate_json.call_args.args[1] is CHECK_SCHEMA
    assert generator.generate_json.call_args.kwargs["purpose"] == "check_write"
    bridge.set_condition.assert_awaited_with("sheep_in_pen_3", "three sheep in the pen", CODE)
    assert await writer.known() == {"flowers_by_door"}


@pytest.mark.asyncio
async def test_a_check_that_cannot_be_written_names_itself():
    writer, *_ = _writer(
        [{"description": "d", "code": "x"}] * 3, reject=[GoalRejectedError("does not compile")] * 3
    )
    with pytest.raises(CheckNotWrittenError, match="does not compile") as e:
        await writer.write("sheep_in_pen_3", "x")
    assert e.value.name == "sheep_in_pen_3"


@pytest.mark.asyncio
async def test_the_keeper_writes_a_new_check_before_adding_a_viewers_request():
    bridge = AsyncMock()
    bridge.check.side_effect = lambda specs: [ConditionStatus(s, False) for s in specs]
    checker = AsyncMock()
    checker.known.return_value = {"flowers_by_door"}
    keeper = ObjectiveKeeper(
        bridge=bridge, event_publisher=AsyncMock(), store=Mock(), checker=checker
    )
    proposal = ObjectiveProposal(
        title="羊を柵に集める",
        conditions=(SHEEP, GoalSpec(GoalPredicate.CUSTOM, name="flowers_by_door")),
        reason="牧場っぽくしたい",
    )

    await keeper.accept(ObjectivePlan(mission=Mission("街")), proposal, requested_by="まめ")

    # 知っている判定は書き直さない
    checker.write.assert_awaited_once_with("sheep_in_pen_3", "羊を柵に集める: 牧場っぽくしたい")
    bridge.check.assert_awaited_once()


@pytest.mark.asyncio
async def test_without_a_writer_an_unknown_check_is_rejected():
    bridge = AsyncMock()
    bridge.conditions.return_value = []
    keeper = ObjectiveKeeper(bridge=bridge, event_publisher=AsyncMock(), store=Mock())
    proposal = ObjectiveProposal(title="羊", conditions=(SHEEP,), reason="")
    with pytest.raises(GoalRejectedError, match="no check named sheep_in_pen_3"):
        await keeper.accept(ObjectivePlan(mission=Mission("街")), proposal, requested_by="a")
