"""小目標の束（同時に追う複数の条件）のテスト（docs/design/44_goal_bundles.md）。"""

import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from ailoveshen.application.use_cases.goal_chooser import ChosenGoal, GoalChooser
from ailoveshen.application.use_cases.goal_vocabulary import goal_schema, parse_decision
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.play import AdvancePlayUseCase, _also
from ailoveshen.domain.entities import Conversation, ObjectivePlan, PlaySession
from ailoveshen.domain.events import GoalMemberMetEvent
from ailoveshen.domain.value_objects import (
    ActionDecision,
    ActionResult,
    Activity,
    BundleMember,
    ConditionStatus,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    Mission,
    Job,
    Side,
)
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
    MineflayerBridgeClient,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import format_activity
from ailoveshen.infrastructure.adapters.storage.json_mission_store import JsonMissionStore
from ailoveshen.presentation.services.narrator import Narrator
from ailoveshen.presentation.web.goal_board import goals_snapshot

LOG = GoalSpec(GoalPredicate.HAVE, item="log", count=16)
SAPLING = GoalSpec(GoalPredicate.PLANTED, item="sapling", count=4)
STONE = GoalSpec(GoalPredicate.HAVE, item="cobblestone", count=8)
BUILT = GoalSpec(GoalPredicate.BUILT)
BUNDLE = {
    "review": "森と石を 1 回の外出で",
    "predicate": "have",
    "item": "log",
    "count": 16,
    "together": [
        {"predicate": "planted", "item": "sapling", "count": 4},
        {"predicate": "have", "item": "cobblestone", "count": 8},
    ],
    "approach": "森へ行く道で石を掘り、木を切りながら葉から苗木を取って植える",
    "serves": "current",
    "reason": "家の材料",
}


def _plan() -> ObjectivePlan:
    plan = ObjectivePlan(mission=Mission("街"))
    plan.add("家を建てる", (BUILT,))
    return plan


def _obs(members=(), met=False) -> GameObservation:
    return GameObservation(
        state={"self": {"health": 20}},
        candidates=(),
        health=20.0,
        food=20,
        goal=GoalStatus(met=met, remaining=5, members=tuple(members)),
    )


class TestShape:
    def test_a_decision_carries_the_bundle_and_its_approach(self):
        decision = parse_decision(BUNDLE)
        assert decision.spec == LOG
        assert decision.together == (SAPLING, STONE)
        assert decision.approach.startswith("森へ")
        schema = goal_schema([GoalPredicate.HAVE], ["m1"])
        # 見直しの次に、残りを外出ごとに分けさせてから中目標を書かせる（§9）
        assert list(schema["properties"])[:2] == ["review", "trips"]
        assert "trips" in schema["required"]
        assert schema["properties"]["together"]["maxItems"] == 3
        step = schema["properties"]["jobs"]["items"]["properties"]
        assert "together" in step and "approach" in step

    def test_only_up_to_four_world_conditions_without_repeats(self):
        with pytest.raises(ValueError, match="through_night cannot be in a bundle"):
            parse_decision({**BUNDLE, "together": [{"predicate": "through_night"}]})
        with pytest.raises(ValueError, match="at most 4"):
            Goal(LOG, together=(SAPLING, STONE, BUILT, GoalSpec(GoalPredicate.LIT, distance=8)))
        with pytest.raises(ValueError, match="twice"):
            Goal(LOG, together=(LOG,))
        with pytest.raises(ValueError, match="job 1: together 1"):
            parse_decision({**BUNDLE, "jobs": [{**BUNDLE, "together": [{"predicate": "x"}]}]})

    def test_a_bundled_goal_bundles_the_jobs_it_was_written_as_apart(self):
        """実測（2026-09-27）: 小目標は束なのに、中目標では石と原木を別々に書いた。"""
        decision = parse_decision(
            {
                **BUNDLE,
                "together": [{"predicate": "have", "item": "cobblestone", "count": 8}],
                "jobs": [
                    {"predicate": "have", "item": "log", "count": 16, "reason": "原木"},
                    {"predicate": "have", "item": "cobblestone", "count": 8, "reason": "石"},
                    {"predicate": "built", "reason": "建てる"},
                ],
            }
        )
        assert [s.specs for s in decision.jobs] == [(LOG, STONE), (BUILT,)]
        assert decision.jobs[0].approach == decision.approach
        assert decision.jobs[0].reason == "原木"

    def test_jobs_can_be_bundles_and_are_saved(self, tmp_path):
        decision = parse_decision({**BUNDLE, "jobs": [{**BUNDLE, "reason": "森へ"}]})
        step = decision.jobs[0]
        assert step.specs == (LOG, SAPLING, STONE)
        assert step.describe() == "have(log, 16) + planted(sapling, 4) + have(cobblestone, 8)"
        plan = _plan()
        plan.set_jobs("m1", decision.jobs)
        store = JsonMissionStore(tmp_path / "mission.json")
        store.save(plan)
        assert store.load().pending[0].jobs == plan.get("m1").jobs
        assert store.load().pending[0].jobs[0].specs == step.specs


