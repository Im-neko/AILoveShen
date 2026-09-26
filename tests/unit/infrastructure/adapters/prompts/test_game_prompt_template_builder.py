"""GamePromptTemplateBuilder アダプタのテスト。"""

from dataclasses import replace

from ailoveshen.domain.value_objects import (
    Activity,
    Candidate,
    CharacterProfile,
    ConditionStatus,
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
    ToolCall,
    ToolOutcome,
    TownDefinition,
    TownSite,
    TownStage,
)
from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)

BLUEPRINT = HouseBlueprint("ぽかぽか", "明るい家", 5, 5, 3, Side.SOUTH, 2)
PLANKS = Goal(
    GoalSpec(GoalPredicate.HAVE, item="planks", count=12), reason="壁の材料", mid_goal_id="m1"
)
MISSION = Mission("生き延びながら家を建て、街にしていく")
EAST = TownSite("E", 96, 0, "地表の石が多い", "いしのまち")
EAST_ROW = {
    "id": "E",
    "x": 96,
    "z": 0,
    "distance": 96,
    "flat_plots": 12,
    "water_pct": 5,
    "steep_pct": 3,
    "stone": 40,
    "coal": 3,
    "iron": 1,
    "logs": 55,
    "lava": 0,
    "animals": 4,
    "loaded_pct": 100,
}
HERE_ROW = {**EAST_ROW, "id": "here", "x": 0, "distance": 0, "stone": 0}
HOUSE = MidGoal(
    "m1", "自分の家を作る", (GoalSpec(GoalPredicate.BUILT),), progress=("30/70", "  sub-step")
)
ALL = list(GoalPredicate)
FOOD = GoalSpec(GoalPredicate.STORED, item="food", count=16)
TOWN = TownDefinition(
    "安全で備えのある小さな街",
    (
        TownStage("備蓄", "冬に備える", conditions=(FOOD,)),
        TownStage(
            "敷地の安全", "夜に備える", conditions=(GoalSpec(GoalPredicate.LIT, distance=16),)
        ),
        TownStage(
            "複数の建物", "街らしく", unresolved=("倉庫を建てる（2 軒目を建てる行動が要る）",)
        ),
    ),
)


