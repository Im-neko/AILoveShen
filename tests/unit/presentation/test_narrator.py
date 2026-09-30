"""Narrator（小目標の変化を口に出す）のテスト。"""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalDecidingEvent,
    GoalEndedEvent,
    GoalMemberMetEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    JobDoneEvent,
    ObjectiveAddedEvent,
    ObjectiveCompletedEvent,
    ObjectiveDroppedEvent,
    SkillLearnedEvent,
    SkillRevisedEvent,
    TownCompletedEvent,
    TownDefinedEvent,
    TownSiteChosenEvent,
)
from ailoveshen.domain.value_objects import Activity, GameObservation, Goal, GoalPredicate, GoalSpec
from ailoveshen.presentation.services.narrator import (
    RETHINKING_NOTICES,
    THINKING_NOTICE_INTERVAL_SECONDS,
    THINKING_NOTICES,
    Narrator,
)

ACTIVITY = Activity()
NEW = "新しい小目標（これからやること。まだ済んでいない）: "


@pytest.fixture
def llm():
    """LLMService のモック。"""
    service = AsyncMock()
    service.generate_commentary.return_value = "次は羊を探すよ"
    return service


@pytest.fixture
def said():
    """Narrator が言ったこと。"""
    return []


@pytest.fixture
def narrator(llm, said):
    async def say(text: str) -> None:
        said.append(text)

    return Narrator(llm, activity=lambda: current[0], say=say)


current = [ACTIVITY]  # 今 activity() が返すもの


async def _act(narrator: Narrator, ok: bool = True) -> None:
    """行動が 1 つ終わった（成功して初めて、新しい小目標を話す。設計書 65 §3）。"""
    await narrator.on_action(GameActionExecutedEvent(action_id="dig", ok=ok))


async def _set(narrator: Narrator, event: GoalSetEvent) -> None:
    """新しい小目標を決めて、最初の行動が成功した。"""
    await narrator.on_goal_set(event)
    await _act(narrator)


def _events(llm) -> list[list[str]]:
    return [c.kwargs["recent_events"] for c in llm.generate_commentary.call_args_list]