class TestSession:
    def test_the_budget_grows_with_the_bundle_and_each_member_is_told_once(self):
        session = PlaySession(blueprint=None, plan=_plan())
        session.set_goal(Goal(LOG, together=(SAPLING, STONE)), "day")
        assert session.step_budget == 40 + 2 * 20
        members = [
            BundleMember("have(log, 16)", True, 0),
            BundleMember("have(cobblestone, 8)", False, 3),
        ]
        assert session.newly_met_members(_obs(members)) == ("have(log, 16)",)
        assert session.newly_met_members(_obs(members)) == ()
        # 束全体が済んだら、小目標の終わりで話す
        done = [BundleMember("have(cobblestone, 8)", True, 0)]
        assert session.newly_met_members(_obs(done, met=True)) == ()

    def test_what_the_bundle_gathers_is_not_side_work(self):
        plan = _plan()
        plan.add("苗木と石", (SAPLING, STONE, GoalSpec(GoalPredicate.HAVE, item="wool", count=3)))
        also = _also(plan, (LOG, SAPLING, STONE))
        assert [s.item for s in also] == ["wool"]


class TestChooser:
    @pytest.mark.asyncio
    async def test_a_bundle_job_goes_on_with_what_is_not_met_yet(self):
        session = PlaySession(blueprint=None, plan=_plan())
        session.plan.set_jobs(
            "m1",
            (
                Job(LOG, "森へ", together=(SAPLING, STONE), approach="森で"),
                Job(BUILT, "建てる"),
            ),
        )
        bridge = AsyncMock()
        bridge.check.side_effect = lambda specs: [ConditionStatus(s, s == LOG) for s in specs]
        jev = AsyncMock()
        from ailoveshen.domain.value_objects import FastAnswer, FastVerdict

        jev.ask.return_value = FastVerdict({"next_goal": FastAnswer("next_goal", "A", 0.9)}, 5)

        picked = await GoalChooser(jev, bridge).choose(session, _obs(), "goal x is met")

        assert isinstance(picked, ChosenGoal)
        assert (picked.spec, picked.together, picked.approach) == (SAPLING, (STONE,), "森で")
        state, (question,) = jev.ask.await_args.args
        assert "planted(sapling, 4) + have(cobblestone, 8)" in question.criteria["A"]
        assert "not yet: planted(sapling, 4), have(cobblestone, 8)" in state["jobs"][0]


@pytest.mark.asyncio
async def test_a_member_met_is_published_and_narrated():
    bridge = AsyncMock()
    members = (
        BundleMember("have(log, 16)", True, 0),
        BundleMember("have(cobblestone, 8)", False, 3),
    )
    bridge.observe.return_value = _obs(members)
    bridge.act.side_effect = lambda action_id: ActionResult(action_id, True, "ok", 1.0)
    selector = AsyncMock()
    selector.select.return_value = ActionDecision("wait", 0.9)
    prompts = Mock()
    prompts.build_action_context.return_value = ({}, "")
    events = AsyncMock()
    notes = Mock()
    notes.load.return_value = None
    use_case = AdvancePlayUseCase(
        bridge=bridge,
        text_generator=AsyncMock(),
        prompt_builder=prompts,
        action_selector=selector,
        event_publisher=events,
        conversation=Conversation(),
        objectives=AsyncMock(),
        town=AsyncMock(),
        notes=NoteKeeper(notes),
    )
    blueprint = HouseBlueprint("小屋", "c", 5, 5, 3, Side.NORTH, 2)
    session = PlaySession(blueprint=blueprint, plan=_plan())
    session.set_goal(Goal(LOG, together=(STONE,), objective_id="m1"), "day")

    await use_case.execute(session)

    met = [
        c.args[0]
        for c in events.publish.call_args_list
        if isinstance(c.args[0], GoalMemberMetEvent)
    ]
    assert [(e.goal, e.member, e.left) for e in met] == [
        ("have(log, 16) + have(cobblestone, 8)", "have(log, 16)", ("have(cobblestone, 8)",))
    ]

    llm = AsyncMock()
    llm.generate_commentary.return_value = "原木はそろった、次は石！"
    said = []

    async def say(text):
        said.append(text)

    narrator = Narrator(llm, activity=lambda: None, say=say)
    await narrator.on_member_met(met[0])
    await narrator.drain()
    assert said == ["原木はそろった、次は石！"]
    events_told = llm.generate_commentary.await_args.kwargs["recent_events"]
    assert "have(log, 16) が済んだ" in events_told[0] and "have(cobblestone, 8)" in events_told[0]


def test_the_bundle_is_shown_to_gemini_and_on_the_board():
    goal = Goal(
        LOG, reason="家の材料", together=(STONE,), approach="森の途中で石も", objective_id="m1"
    )
    activity = Activity(
        mission=Mission("街"), objectives=tuple(_plan().pending), goal=goal, observation=_obs()
    )
    text = format_activity(activity)
    assert "have(log, 16) + have(cobblestone, 8)" in text
    assert "進め方: 森の途中で石も" in text
    board = goals_snapshot(activity)["goal"]
    assert board["label"].endswith("（ほか 1 つ）")
    assert len(board["together"]) == 1


@pytest.mark.asyncio
async def test_the_client_sends_the_bundle_and_reads_each_member():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "met": False,
                "remaining": 11,
                "lines": [],
                "members": [
                    {
                        "spec": {"predicate": "have", "item": "log", "count": 16},
                        "met": True,
                        "remaining": 0,
                    },
                    {
                        "spec": {"predicate": "planted", "item": "sapling", "count": 4},
                        "met": False,
                        "remaining": 4,
                    },
                ],
            },
        )

    client = MineflayerBridgeClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://b")
    status = await client.set_goal(LOG, together=(SAPLING,))
    assert seen["together"] == [SAPLING.to_dict()]
    assert status.members == (
        BundleMember("have(log, 16)", True, 0),
        BundleMember("planted(sapling, 4)", False, 4),
    )
