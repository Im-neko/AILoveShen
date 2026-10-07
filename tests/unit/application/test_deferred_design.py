"""建物の設計を返事のあとに裏でする（設計書 107）: 返事が設計を待たず、足せたら今やる考え直しをする。"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.application.use_cases.goal_vocabulary import ObjectiveProposal
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.domain.entities import Conversation, ObjectivePlan
from ailoveshen.domain.exceptions import GoalRejectedError
from ailoveshen.domain.value_objects import CharacterProfile, GoalPredicate, GoalSpec, Mission
from tests.unit.application.test_play_use_cases import _judged, _session

ANNEX = GoalSpec(GoalPredicate.BUILT, name="annex")
PROPOSAL = ObjectiveProposal(title="家を広くする", conditions=(ANNEX,), reason="ベッドの場所")


class SlowBuilder:
    """設計が終わるのを、テストが許すまで待つ建築家。"""

    def __init__(self, home=True, fail=None):
        self.release = asyncio.Event()
        self.designed = []
        self.home = home
        self.fail = fail

    async def known(self):
        return set()

    async def require_home(self):
        if not self.home:
            raise GoalRejectedError("there is no home yet: build the house first")

    async def design(self, name, brief, max_blocks):
        self.designed.append(name)
        await self.release.wait()
        if self.fail:
            raise self.fail


def _keeper(builder):
    bridge = AsyncMock()
    bridge.check.side_effect = _judged()
    bridge.builds.return_value = [{"name": "annex", "total": 120}]
    store = Mock()
    store.load.return_value = None
    keeper = ObjectiveKeeper(bridge=bridge, event_publisher=AsyncMock(), store=store, builder=builder)
    keeper.on_design_failed = AsyncMock()
    return keeper


@pytest.mark.asyncio
async def test_the_reply_side_does_not_wait_for_the_design_and_the_objective_is_added_after_it():
    builder = SlowBuilder()
    keeper = _keeper(builder)
    plan = ObjectivePlan(mission=Mission("街"))
    added = []
    result = await asyncio.wait_for(
        keeper.accept(plan, PROPOSAL, requested_by="まめ", on_added=added.append), timeout=1
    )
    assert result is None and added == [] and len(plan.pending) == 0  # まだ足されていない
    await asyncio.sleep(0)
    assert builder.designed == ["annex"]  # 裏で設計が始まっている
    builder.release.set()
    await keeper.drain()
    assert [g.title for g in plan.pending] == ["家を広くする"]
    assert added == [plan.pending[0]]
    assert plan.pending[0].budget == 280  # 建物の大きさで広げた予算
    keeper.on_design_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_design_that_fails_is_reported_once_and_adds_nothing():
    builder = SlowBuilder(fail=GoalRejectedError("the block would replace the door"))
    keeper = _keeper(builder)
    plan = ObjectivePlan(mission=Mission("街"))
    added = []
    await asyncio.wait_for(keeper.accept(plan, PROPOSAL, requested_by="まめ", on_added=added.append), timeout=1)
    builder.release.set()
    await keeper.drain()
    assert added == [] and len(plan.pending) == 0
    keeper.on_design_failed.assert_awaited_once_with("まめ", "家を広くする", "the block would replace the door")


@pytest.mark.asyncio
async def test_without_a_home_it_is_refused_at_once_not_in_the_background():
    builder = SlowBuilder(home=False)
    keeper = _keeper(builder)
    with pytest.raises(GoalRejectedError, match="there is no home yet"):
        await keeper.accept(
            ObjectivePlan(mission=Mission("街")), PROPOSAL, requested_by="まめ", on_added=lambda g: None
        )
    assert builder.designed == []


@pytest.mark.asyncio
async def test_the_same_request_while_designing_is_not_designed_twice_and_cancel_stops_it():
    builder = SlowBuilder()
    keeper = _keeper(builder)
    plan = ObjectivePlan(mission=Mission("街"))
    await asyncio.wait_for(keeper.accept(plan, PROPOSAL, requested_by="まめ", on_added=lambda g: None), timeout=1)
    await asyncio.wait_for(keeper.accept(plan, PROPOSAL, requested_by="べつ", on_added=lambda g: None), timeout=1)
    await asyncio.sleep(0)
    assert builder.designed == ["annex"]
    keeper.cancel_designing()
    await keeper.drain()
    assert len(plan.pending) == 0
    keeper.on_design_failed.assert_not_awaited()  # 配信を止めたのは、断られたのではない


@pytest.mark.asyncio
async def test_a_now_request_rethinks_only_after_the_objective_is_added():
    """「今やる」の考え直しは、大目標が足せてから（足す前に考え直すと、ブリッジがまだ知らない建物を選ぶ）。"""
    builder = SlowBuilder()
    keeper = _keeper(builder)
    session = _session()
    generator = AsyncMock()
    generator.generate_json.return_value = {
        "reply": "広くするよ！",
        "request": "accept",
        "level": "objective",
        "title": "家を広くする",
        "conditions": [{"predicate": "built", "name": "annex"}],
        "reason": "ベッドの場所",
        "when": "now",
    }
    prompts = Mock()
    prompts.build_system_prompt.return_value = "system"
    prompts.build_chat_response_prompt.return_value = "prompt"
    use_case = GenerateResponseUseCase(
        text_generator=generator,
        prompt_builder=prompts,
        event_publisher=AsyncMock(),
        conversation=Conversation(),
        character=CharacterProfile(),
        objectives=keeper,
    )
    response = await asyncio.wait_for(
        use_case.execute(
            GenerateResponseRequest(user_name="まめ", message="広くして", user_id="1", session=session)
        ),
        timeout=1,
    )
    assert response.success and response.text == "広くするよ！" and response.objective is None
    assert not session.rethink_reason  # まだ足されていない
    builder.release.set()
    await keeper.drain()
    assert 'you told まめ you would do "家を広くする" now' in session.rethink_reason