def _obs(**kwargs) -> GameObservation:
    state = {
        "time": {"phase": "day", "time_of_day": 6000},
        "self": {
            "held_item": "wooden_sword",
            "equipment": {"head": "leather_helmet", "chest": None, "off_hand": "shield"},
        },
        "inventory": {"spruce_log": 2},
        "mobs": [{"name": "zombie", "hostile": True, "distance_m": 9.5, "visible": True}],
        "memory": {
            "places": [
                {"kind": "sheep", "count": 3, "direction": "NE", "distance_m": 80, "minutes_ago": 4}
            ],
            "deaths": [{"direction": "S", "distance_m": 40, "minutes_ago": 12}],
        },
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


def _goal_prompt(obs=None, recent_goals=(), mid_goals=(HOUSE,), activity=None, **kwargs) -> str:
    args = {
        "blueprint": BLUEPRINT,
        "activity": activity
        or Activity(
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
    """GamePromptTemplateBuilder のテスト。"""

    def test_design_prompt_has_bounds_and_character(self):
        """設計のプロンプトは大きさの範囲とキャラクターを示す。"""
        prompt = GamePromptTemplateBuilder().build_house_design_prompt(
            CharacterProfile(name="シェン", personality_traits=("元気",))
        )

        assert "「シェン」" in prompt and "元気" in prompt
        assert "5〜7" in prompt and "3〜4" in prompt
        assert "前回の設計" not in prompt

    def test_design_prompt_includes_previous_error(self):
        """通らなかった設計のエラーを、やり直しのために示す。"""
        prompt = GamePromptTemplateBuilder().build_house_design_prompt(
            CharacterProfile(), previous_error="width must be 5-7"
        )

        assert "width must be 5-7" in prompt

    def test_goal_prompt_lists_only_the_given_predicates(self):
        """今使える述語は名前だけを状態の側に出す。説明は全部システム指示に（26 §4）。"""
        prompt = _goal_prompt(predicates=[GoalPredicate.HAVE, GoalPredicate.EXPLORED])
        offered = prompt.split("## 今使える小目標\n")[1].split("\n\n")[0]

        assert offered == "have, explored"
        system = GamePromptTemplateBuilder().build_goal_system()
        assert "have(item, count)" in system and "through_night" in system

    def test_goal_system_does_not_change_with_the_state(self):
        """システム指示は状態で変わらない（暗黙のキャッシュが先頭に当たる）。"""
        builder = GamePromptTemplateBuilder()

        assert builder.build_goal_system() == builder.build_goal_system()
        assert "$" not in builder.build_goal_system()

    def test_goal_prompt_shows_the_current_goal_status(self):
        """小目標の進み具合と、何が妨げているかが LLM に届く。"""
        prompt = _goal_prompt()

        assert "have(planks, 12)（「自分の家を作る」のため）: 壁の材料" in prompt
        assert "have 12 planks (5/12): craft spruce_planks x2" in prompt
        assert "進められない理由: no oak_log nearby for oak_log" in prompt
        assert "goal have(planks, 12) is met" in prompt

    def test_goal_prompt_describes_state(self):
        """体力、持ち物、必要なもの、時刻、失敗がプロンプトに入る。"""
        prompt = _goal_prompt()

        assert "体力 12.0/20" in prompt and "spruce_log" in prompt
        assert "気をつけること: hostile zombie 10m away" in prompt
        assert "時間帯: 昼（日暮れまで約 5 分）" in prompt
        assert "家: まだない" in prompt
        assert "craft oak_door x1=失敗（failed: no table）" in prompt
        assert "装備: 手に wooden_sword、頭 leather_helmet、左手 shield" in prompt
        assert (
            "覚えている場所（前に見た、今は見えない）: sheep 3（北東 80m、4 分前）、"
            "死んだ場所（南 40m、12 分前）"
        ) in prompt

    def test_goal_prompt_at_night_counts_to_morning(self):
        """夜は朝までの時間と拠点を示す。"""
        obs = replace(_obs(), has_home=True, inside_home=True, bed_in_home=True)
        obs.state["time"] = {"phase": "night", "time_of_day": 18000}

        prompt = _goal_prompt(obs)

        assert "時間帯: 夜（朝まで約 5 分）" in prompt
        assert "家: 家の中にいる、ベッドあり、チェスト 0" in prompt
        assert "チェストの中身: なし" in prompt

    def test_goal_prompt_home_name_and_chests(self):
        """拠点の名前と、チェストを最後に開けたときの中身。"""
        obs = replace(_obs(), has_home=True, inside_home=False, bed_in_home=False)
        obs.state["home"] = {"name": "ぽかぽかログハウス"}
        obs.state["memory"]["chests"] = [
            {
                "direction": "N",
                "distance_m": 3,
                "contents": {"oak_log": 20, "cobblestone": 14},
                "minutes_ago": 2,
            },
            {"direction": "N", "distance_m": 3, "contents": {}, "minutes_ago": 5},
        ]

        prompt = _goal_prompt(obs)

        assert "家: ぽかぽかログハウス、完成している（外にいる）、ベッドなし、チェスト 2" in prompt
        # 空のチェストは出さない（数は家の行にある。26 §4）
        assert "- チェストの中身: oak_log 20、cobblestone 14（2 分前）\n" in prompt

    def test_goal_prompt_shows_only_the_top_chest_items(self):
        """チェストは多い品から数件だけ。"""
        obs = _obs()
        obs.state["memory"]["chests"] = [
            {
                "direction": "N",
                "distance_m": 3,
                "contents": {f"item{i}": i for i in range(1, 10)},
                "minutes_ago": 1,
            }
        ]

        prompt = _goal_prompt(obs)

        assert "item9 9、item8 8、item7 7、item6 6、item5 5、item4 4 ほか 3 種（1 分前）" in prompt
        assert "item3 3" not in prompt

    def test_goal_prompt_recent_goals_and_previous_error(self):
        """過去の小目標はどう終わったかを示し、断られた小目標は理由を示す。"""
        recent = (GoalOutcome(PLANKS, "goal have(planks, 12) stalled (no progress in 8 steps)"),)

        prompt = _goal_prompt(recent_goals=recent, previous_error="unknown item or group: x")

        assert (
            "have(planks, 12)（「自分の家を作る」のため）: 壁の材料"
            "（未達成、終了: goal have(planks, 12) stalled"
        ) in prompt
        assert "unknown item or group: x" in prompt

    def test_goal_prompt_shows_the_hierarchy_and_what_was_said(self):
        """小目標の決定は、大目標、id つきの中目標、会話を見る。"""
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
        assert "sub-step" not in prompt  # ソルバーの細かい手順はリストに出さない
        assert "2. [m3] ベッドで寝る（nekoさんの頼み） 完了条件: placed(bed, home)" in prompt
        assert "- 剣を持つ（断念: it took 80 steps）" in prompt
        # 小目標の番号はメモの根拠に使う（今の小目標は、これまでのものの次）
        assert "今の小目標 [2]: have(planks, 12)（「自分の家を作る」のため）: 壁の材料" in prompt
        assert "  - [1] through_night()（身を守るため）: 夜は危ない（達成" in prompt
        assert "完了条件に使えるのは次だけ" in GamePromptTemplateBuilder().build_goal_system()
        assert (
            "食べ物を探すときも have(food, n) を選ぶ"
            in GamePromptTemplateBuilder().build_goal_system()
        )
        assert "nekoさん: ベッド作って\nあなた: 家ができたら作るね" in prompt

    def test_goal_prompt_shows_the_town_and_its_stage(self):
        """街の段階に印がつき、段階の小目標はやめられない。"""
        stage = MidGoal("m4", "備蓄", (FOOD,), stage=0)
        activity = Activity(mission=MISSION, town=TOWN, town_stage=0, mid_goals=(stage,))

        prompt = _goal_prompt(activity=activity)

        assert "- 街の定義: 安全で備えのある小さな街" in prompt
        assert "  1. [今] 備蓄: 冬に備える\n  2. [先] 敷地の安全: 夜に備える" in prompt
        assert "[m4] 備蓄 [街の段階 1: やめられない] [取り組み中]" in prompt
        assert "街の段階の中目標はやめられない" in GamePromptTemplateBuilder().build_goal_system()

    def test_goal_prompt_shows_what_the_town_waits_for(self):
        """取り組む段階は未解決の部分を示す。街が完成したらそう言う。"""
        waiting = _goal_prompt(activity=Activity(mission=MISSION, town=TOWN, town_stage=2))
        assert "3. [今] 複数の建物: 街らしく\n     まだできないこと" in waiting
        half = replace(TOWN, stages=(replace(TOWN.stages[0], unresolved=("柵",)),))
        started = _goal_prompt(
            activity=Activity(mission=MISSION, town=half, town_stage=0, stage_met=(FOOD,))
        )
        assert "     できたこと: stored(food, 16)\n     まだできないこと" in started
        assert "倉庫を建てる" in waiting

        done = _goal_prompt(activity=Activity(mission=MISSION, town=TOWN, town_stage=3))
        assert "街は完成した（全 3 段階）" in done

    def test_town_prompt(self):
        """街のプロンプトには大目標、能力、条件、エラーが入る。"""
        prompt = GamePromptTemplateBuilder().build_town_prompt(
            CharacterProfile(), MISSION, EAST, EAST_ROW, previous_error="no way to get iron_sword"
        )

        assert "「生き延びながら家を建て、街にしていく」" in prompt
        assert "街「いしのまち」の場所。「いしのまち」を東（96,0）に作る: 地表の石が多い" in prompt
        assert "地表の石 40・石炭 3・鉄 1" in prompt
        assert "stored(" in prompt and "lit(" in prompt
        assert "5 段階まで" in prompt
        assert "no way to get iron_sword\n定義し直してください" in prompt

    def test_site_prompt(self):
        """場所の選択のプロンプトは、候補地の id と数字を並べる。"""
        prompt = GamePromptTemplateBuilder().build_site_prompt(
            CharacterProfile(name="シェン"), MISSION, [HERE_ROW, EAST_ROW], previous_error="x"
        )

        assert "「シェン」" in prompt
        assert "- here: 最初の家の場所（0,0、家から 0m）: 家を建てられる平らな区画 12" in prompt
        assert "- E: 東（96,0、家から 96m）" in prompt
        assert "x\n選び直してください" in prompt

    def test_design_prompt_for_the_new_site(self):
        """引っ越し先の家の設計には、その場所の説明が入る。"""
        prompt = GamePromptTemplateBuilder().build_house_design_prompt(
            CharacterProfile(), site_note="街「いしのまち」の場所"
        )

        assert "## 建てる場所\n街「いしのまち」の場所" in prompt

    def test_the_site_is_in_the_activity(self):
        """決めたこと（場所と理由）と、調べた事実（数字）を分けて、どのプロンプトにも出す。"""
        surveying = _obs()
        surveying = replace(
            surveying, state={**surveying.state, "survey": {"planned": 9, "sites": [HERE_ROW]}}
        )
        prompt = _goal_prompt(obs=surveying)
        assert "- 街の場所: まだ決めていない（候補地を調べた数 1/9）" in prompt
        assert "  - 最初の家の場所（0,0、家から 0m）" in prompt

        chosen = Activity(mission=MISSION, site=EAST, goal=PLANKS, observation=surveying)
        prompt = _goal_prompt(activity=chosen)
        assert (
            "- 街の場所（決めたこと）: 「いしのまち」を東（96,0）に作る: 地表の石が多い" in prompt
        )
        assert "- 調べた候補地（ブリッジが測った数字" in prompt

    def test_stage_prompt(self):
        """段階のプロンプトは段階を変えず、解決していないものを並べる。"""
        prompt = GamePromptTemplateBuilder().build_stage_prompt(TOWN, TOWN.stages[2])

        assert "その段階「複数の建物」（街らしく）" in prompt
        assert "- 倉庫を建てる（2 軒目を建てる行動が要る）" in prompt
        assert "すでにある conditions: なし" in prompt
        assert "前回の答え" not in prompt

    def test_goal_prompt_with_the_home_built_before(self):
        """前の実行で拠点を建てたなら、建てる家は示さない。"""
        prompt = _goal_prompt(blueprint=None)

        assert "## 建てる家\nなし（前に建てた家が完成していて、拠点になっている）" in prompt

    def test_goal_prompt_without_needs(self):
        """ブリッジの "none" は、気をつけるものなしと示す。"""
        assert "気をつけること: なし" in _goal_prompt(_obs(needs=("none",)))

    def test_action_context(self):
        """行動選択は小目標、進み具合、必要なものを見る。優先順位は渡さない。"""
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


class TestToolPrompt:
    """道具を選ばせるプロンプト（設計書 21 §6）。"""

    STATE = {
        "self": {"position": {"x": 10.5, "y": 67, "z": 10.5}},
        "surroundings": {
            "legend": "north is up",
            "grid": ["+3 +3 +3", "+3  @ +3", "+3 +3 +3"],
            "walls_around": 8,
            "head_blocked": False,
            "standing_on": "stone",
        },
        "mobs": [{"id": 7, "name": "zombie", "hostile": True, "distance_m": 9.5, "direction": "N"}],
    }

    def _prompt(self, recent=(), intent=""):
        activity = Activity(mission=MISSION, goal=PLANKS, observation=_obs(), intent=intent)
        return GamePromptTemplateBuilder().build_tool_prompt(
            activity,
            self.STATE,
            (Candidate("dig oak_log at 1,70,2", {"verb": "dig", "distance": 3}),),
            recent,
        )

    def test_the_prompt_shows_the_shape_around_the_mobs_and_the_suggestions(self):
        prompt = self._prompt()
        assert "+3  @ +3" in prompt
        assert "8 / 8" in prompt
        assert "#7 zombie（敵）" in prompt
        assert "- dig oak_log at 1,70,2（3m）" in prompt
        assert "have 12 planks (5/12)" in prompt  # 小目標の進み具合（ソルバーの木）

    def test_recent_tools_show_refusals_and_watch_stops(self):
        call = ToolCall("goto", {"x": 1, "y": 70, "z": 2}, intent="外に出る")
        recent = (
            ToolOutcome(call, False, "refused: staying inside for the night", 0.0, refused=True),
            ToolOutcome(call, False, "failed: woke up: q", 3.0, stopped_by="woke up: q"),
        )
        prompt = self._prompt(recent=recent, intent="外に出る")
        assert "goto(x=1, y=70, z=2)「外に出る」: 断られた refused: staying inside" in prompt
        assert "失敗（見張り: woke up: q）" in prompt
        assert "- 今やろうとしていること: 外に出る" in prompt

    def test_a_screen_note_is_marked_unverified_with_its_age(self):
        from datetime import datetime, timedelta, timezone

        from ailoveshen.domain.value_objects import ScreenNote

        note = ScreenNote(
            seen="暗い穴の底にいる",
            concern="出られていない",
            matches_goal=False,
            taken_at=datetime.now(timezone.utc) - timedelta(minutes=3),
        )
        activity = Activity(mission=MISSION, goal=PLANKS, observation=_obs(), screen_note=note)
        prompt = GamePromptTemplateBuilder().build_screen_review_prompt(activity)
        assert (
            "- 画面で見たこと（3 分前、画像の解釈で確かめていない）: 暗い穴の底にいる。"
            "気になったこと: 出られていない。目標と合っていないように見えた"
        ) in prompt
        assert "目標を達成したかは画像では決めない" in prompt


class TestFailureDiagnosisPrompt:
    """失敗の記録、分析、助言の見え方（docs/design/27）。"""

    def test_the_failure_record_and_the_options_are_shown(self):
        prompt = _goal_prompt(
            failure_record=("explore east (0.13) → ok: walked 21m east",),
            offered=(
                Candidate(
                    "go to pig seen at -21,-267",
                    {"distance": 296, "seen_minutes_ago": 40},
                ),
                Candidate("explore east", {"been_there": True}),
            ),
            retry_allowed=False,
        )

        assert "## うまくいかなかった小目標の記録" in prompt
        assert "- explore east (0.13) → ok: walked 21m east" in prompt
        assert "- go to pig seen at -21,-267（296m、40 分前に見た）" in prompt
        assert "- explore east（行ったことがある方角）" in prompt
        assert "やり直しは選べない" in prompt

    def test_no_record_section_without_a_failure(self):
        assert "うまくいかなかった小目標の記録" not in _goal_prompt()
        assert "まず原因を分析する" in GamePromptTemplateBuilder().build_goal_system()

    def test_the_advice_reaches_the_chooser_and_the_diagnosis_the_stream(self):
        goal = replace(PLANKS, diagnosis="近くに木がない", advice="Explore west.")
        state, instructions = GamePromptTemplateBuilder().build_action_context(goal, _obs())
        assert state["advice"] == "Explore west."
        assert "advice" in instructions
        plain, _ = GamePromptTemplateBuilder().build_action_context(PLANKS, _obs())
        assert "advice" not in plain

        prompt = _goal_prompt(activity=Activity(mission=MISSION, goal=goal, observation=_obs()))
        assert "前の小目標がうまくいかなかった理由（自分の分析）: 近くに木がない" in prompt
        assert "やり方の助言: Explore west." in prompt


def test_the_landmarks_around_are_shown_with_whose_they_are():
    """ボットが建てていないベッドや家も、近くにあれば見える（見えていても知らなかった）。"""
    obs = _obs()
    obs.state["nearby"] = [
        {
            "kind": "bed",
            "count": 1,
            "owner": None,
            "nearest": {"x": 1, "y": 70, "z": 2, "distance_m": 6.5, "direction": "N"},
        },
        {
            "kind": "torch",
            "count": 4,
            "owner": "home",
            "nearest": {"x": 0, "y": 71, "z": 0, "distance_m": 3.0, "direction": "W"},
        },
    ]
    prompt = _goal_prompt(obs)
    assert (
        "- 近くにあるもの（24 m 以内）: ベッド（一番近いもの: 北 6.5m、自分で建てたものではない）、"
        "松明 4（一番近いもの: 西 3.0m、家のもの）" in prompt
    )
    assert "- 近くにあるもの（24 m 以内）: なし" in _goal_prompt()


class TestPromptLength:
    """代表的な状態のプロンプトの長さ。増えたら気づけるように（26 §4）。"""

    def test_goal_system_length(self):
        # 決まった文（システム指示、キャッシュに当たる部分）。27 の分析、家具の述語、31 の見直し、
        # 33 の植林と畑で増えた
        assert len(GamePromptTemplateBuilder().build_goal_system()) < 6500

    def test_goal_prompt_length(self):
        assert len(_goal_prompt()) < 2500


class TestReviewPrompt:
    """毎回の見直し（docs/design/31）。"""

    def test_the_steps_as_judged_now_are_shown(self):
        log = GoalSpec(GoalPredicate.HAVE, item="log", count=12)
        planks = GoalSpec(GoalPredicate.HAVE, item="planks", count=40)
        wool = GoalSpec(GoalPredicate.HAVE, item="wool", count=3)
        prompt = _goal_prompt(
            step_status=(
                ConditionStatus(log, True),
                ConditionStatus(planks, False, ("planks 8/40",)),
                ConditionStatus(wool, False, (), ("wool",)),
            )
        )

        assert "## 手順の今の状態" in prompt
        assert "- have(log, 12): 済み" in prompt
        assert "- have(planks, 40): まだ（planks 8/40）" in prompt
        assert "- have(wool, 3): まだ（今は手に入らない: wool）" in prompt

    def test_no_steps_no_section_and_the_rules_ask_for_a_review_every_time(self):
        assert "手順の今の状態" not in _goal_prompt()
        system = GamePromptTemplateBuilder().build_goal_system()
        assert "## 見直し（review。毎回、最初に）" in system
        assert "変えないのも答え" in system