class TestNarrator:
    """Narrator のテスト。"""

    @pytest.mark.asyncio
    async def test_goal_change_is_told_with_why_the_last_one_ended(self, narrator, llm, said):
        """終わった小目標と次の小目標を、「今していること」とともに 1 回の発話にする。"""
        await narrator.on_goal_ended(
            GoalEndedEvent(goal="placed(bed, home)", ended_because="stalled", met=False)
        )
        await _set(narrator, 
            GoalSetEvent(goal="through_night()", reason="日が暮れる", objective="")
        )
        await narrator.drain()

        assert _events(llm) == [
            [
                "小目標 placed(bed, home) が未達成でやめた（stalled）",
                NEW + "through_night()（身を守るため。日が暮れる）",
            ]
        ]
        assert llm.generate_commentary.call_args.kwargs["activity"] is ACTIVITY
        assert said == ["次は羊を探すよ"]

    @pytest.mark.asyncio
    async def test_the_objective_served_is_told(self, narrator, llm):
        """次の小目標は、どの大目標のためかを添えて言う。"""
        await _set(narrator, 
            GoalSetEvent(goal="have(log, 3)", reason="剣の材料", objective="身を守る道具を持つ")
        )
        await narrator.drain()

        assert _events(llm) == [
            [NEW + "have(log, 3)（「身を守る道具を持つ」のため。剣の材料）"]
        ]

    @pytest.mark.asyncio
    async def test_a_new_goal_says_where_she_is_now(self, narrator, llm):
        """実測（2026-09-27）: 家から 390m 離れているのに、at_home を決めただけで「家に入った」。"""
        obs = GameObservation(
            state={
                "home": {"door": {"x": -76, "y": 65, "z": -151}},
                "self": {"position": {"x": -73.3, "y": 63, "z": -414.3}},
            },
            candidates=(),
            health=20.0,
            food=20,
            has_home=True,
        )
        current[0] = Activity(observation=obs)
        try:
            await _set(narrator, 
                GoalSetEvent(goal="at_home()", reason="夜は危ない", objective="")
            )
            await narrator.drain()
        finally:
            current[0] = ACTIVITY

        assert _events(llm) == [
            [
                NEW + "at_home()（身を守るため。"
                "夜は危ない）。今いる場所: 家の外（家まで 263m）"
            ]
        ]

    @pytest.mark.asyncio
    async def test_a_job_done_is_kept_for_the_next_line_and_does_not_force_one(self, narrator, llm):
        """済んだ中目標は次の切れ目に話す。中目標が済むだけでは話さない。"""
        await narrator.on_job_done(
            JobDoneEvent(objective="倉庫", job="have(oak_log, 8)", reason="壁の材料", suggested_by="neko")
        )
        await narrator.drain()
        llm.generate_commentary.assert_not_called()
        await _set(narrator, GoalSetEvent(goal="have(stone, 4)", reason="", objective="倉庫"))
        await narrator.drain()
        assert _events(llm)[0][0] == (
            "大目標「倉庫」の中目標 have(oak_log, 8)（nekoさんの提案）が済んだ（壁の材料）"
        )

    @pytest.mark.asyncio
    async def test_objectives_ended_are_told_with_the_next_goal(self, narrator, llm):
        """完了した大目標とやめた大目標（視聴者のものも）は言う。黙って消さない。"""
        await narrator.on_objective_completed(ObjectiveCompletedEvent(title="自分の家を作る"))
        await narrator.on_objective_dropped(
            ObjectiveDroppedEvent(title="探検", reason="予算を超えた", requested_by="tori")
        )
        await _set(narrator, 
            GoalSetEvent(goal="placed(bed, home)", reason="", objective="寝る")
        )
        await narrator.drain()

        assert _events(llm)[0][:2] == [
            "大目標「自分の家を作る」が完了した",
            "大目標「探検」（toriさんの頼み）をやめた（予算を超えた）",
        ]

    @pytest.mark.asyncio
    async def test_own_new_objective_is_told_a_viewers_is_not(self, narrator, llm):
        """受けた頼みはもう一度言わない（返答で言った）。"""
        await narrator.on_objective_added(
            ObjectiveAddedEvent(title="ベッド", reason="頼まれた", requested_by="neko", position=2)
        )
        await narrator.on_objective_added(
            ObjectiveAddedEvent(title="剣を持つ", reason="夜に備える", position=3)
        )
        await _set(narrator, GoalSetEvent(goal="built()", reason="", objective="家"))
        await narrator.drain()

        assert _events(llm)[0][0] == "大目標「剣を持つ」をリストの 3 番目に足した（夜に備える）"
        assert len(_events(llm)[0]) == 2

    @pytest.mark.asyncio
    async def test_empty_commentary_says_nothing(self, narrator, llm, said):
        """生成に失敗したら何も言わない。"""
        llm.generate_commentary.return_value = ""
        await _set(narrator, GoalSetEvent(goal="built()", reason=""))
        await narrator.drain()

        assert said == []

    @pytest.mark.asyncio
    async def test_activity_is_taken_when_the_event_happens(self, narrator, llm):
        """実況を生成している間に決まった小目標は、その実況に紛れ込まない。"""
        await _set(narrator, GoalSetEvent(goal="built()", reason=""))
        current[0] = Activity(recent_goals=())  # セッションが先に進む
        try:
            await narrator.drain()
        finally:
            current[0] = ACTIVITY

        assert llm.generate_commentary.call_args.kwargs["activity"] is ACTIVITY

    @pytest.mark.asyncio
    async def test_house_completion_is_told_with_the_next_goal(self, narrator, llm):
        """完了と次にすることを 1 回の発話にする。"""
        await narrator.on_house_completed(HouseCompletedEvent(name="ぽかぽか"))
        await narrator.on_goal_ended(GoalEndedEvent(goal="built()", ended_because="met", met=True))
        await _set(narrator, 
            GoalSetEvent(goal="have(wooden_sword, 1)", reason="身を守る", objective="剣")
        )
        await narrator.drain()

        assert _events(llm) == [
            [
                "家「ぽかぽか」が完成した",
                "小目標 built() が達成（met）",
                NEW + "have(wooden_sword, 1)（「剣」のため。身を守る）",
            ]
        ]

    @pytest.mark.asyncio
    async def test_the_town_is_told_when_decided_and_when_done(self, narrator, llm):
        """街の定義と完成を、次の小目標と一緒に言う。"""
        await narrator.on_town_site_chosen(
            TownSiteChosenEvent(name="いしのまち", site_id="E", reason="石が多い", moving=True)
        )
        await narrator.on_town_defined(TownDefinedEvent(text="小さな街", stages=("備蓄", "明かり")))
        await narrator.on_town_completed(TownCompletedEvent(text="小さな街"))
        await _set(narrator, GoalSetEvent(goal="at_home()", reason="夜", objective=""))
        await narrator.drain()

        assert _events(llm)[0][:3] == [
            "街「いしのまち」の場所を決めた（そこに家を建てて引っ越す）: 石が多い",
            "最終目標の街をこう決めた: 小さな街（段階: 備蓄 → 明かり）",
            "街が完成した（小さな街）",
        ]

    @pytest.mark.asyncio
    async def test_a_learned_skill_is_told_at_once_and_a_fix_with_the_next_goal(
        self, narrator, llm
    ):
        """技を覚えたらすぐ話す（見どころ）。直したことは次の目標と一緒に（docs/design/22 §7）。"""
        await narrator.on_skill_learned(
            SkillLearnedEvent(name="hunt", description="近くの動物を狩る")
        )
        await narrator.drain()
        assert _events(llm) == [["新しい技を覚えた: 近くの動物を狩る（hunt。初めてうまくいった）"]]

        await narrator.on_skill_revised(
            SkillRevisedEvent(name="hunt", description="狩り", version=2, reason="no pig")
        )
        await _set(narrator, GoalSetEvent(goal="have(food, 2)", reason="", objective=""))
        await narrator.drain()
        assert _events(llm)[1][0] == "技「狩り」を直した（v2）（前の失敗: no pig）"


