"""MineflayerBridgeClient アダプタのテスト。"""

import json

import httpx
import pytest

from ailoveshen.domain.exceptions import GameBridgeError, GoalRejectedError, SkillRejectedError
from ailoveshen.domain.value_objects import (
    GoalPredicate,
    GoalSpec,
    HouseBlueprint,
    Side,
    SkillDraft,
    TownSite,
)
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
    MineflayerBridgeClient,
)

# GET /observe の形（minecraft-bridge/src/index.mjs）
OBSERVE = {
    "busy": True,
    "observation": {
        "time": {"phase": "dusk", "time_of_day": 12500, "day": 4},
        "self": {"health": 18.5, "food": 17},
        "home": {"inside": False, "door_open": False, "bed": True},
        "inventory": {"spruce_log": 3},
        "build": {"origin": {"x": 1, "y": 2, "z": 3}, "total": 72, "placed": 10, "complete": False},
    },
    "needs": ["night is coming: hostile mobs spawn outside in the dark"],
    "goal": {
        "spec": {"predicate": "have", "item": "planks", "count": 12},
        "met": False,
        "remaining": 4,
        "lines": ["have 12 planks (5/12): craft spruce_planks x2"],
        "blocked": ["no oak_log nearby for oak_log"],
    },
    "candidates": [
        {"id": "dig spruce_log at 1,70,2", "verb": "dig", "target": "spruce_log", "distance": 3}
    ],
}


def _client(handler) -> MineflayerBridgeClient:
    client = MineflayerBridgeClient()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://bridge"
    )
    return client


