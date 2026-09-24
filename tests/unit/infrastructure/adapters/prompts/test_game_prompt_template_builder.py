"""Tests for GamePromptTemplateBuilder adapter."""

from dataclasses import replace

from ailoveshen.domain.value_objects import (
    AvailableAction,
    BuildStatus,
    CharacterProfile,
    GameObservation,
    Goal,
    GoalType,
    HouseBlueprint,
    MaterialNeeds,
    Side,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)

BLUEPRINT = HouseBlueprint("ぽかぽか", "明るい家", 5, 5, 3, Side.SOUTH, 2)
NEEDS = MaterialNeeds(logs_short=5, planks_short=20, door_needed=True, table_needed=False)


def _obs(actions, build=None) -> GameObservation:
    return GameObservation(
        state={"time": {"phase": "night"}, "mobs": [{"hostile": True, "visible": True}]},
        inventory={"spruce_log": 2},
        actions=tuple(AvailableAction(a, a) for a in actions),
        health=12.0,
        food=18,
        build=build,
    )


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

    def test_goal_prompt_lists_the_given_goals(self):
        """Test only the goals passed in are offered."""
        prompt = GamePromptTemplateBuilder().build_goal_prompt(
            BLUEPRINT,
            NEEDS,
            _obs(["collect_log", "idle"]),
            None,
            "no goal yet",
            (),
            [GoalType.GATHER_WOOD],
        )

        assert "- gather_wood:" in prompt
        assert "- craft:" not in prompt
        assert "- build_shelter:" not in prompt

    def test_goal_prompt_describes_state(self):
        """Test progress, needs, situation and history appear in the prompt."""
        build = BuildStatus(total=72, placed=30, complete=False, site_chosen=True)
        prompt = GamePromptTemplateBuilder().build_goal_prompt(
            BLUEPRINT,
            NEEDS,
            _obs(["build_step"], build=build),
            Goal(GoalType.CRAFT),
            "goal craft is met",
            (Goal(GoalType.GATHER_WOOD, reason="木がない"),),
            [GoalType.BUILD_SHELTER],
        )

        assert "30/72" in prompt
        assert "原木があと 5 本" in prompt and "ドア" in prompt
        assert "時間帯: 夜" in prompt and "体力 12.0/20" in prompt
        assert "見えている敵: 1 体" in prompt
        assert "gather_wood: 木がない" in prompt
        assert "goal craft is met" in prompt

    def test_goal_prompt_shows_why_recent_actions_failed(self):
        """Test failed actions carry their reason so the LLM can react to it."""
        obs = _obs(["explore"])
        obs.state["recent_actions"] = [
            {"action": "collect_log", "ok": True, "result": "chopped"},
            {"action": "build_step", "ok": False, "result": "failed: no flat 5x5 site"},
        ]
        prompt = GamePromptTemplateBuilder().build_goal_prompt(
            BLUEPRINT, NEEDS, obs, None, "", (), []
        )

        assert "collect_log=成功 / build_step=失敗（failed: no flat 5x5 site）" in prompt

    def test_goal_prompt_tells_whether_a_log_is_reachable(self):
        """Test the LLM is told whether gathering wood can proceed here."""
        builder = GamePromptTemplateBuilder()
        none = builder.build_goal_prompt(BLUEPRINT, NEEDS, _obs(["explore"]), None, "", (), [])
        obs = _obs(["collect_log"])
        obs.state["resources"] = {"nearest_reachable_log": {"block": "oak_log", "distance_m": 6.5}}
        near = builder.build_goal_prompt(BLUEPRINT, NEEDS, obs, None, "", (), [])

        assert "近くに切れる木: なし" in none
        assert "近くに切れる木: あり（6.5m 先）" in near

    def test_goal_prompt_tells_time_left_and_home(self):
        """Test the LLM sees how long until dusk and whether a home exists."""
        obs = _obs(["collect_log"])
        obs.state["time"] = {"phase": "day", "time_of_day": 6000}
        prompt = GamePromptTemplateBuilder().build_goal_prompt(
            BLUEPRINT, NEEDS, obs, None, "", (), []
        )

        assert "時間帯: 昼（日暮れまで約 5 分）" in prompt
        assert "家: まだない" in prompt

    def test_goal_prompt_at_night_counts_to_morning(self):
        """Test at night the time until morning is shown."""
        obs = replace(_obs(["stay_inside"]), has_home=True, inside_home=True)
        obs.state["time"] = {"phase": "night", "time_of_day": 18000}
        prompt = GamePromptTemplateBuilder().build_goal_prompt(
            BLUEPRINT, NEEDS, obs, None, "", (), []
        )

        assert "時間帯: 夜（朝まで約 5 分）" in prompt
        assert "家: 家の中にいる" in prompt

    def test_action_instructions_name_the_goal(self):
        """Test the selector instructions carry the goal and house."""
        text = GamePromptTemplateBuilder().build_action_instructions(
            Goal(GoalType.BUILD_SHELTER, reason="材料がそろった"), BLUEPRINT
        )

        assert "build_shelter" in text and "ぽかぽか" in text and "stay alive" in text