def _pursuing(item: str, count: int) -> Activity:
    """have(item, count) を追っている最中（話した予告を、今も追っている小目標として覚えるため）。"""
    return Activity(goal=Goal(GoalSpec(GoalPredicate.HAVE, item=item, count=count), "理由"))


class TestForecastAfterAction:
    """予告は行動が 1 つ成功してから言う。話した予告をやめたら、やめたと言う（設計書 65 §3）。"""

    @pytest.mark.asyncio
    async def test_a_new_goal_is_not_told_until_an_action_succeeds(self, narrator, llm):
        await narrator.on_goal_set(GoalSetEvent(goal="have(log, 3)", reason="剣の材料", objective="剣"))
        await narrator.drain()
        llm.generate_commentary.assert_not_called()

        await _act(narrator, ok=False)  # 失敗は、動き出したことにならない
        await narrator.drain()
        llm.generate_commentary.assert_not_called()

        await _act(narrator)
        await narrator.drain()
        assert _events(llm) == [[NEW + "have(log, 3)（「剣」のため。剣の材料）"]]

        await _act(narrator)  # 予告は 1 回だけ
        await narrator.drain()
        assert len(_events(llm)) == 1

    @pytest.mark.asyncio
    async def test_a_goal_that_ends_before_any_action_is_never_told(self, narrator, llm):
        await narrator.on_goal_set(GoalSetEvent(goal="through_night()", reason="夜", objective=""))
        await narrator.on_goal_ended(
            GoalEndedEvent(goal="through_night()", ended_because="met", met=True)
        )
        await _set(narrator, GoalSetEvent(goal="have(log, 3)", reason="", objective="剣"))
        await narrator.drain()

        assert _events(llm) == [[NEW + "have(log, 3)（「剣」のため。）"]]  # through_night は出ない

    @pytest.mark.asyncio
    async def test_a_highlight_that_was_waiting_is_told_when_the_new_goal_ends_unacted(
        self, narrator, llm
    ):
        await narrator.on_house_completed(HouseCompletedEvent(name="ぽかぽか"))
        await narrator.on_goal_set(GoalSetEvent(goal="have(log, 3)", reason="", objective="剣"))
        await narrator.on_goal_ended(
            GoalEndedEvent(goal="have(log, 3)", ended_because="stuck", met=False)
        )
        await narrator.drain()

        assert _events(llm) == [["家「ぽかぽか」が完成した"]]

    @pytest.mark.asyncio
    async def test_dropping_a_told_forecast_is_said_and_not_left_to_the_gate(self, llm):
        said, asked = [], []

        async def say(text):
            said.append(text)

        async def gate(events, quiet):
            asked.append(events)
            return False

        clock = Clock()
        narrator = Narrator(llm, activity=lambda: current[0], say=say, gate=gate, clock=clock)
        current[0] = _pursuing("log", 3)
        try:
            await _set(narrator, GoalSetEvent(goal="have(log, 3)", reason="剣", objective="剣"))
            await narrator.drain()  # 初めては必ず話す
            assert len(said) == 1
            await narrator.on_goal_ended(
                GoalEndedEvent(goal="have(log, 3)", ended_because="stuck", met=False)
            )
            current[0] = _pursuing("stone", 4)
            clock.now = 5
            await _set(narrator, GoalSetEvent(goal="have(stone, 4)", reason="", objective="剣"))
            await narrator.drain()
        finally:
            current[0] = ACTIVITY

        assert asked == []  # 話したことをやめたのだから、Jev の「今はいい」に任せない
        assert len(said) == 2
        assert _events(llm)[1][0].startswith("さっき次にやると話した小目標 have(log, 3) を、やり遂げずにやめた（stuck）")

    @pytest.mark.asyncio
    async def test_a_told_forecast_that_was_met_is_told_as_met(self, narrator, llm):
        current[0] = _pursuing("log", 3)
        try:
            await _set(narrator, GoalSetEvent(goal="have(log, 3)", reason="", objective="剣"))
            await narrator.drain()
            await narrator.on_goal_ended(
                GoalEndedEvent(goal="have(log, 3)", ended_because="met", met=True)
            )
            await _set(narrator, GoalSetEvent(goal="have(stone, 4)", reason="", objective="剣"))
            await narrator.drain()
        finally:
            current[0] = ACTIVITY

        assert _events(llm)[1][0] == "小目標 have(log, 3) が達成（met）"

    @pytest.mark.asyncio
    async def test_a_forecast_the_gate_kept_back_is_forgotten_when_the_goal_ends(self, llm):
        said = []

        async def say(text):
            said.append(text)

        async def gate(events, quiet):
            return False

        clock = Clock()
        narrator = Narrator(llm, activity=lambda: current[0], say=say, gate=gate, clock=clock)
        current[0] = _pursuing("log", 3)
        try:
            await _set(narrator, GoalSetEvent(goal="have(log, 3)", reason="", objective="剣"))
            await narrator.drain()  # 初めては必ず話す
            clock.now = 5
            current[0] = _pursuing("stone", 4)
            await _set(narrator, GoalSetEvent(goal="have(stone, 4)", reason="", objective="剣"))
            await narrator.drain()  # Jev が「今はいい」: 予告は話していない
            await narrator.on_goal_ended(
                GoalEndedEvent(goal="have(stone, 4)", ended_because="stuck", met=False)
            )
            await _set(narrator, GoalSetEvent(goal="have(coal, 2)", reason="", objective="剣"))
            await narrator.drain()
        finally:
            current[0] = ACTIVITY

        assert len(said) == 1
        assert all("have(stone, 4)" not in e for e in narrator._pending)