class TestMineflayerBridgeClient:
    """Node のブリッジとの HTTP の取り決めのテスト。"""

    @pytest.mark.asyncio
    async def test_observe_parses_observation(self):
        """観測の JSON が GameObservation になる。"""
        client = _client(lambda req: httpx.Response(200, json=OBSERVE))

        obs = await client.observe()

        assert obs.health == 18.5 and obs.food == 17
        assert obs.candidates[0].action_id == "dig spruce_log at 1,70,2"
        assert obs.candidates[0].description == {
            "verb": "dig",
            "target": "spruce_log",
            "distance": 3,
        }
        assert obs.goal.remaining == 4 and not obs.goal.met
        assert obs.goal.lines == ("have 12 planks (5/12): craft spruce_planks x2",)
        assert obs.goal.blocked == ("no oak_log nearby for oak_log",)
        assert obs.needs == ("night is coming: hostile mobs spawn outside in the dark",)
        assert obs.state["inventory"] == {"spruce_log": 3}
        assert obs.time_phase == "dusk" and obs.busy
        assert obs.day == 4
        assert obs.has_plan and not obs.house_complete
        assert obs.has_home and not obs.inside_home and obs.bed_in_home

    @pytest.mark.asyncio
    async def test_observe_without_plan_home_or_goal(self):
        """項目がなければ、設計も拠点も小目標もまだない。"""
        data = {**OBSERVE, "goal": None, "observation": {**OBSERVE["observation"], "home": None}}
        del data["observation"]["build"]
        client = _client(lambda req: httpx.Response(200, json=data))

        obs = await client.observe()

        assert not obs.has_plan and not obs.house_complete
        assert not obs.has_home and not obs.inside_home
        assert obs.goal is None

    @pytest.mark.asyncio
    async def test_set_goal_puts_the_spec(self):
        """set_goal() は指定をチェストに残す物と一緒に送り、状態を返す。"""
        seen = {}

        def handler(req):
            seen["method"] = req.method
            seen["path"] = req.url.path
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json=OBSERVE["goal"])

        status = await _client(handler).set_goal(
            GoalSpec(GoalPredicate.HAVE, item="planks", count=12),
            keep=(GoalSpec(GoalPredicate.STORED, item="food", count=10),),
        )

        assert seen == {
            "method": "PUT",
            "path": "/goal",
            "body": {
                "predicate": "have",
                "item": "planks",
                "count": 12,
                "keep": [{"item": "food", "count": 10}],
                "also": [],
            },
        }
        assert status.remaining == 4

    @pytest.mark.asyncio
    async def test_rejected_goal_raises_with_the_reason(self):
        """400 は、ブリッジの理由を持つ GoalRejectedError になる。"""
        client = _client(
            lambda req: httpx.Response(400, json={"error": "unknown item or group: x"})
        )

        with pytest.raises(GoalRejectedError, match="unknown item or group: x"):
            await client.set_goal(GoalSpec(GoalPredicate.HAVE, item="x", count=1))

    @pytest.mark.asyncio
    async def test_check_judges_conditions_without_setting_a_goal(self):
        """check() は指定を送り、それぞれの状態を順に返す。"""
        seen = {}

        def handler(req):
            seen["path"] = req.url.path
            seen["body"] = json.loads(req.content)
            return httpx.Response(
                200,
                json=[
                    {"spec": {"predicate": "built"}, "met": True, "lines": []},
                    {
                        "spec": {"predicate": "placed", "item": "bed", "where": "home"},
                        "met": False,
                        "lines": ["a bed in the house: no"],
                        "impossible": ["no way to get white_wool"],
                    },
                ],
            )

        built = GoalSpec(GoalPredicate.BUILT)
        bed = GoalSpec(GoalPredicate.PLACED, item="bed", where="home")
        statuses = await _client(handler).check([built, bed])

        assert seen == {
            "path": "/check",
            "body": {"specs": [{"predicate": "built"}, bed.to_dict()]},
        }
        assert [(s.spec, s.met, s.lines, s.impossible) for s in statuses] == [
            (built, True, (), ()),
            (bed, False, ("a bed in the house: no",), ("no way to get white_wool",)),
        ]

    @pytest.mark.asyncio
    async def test_check_rejected_condition_raises_with_the_reason(self):
        """ブリッジが判定できない条件は GoalRejectedError になる。"""
        client = _client(
            lambda req: httpx.Response(400, json={"error": "unknown item or group: diamondz"})
        )

        with pytest.raises(GoalRejectedError, match="diamondz"):
            await client.check([GoalSpec(GoalPredicate.HAVE, item="diamondz", count=1)])

    @pytest.mark.asyncio
    async def test_check_nothing_asks_nothing(self):
        """条件がなければリクエストしない。"""
        assert await _client(lambda req: httpx.Response(500)).check([]) == []

    @pytest.mark.asyncio
    async def test_act_posts_candidate_id(self):
        """act() は id を送り、結果を返す。"""
        seen = {}

        def handler(req):
            seen["path"] = req.url.path
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"ok": False, "result": "failed: x", "seconds": 1.5})

        result = await _client(handler).act("wait inside")

        assert seen == {"path": "/act", "body": {"id": "wait inside"}}
        assert not result.ok and result.result == "failed: x" and result.seconds == 1.5

    @pytest.mark.asyncio
    async def test_set_build_plan_sends_blocks_in_order_with_the_design(self):
        """建てる計画は、ブロックの種類を文字列にして順番どおりに、設計と一緒に送る。"""
        seen = {}

        def handler(req):
            seen["method"] = req.method
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={})

        blueprint = HouseBlueprint(
            "ぽかぽか", "木の家", 5, 6, 3, Side.SOUTH, 2, corner_pillars=True
        )
        await _client(handler).set_build_plan(blueprint)

        body = seen["body"]
        assert seen["method"] == "PUT"
        assert body["blocks"][:2] == [
            {"x": b.x, "y": b.y, "z": b.z, "block": b.kind.value} for b in blueprint.blocks()[:2]
        ]
        assert len(body["blocks"]) == len(blueprint.blocks())
        assert (body["width"], body["depth"], body["height"]) == (5, 6, 4)
        assert body["design"] == {
            "name": "ぽかぽか",
            "concept": "木の家",
            "width": 5,
            "depth": 6,
            "wall_height": 3,
            "door_side": "south",
            "door_offset": 2,
            "corner_pillars": True,
        }
        assert body["site"] is None

    @pytest.mark.asyncio
    async def test_set_build_plan_sends_the_site(self):
        """引っ越し先の家は、建てる場所と一緒に送る。"""
        seen = {}

        def handler(req):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={})

        blueprint = HouseBlueprint("石の家", "c", 5, 5, 3, Side.SOUTH, 2)
        await _client(handler).set_build_plan(blueprint, TownSite("E", 96, 0, "r", "n"))

        assert seen["body"]["site"] == {"x": 96, "z": 0}

    @pytest.mark.asyncio
    async def test_error_status_raises(self):
        """200 以外の応答は GameBridgeError。"""
        client = _client(lambda req: httpx.Response(409, json={"error": "busy"}))

        with pytest.raises(GameBridgeError, match="409"):
            await client.act("explore north")

    @pytest.mark.asyncio
    async def test_unreachable_raises(self):
        """接続エラーは GameBridgeError。"""

        def handler(req):
            raise httpx.ConnectError("refused")

        with pytest.raises(GameBridgeError, match="unreachable"):
            await _client(handler).observe()


