"""Tests for GamePromptTemplateBuilder adapter."""

from dataclasses import replace

from ailoveshen.domain.value_objects import (
    Activity,
    Candidate,
    CharacterProfile,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalOutcome,
    GoalPredicate,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    MessageType,
    MidGoal,
    MidGoalState,
    Mission,
    Side,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)

BLUEPRINT = HouseBlueprint("ぽかぽか", "明るい家", 5, 5, 3, Side.SOUTH, 2)
PLANKS = Goal(
    GoalSpec(GoalPredicate.HAVE, item="planks", count=12), reason="壁の材料", mid_goal_id="m1"
)
MISSION = Mission("生き延びながら家を建て、街にしていく")
HOUSE = MidGoal(
    "m1", "自分の家を作る", (GoalSpec(GoalPredicate.BUILT),), progress=("30/70", "  sub-step")
)
ALL = list(GoalPredicate)


def _obs(**kwargs) -> GameObservation:
    state = {
        "time": {"phase": "day", "time_of_day": 6000},
        "self": {
            "held_item": "wooden_sword",
            "equipment": {"head": "leather_helmet", "chest": None, "off_hand": "shield"},
        },
        "inventory": {"spruce_log": 2},
        "mobs": [{"name": "zombie", "hostile": True, "distance_m": 9.5, "visible": True}],
        "recent_actions": [
            {"action": "dig oak_log at 1,2,3", "ok": True, "result": "dug"},
            {"action": "craft oak_door x1", "ok": False, "result": "failed: no table"},
        ],
    }
    params = {
        "state": state,
        "candidates": (Candidate("wait", {"verb": "wait"}),),
        "health": 12.0,
        "food": 18,
        "needs": ("hostile zombie 10m away",),
        "goal": GoalStatus(
            met=False,
            remaining=4,
            lines=("have 12 planks (5/12): craft spruce_planks x2",),
            blocked=("no oak_log nearby for oak_log",),
        ),
    }
    params.update(kwargs)
    return GameObservation(**params)


def _goal_prompt(obs=None, recent_goals=(), mid_goals=(HOUSE,), **kwargs) -> str:
    args = {
        "blueprint": BLUEPRINT,
        "activity": Activity(
            mission=MISSION,
            mid_goals=mid_goals,
            goal=PLANKS,
            observation=obs or _obs(),
            recent_goals=recent_goals,
        ),
        "goal_ended_because": "goal have(planks, 12) is met",
        "recent_messages": (),
        "predicates": ALL,
    }
    args.update(kwargs)
    return GamePromptTemplateBuilder().build_goal_prompt(**args)