class TestMemberMetAndUnheard:
    @pytest.mark.asyncio
    async def test_a_bundle_member_that_is_met_first_is_told_after_the_bundle_is_announced(
        self, narrator, llm
    ):
        await narrator.on_goal_set(
            GoalSetEvent(goal="have(furnace, 1) + have(coal, 1)", reason="炉を使う", objective="鉄")
        )
        await narrator.on_member_met(
            GoalMemberMetEvent(
                goal="have(furnace, 1) + have(coal, 1)", member="have(furnace, 1)", left=("have(coal, 1)",)
            )
        )
        await narrator.drain()

        (events,) = _events(llm)
        assert events[0].startswith(NEW + "have(furnace, 1) + have(coal, 1)")
        assert events[1].startswith("一緒に追っている have(furnace, 1) + have(coal, 1) のうち have(furnace, 1) が済んだ")

    @pytest.mark.asyncio
    async def test_a_forecast_that_was_never_heard_is_not_reported_as_dropped(self, narrator, llm, said):
        current[0] = _pursuing("log", 3)
        try:
            await _set(narrator, GoalSetEvent(goal="have(log, 3)", reason="", objective="剣"))
            await narrator.drain()
            narrator.unheard(said[0])  # 読み上げが古くなって捨てた
            await narrator.on_goal_ended(
                GoalEndedEvent(goal="have(log, 3)", ended_because="stuck", met=False)
            )
        finally:
            current[0] = ACTIVITY

        assert narrator._pending == ["小目標 have(log, 3) が未達成でやめた（stuck）"]  # 「さっき話した」は言わない
        assert not narrator._must_speak

    @pytest.mark.asyncio
    async def test_a_waiting_highlight_is_not_trimmed_away(self, narrator):
        await narrator.on_house_completed(HouseCompletedEvent(name="ぽかぽか"))
        for i in range(12):
            await narrator.on_goal_ended(
                GoalEndedEvent(goal=f"have(log, {i})", ended_because="stuck", met=False)
            )

        assert "家「ぽかぽか」が完成した" in narrator._pending