class TestToolEndpoints:
    """道具（POST /tool）、共通の状態（GET /state）、中断（POST /abort）（設計書 21）。"""

    @pytest.mark.asyncio
    async def test_run_tool_posts_the_call_and_turns_lookups_into_json(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, json.loads(request.content)))
            body = json.loads(request.content)
            if body["name"] == "recipe_of":
                return httpx.Response(
                    200, json={"ok": True, "result": {"crafting": []}, "seconds": 0}
                )
            return httpx.Response(
                200,
                json={
                    "ok": False,
                    "refused": True,
                    "result": "refused: staying inside",
                    "seconds": 0,
                },
            )

        client = _client(handler)
        assert await client.run_tool("recipe_of", {"item": "stick"}) == (
            True,
            '{"crafting": []}',
            0.0,
            False,
        )
        assert await client.run_tool("goto", {"x": 1, "y": 70, "z": 2}) == (
            False,
            "refused: staying inside",
            0.0,
            True,
        )
        assert seen[1] == ("POST", "/tool", {"name": "goto", "args": {"x": 1, "y": 70, "z": 2}})

    @pytest.mark.asyncio
    async def test_state_and_abort(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/state":
                return httpx.Response(200, json={"action": None, "mobs": []})
            assert json.loads(request.content) == {"reason": "woke up: q"}
            return httpx.Response(200, json={"aborted": False, "why": "no action is running"})

        client = _client(handler)
        assert await client.state() == {"action": None, "mobs": []}
        assert await client.abort("woke up: q") is False


@pytest.mark.asyncio
async def test_set_build_puts_the_expanded_blocks_and_a_rejection_carries_the_reason():
    from ailoveshen.domain.value_objects import (
        BlockKind,
        BuildAnchor,
        BuildDesign,
        BuildShape,
        ShapeKind,
    )

    design = BuildDesign(
        "annex",
        "寝室",
        BuildAnchor.HOME_EAST,
        (BuildShape(ShapeKind.FILL, (0, 0, 0), (1, 0, 2), BlockKind.PLANKS),),
    )
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        if seen.get("reject"):
            return httpx.Response(400, json={"error": "it would overlap the build shed"})
        return httpx.Response(200, json={"name": "annex"})

    client = _client(handler)
    await client.set_build(design)
    assert seen["path"] == "/builds/annex"
    assert seen["body"]["anchor"] == "home:east"
    assert seen["body"]["size"] == {"width": 2, "depth": 3, "height": 1}
    assert len(seen["body"]["blocks"]) == 6 and seen["body"]["blocks"][0]["block"] == "planks"
    seen["reject"] = True
    with pytest.raises(GoalRejectedError, match="overlap the build shed"):
        await client.set_build(design)


class TestSkillsOverHttp:
    """技の取り決め（minecraft-bridge/src/index.mjs、docs/design/22）。"""

    @pytest.mark.asyncio
    async def test_list_save_run_and_answer(self):
        seen = []

        def handler(req):
            seen.append((req.method, req.url.path, json.loads(req.content or b"null")))
            if req.url.path == "/skills":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "name": "hunt",
                            "description": "狩る",
                            "version": 2,
                            "successes": 1,
                            "uses": 2,
                            "failures": 1,
                            "verified": True,
                            "params": {},
                            "expects": {"predicate": "progress"},
                            "last_failure": "x",
                        }
                    ],
                )
            if req.method == "PUT":
                return httpx.Response(200, json={"name": "hunt", "version": 3})
            if req.url.path == "/skills/hunt/run":
                return httpx.Response(
                    200,
                    json={
                        "ok": False,
                        "ended": "fail",
                        "version": 3,
                        "reason": "no pig",
                        "expects": {"met": False, "lines": ["food 1/2"]},
                        "calls": [{"tool": "goto", "ok": True}],
                        "log": ["a"],
                        "seconds": 4.2,
                        "learned": False,
                    },
                )
            return httpx.Response(200, json={"accepted": True})

        client = _client(handler)
        [info] = await client.skills()
        assert (info.name, info.version, info.verified, info.last_failure) == ("hunt", 2, True, "x")
        draft = SkillDraft(
            "狩る", {}, {"predicate": "progress"}, "export default async function () {}"
        )
        assert await client.save_skill("hunt", draft) == 3
        run = await client.run_skill("hunt", {"animal": "pig"}, 3)
        assert (run.ok, run.reason, run.expects_lines, run.seconds) == (
            False,
            "no pig",
            ("food 1/2",),
            4.2,
        )
        await client.answer_judge(4, True, 0.8)
        assert seen[2] == ("POST", "/skills/hunt/run", {"args": {"animal": "pig"}, "version": 3})
        assert seen[3] == ("POST", "/judge", {"id": 4, "answer": True, "confidence": 0.8})

    @pytest.mark.asyncio
    async def test_a_rejected_skill_and_a_missing_one(self):
        def handler(req):
            if req.method == "PUT":
                return httpx.Response(400, json={"error": "the code does not compile"})
            return httpx.Response(404, json={"error": "there is no skill named x"})

        client = _client(handler)
        with pytest.raises(SkillRejectedError, match="does not compile"):
            await client.save_skill("x", SkillDraft("d", {}, {"predicate": "progress"}, "c"))
        assert await client.skill("x") is None
        run = await client.run_skill("x", {})
        assert not run.ok and "no skill" in run.reason


def test_what_other_mid_goals_gather_is_sent_as_also():
    from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
        _gathered,
    )

    assert _gathered(GoalSpec(GoalPredicate.PLANTED, item="sapling", count=4)) == {
        "item": "sapling",
        "count": 4,
    }
    assert _gathered(GoalSpec(GoalPredicate.FARMED, item="wheat", count=30)) == {
        "item": "wheat_seeds",
        "count": 16,
    }
    assert _gathered(GoalSpec(GoalPredicate.STORED, item="log", count=32)) == {
        "item": "log",
        "count": 32,
    }
    assert _gathered(GoalSpec(GoalPredicate.BUILT)) is None