class TestGamePromptTemplateBuilder:
    """Tests for GamePromptTemplateBuilder."""

    def test_design_prompt_has_bounds_and_character(self):
        """Test the design prompt states the size bounds and the character."""
        prompt = GamePromptTemplateBuilder().build_house_design_prompt(
            CharacterProfile(name="シェン", personality_traits=("元気",))
        )

        assert "「シェン」" in prompt and "元気" in prompt
        assert "5〜7" in prompt and "3〜4" in prompt
        assert "前回の設計" not in prompt

    def test_design_prompt_includes_previous_error(self):
        """Test a rejected design's error is shown for the retry."""
        prompt = GamePromptTemplateBuilder().build_house_design_prompt(
            CharacterProfile(), previous_error="width must be 5-7"
        )

        assert "width must be 5-7" in prompt

    def test_goal_prompt_lists_only_the_given_predicates(self):
        """Test only the predicates passed in are offered."""
        prompt = _goal_prompt(predicates=[GoalPredicate.HAVE, GoalPredicate.EXPLORED])
        offered = prompt.split("## 使える目標（小目標）\n")[1].split("\n\n")[0]

        assert "have(item, count)" in offered and "explored(distance)" in offered
        assert "through_night" not in offered and "built:" not in offered

    def test_goal_prompt_shows_the_current_goal_status(self):
        """Test the subgoal progress and what blocks it reach the LLM."""
        prompt = _goal_prompt()

        assert "have(planks, 12)（「自分の家を作る」のため）: 壁の材料" in prompt
        assert "have 12 planks (5/12): craft spruce_planks x2" in prompt
        assert "進められない理由: no oak_log nearby for oak_log" in prompt
        assert "goal have(planks, 12) is met" in prompt

    def test_goal_prompt_describes_state(self):
        """Test health, inventory, needs, time and failures are in the prompt."""
        prompt = _goal_prompt()

        assert "体力 12.0/20" in prompt and "spruce_log" in prompt
        assert "気をつけること: hostile zombie 10m away" in prompt
        assert "時間帯: 昼（日暮れまで約 5 分）" in prompt
        assert "家: まだない" in prompt
        assert "craft oak_door x1=失敗（failed: no table）" in prompt
        assert "装備: 手に wooden_sword、頭 leather_helmet、左手 shield" in prompt

    def test_goal_prompt_at_night_counts_to_morning(self):
        """Test at night the time until morning is shown, and the home."""
        obs = replace(_obs(), has_home=True, inside_home=True, bed_in_home=True)
        obs.state["time"] = {"phase": "night", "time_of_day": 18000}

        prompt = _goal_prompt(obs)

        assert "時間帯: 夜（朝まで約 5 分）" in prompt
        assert "家: 家の中にいる、ベッドあり" in prompt

    def test_goal_prompt_recent_goals_and_previous_error(self):
        """Test past goals show how they ended, and a rejected goal's reason is shown."""
        recent = (GoalOutcome(PLANKS, "goal have(planks, 12) stalled (no progress in 8 steps)"),)

        prompt = _goal_prompt(recent_goals=recent, previous_error="unknown item or group: x")

        assert (
            "have(planks, 12)（「自分の家を作る」のため）: 壁の材料"
            "（未達成、終了: goal have(planks, 12) stalled"
        ) in prompt
        assert "unknown item or group: x" in prompt

    def test_goal_prompt_shows_the_hierarchy_and_what_was_said(self):
        """Test the goal decision sees the mission, the mid goals with ids, and the conversation."""
        bed = MidGoal(
            "m3",
            "ベッドで寝る",
            (GoalSpec(GoalPredicate.PLACED, item="bed", where="home"),),
            requested_by="neko",
        )
        sword = MidGoal(
            "m2",
            "剣を持つ",
            (GoalSpec(GoalPredicate.HAVE, item="wooden_sword", count=1),),
            state=MidGoalState.DROPPED,
            ended_because="it took 80 steps",
        )
        night = Goal(GoalSpec(GoalPredicate.THROUGH_NIGHT), "夜は危ない")
        recent = (GoalOutcome(night, "the time of day changed from night to day", met=True),)
        messages = (
            ConversationMessage.from_viewer("ベッド作って", "neko"),
            ConversationMessage.from_streamer("家ができたら作るね", MessageType.RESPONSE),
        )

        prompt = _goal_prompt(
            mid_goals=(HOUSE, bed, sword), recent_goals=recent, recent_messages=messages
        )

        assert "- 大目標: 生き延びながら家を建て、街にしていく" in prompt
        assert "1. [m1] 自分の家を作る [取り組み中] 完了条件: built()（30/70）" in prompt
        assert "sub-step" not in prompt  # the solver's sub-steps are left out of the list
        assert "2. [m3] ベッドで寝る（nekoさんの頼み） 完了条件: placed(bed, home)" in prompt
        assert "- 剣を持つ（断念: it took 80 steps）" in prompt
        assert "今の小目標: have(planks, 12)（「自分の家を作る」のため）: 壁の材料" in prompt
        assert "through_night()（身を守るため）: 夜は危ない（達成" in prompt
        assert "完了条件に使えるのは次だけ" in prompt
        assert "食べ物を探すときも have(food, n) を選ぶ" in prompt
        assert "nekoさん: ベッド作って\nあなた: 家ができたら作るね" in prompt

    def test_goal_prompt_with_the_home_built_before(self):
        """Test no house to build is shown when the home was built in an earlier run."""
        prompt = _goal_prompt(blueprint=None)

        assert "## 建てる家\nなし（前に建てた家が完成していて、拠点になっている）" in prompt

    def test_goal_prompt_without_needs(self):
        """Test the bridge's "none" is shown as nothing to watch for."""
        assert "気をつけること: なし" in _goal_prompt(_obs(needs=("none",)))

    def test_action_context(self):
        """Test the selector sees the goal, progress and needs; no priority order is given."""
        state, instructions = GamePromptTemplateBuilder().build_action_context(PLANKS, _obs())

        assert state["goal"] == "have(planks, 12)"
        assert state["progress"] == ["have 12 planks (5/12): craft spruce_planks x2"]
        assert state["blocked"] == ["no oak_log nearby for oak_log"]
        assert state["needs"] == ["hostile zombie 10m away"]
        assert state["self"]["time"] == "day (5 minutes until dusk)"
        assert state["self"]["equipment"] == {"head": "leather_helmet", "off_hand": "shield"}
        assert state["nearby_mobs"] == [{"name": "zombie", "hostile": True, "distance_m": 9.5}]
        assert len(state["recent_actions"]) == 2
        assert "Stay alive first" in instructions
        assert "Priorities" not in instructions