class TestGuard:
    """実況は、読む直前に今の状態と照らす。通った文だけ会話の履歴に入れる（設計書 65 §4）。"""

    @staticmethod
    def _narrator(llm, said, remembered, verify):
        async def say(text):
            said.append(text)

        guard = AsyncMock()
        guard.verify.side_effect = verify
        return Narrator(
            llm, activity=lambda: ACTIVITY, say=say, guard=guard, remember=remembered.append
        ), guard

    @pytest.mark.asyncio
    async def test_a_line_that_passes_is_said_and_kept_in_the_conversation(self, llm, said):
        remembered = []

        async def verify(text, regenerate):
            return text

        narrator, _ = self._narrator(llm, said, remembered, verify)
        await _set(narrator, GoalSetEvent(goal="have(log, 3)", reason="", objective="剣"))
        await narrator.drain()

        assert said == ["次は羊を探すよ"] and remembered == ["次は羊を探すよ"]
        assert llm.generate_commentary.call_args.kwargs["record"] is False  # 通ってから入れる

    @pytest.mark.asyncio
    async def test_a_line_that_is_dropped_is_neither_said_nor_remembered(self, llm, said):
        remembered = []

        async def verify(text, regenerate):
            return None

        narrator, _ = self._narrator(llm, said, remembered, verify)
        await _set(narrator, GoalSetEvent(goal="have(log, 3)", reason="", objective="剣"))
        await narrator.drain()

        assert said == [] and remembered == []

    @pytest.mark.asyncio
    async def test_a_rewrite_is_generated_from_the_same_events_plus_the_reason_and_the_state_now(
        self, llm, said
    ):
        remembered = []
        llm.generate_commentary.side_effect = ["家に入れたよ", "まだ外だよ"]

        async def verify(text, regenerate):
            return await regenerate(["前の案は食い違っている"])

        narrator, _ = self._narrator(llm, said, remembered, verify)
        current[0] = Activity(recent_goals=())  # 作り直すときは、今の状態で
        try:
            await _set(narrator, GoalSetEvent(goal="have(log, 3)", reason="", objective="剣"))
            await narrator.drain()
        finally:
            current[0] = ACTIVITY

        first, second = llm.generate_commentary.call_args_list
        assert second.kwargs["recent_events"] == first.kwargs["recent_events"] + ["前の案は食い違っている"]
        assert second.kwargs["record"] is False
        assert said == ["まだ外だよ"] and remembered == ["まだ外だよ"]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.mark.asyncio
async def test_the_gate_keeps_routine_changes_for_the_next_line_but_highlights_and_silence_speak(llm):
    """実況の間合い（docs/design/34 §6）。"""
    said, asked = [], []
    clock = Clock()

    async def say(text):
        said.append(text)

    async def gate(events, quiet):
        asked.append((list(events), quiet))
        return False  # Jev: 今はいい

    narrator = Narrator(llm, activity=lambda: ACTIVITY, say=say, gate=gate, max_silence_seconds=60, clock=clock)
    goal = lambda g: GoalSetEvent(goal=g, reason="r", objective="家")  # noqa: E731

    await _set(narrator, goal("a"))  # 初めては必ず話す
    await narrator.drain()
    clock.now = 10
    await _set(narrator, goal("b"))  # Jev が「今はいい」: 取っておく
    await narrator.drain()
    assert len(said) == 1 and asked[-1][1] == 10

    await narrator.on_objective_completed(ObjectiveCompletedEvent(title="家"))
    await _set(narrator, goal("c"))  # 見どころ: 聞かずに話す、取っておいたものも一緒に
    await narrator.drain()
    assert len(said) == 2 and len(asked) == 1
    assert any("b" in e for e in _events(llm)[-1]) and any("完了" in e for e in _events(llm)[-1])

    clock.now = 100
    await _set(narrator, goal("d"))  # 60 秒黙っていた: 聞かずに話す
    await narrator.drain()
    assert len(said) == 3 and len(asked) == 1


class TestThinkingNotice:
    """考え始めたときの定型の一言（決まるまで無音になるのを防ぐ）。"""

    class Clock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    def _narrator(self, llm, said, clock):
        async def notice(text):
            said.append(text)

        async def say(text):
            said.append(("commentary", text))

        return Narrator(llm, activity=lambda: ACTIVITY, say=say, clock=clock, notice=notice)

    @pytest.mark.asyncio
    async def test_says_a_fixed_line_without_asking_the_llm(self, llm):
        said = []
        narrator = self._narrator(llm, said, self.Clock())

        await narrator.on_goal_deciding(GoalDecidingEvent())
        await narrator.drain()

        assert said == [THINKING_NOTICES[0]]
        assert "考える" in said[0]
        llm.generate_commentary.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_a_failure_it_says_it_will_think_again(self, llm):
        said = []
        narrator = self._narrator(llm, said, self.Clock())

        await narrator.on_goal_deciding(GoalDecidingEvent(after_failure=True))
        await narrator.drain()

        assert said == [RETHINKING_NOTICES[0]]

    @pytest.mark.asyncio
    async def test_it_does_not_say_it_twice_in_a_row_and_rotates_the_lines(self, llm):
        said = []
        clock = self.Clock()
        narrator = self._narrator(llm, said, clock)

        await narrator.on_goal_deciding(GoalDecidingEvent())  # 計画を書く
        clock.now = 40
        await narrator.on_goal_deciding(GoalDecidingEvent())  # 続けて目標を決める: 言わない
        clock.now = THINKING_NOTICE_INTERVAL_SECONDS + 1
        await narrator.on_goal_deciding(GoalDecidingEvent())
        await narrator.drain()

        assert said == [THINKING_NOTICES[0], THINKING_NOTICES[1]]

    @pytest.mark.asyncio
    async def test_it_stays_quiet_without_a_place_to_say_it(self, llm):
        narrator = Narrator(llm, activity=lambda: ACTIVITY, say=AsyncMock())

        await narrator.on_goal_deciding(GoalDecidingEvent())
        await narrator.drain()

        llm.generate_commentary.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failing_voice_does_not_break_the_decision(self, llm):
        async def notice(text):
            raise RuntimeError("tts down")

        narrator = Narrator(llm, activity=lambda: ACTIVITY, say=AsyncMock(), notice=notice)

        await narrator.on_goal_deciding(GoalDecidingEvent())
        await narrator.drain()  # 例外を出さない
