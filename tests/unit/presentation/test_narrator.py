"""Narrator（小目標の変化を口に出す）のテスト。"""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.domain.events import (
    GoalEndedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
    TownCompletedEvent,
    TownDefinedEvent,
    TownSiteChosenEvent,
)
from ailoveshen.domain.value_objects import Activity
from ailoveshen.presentation.services.narrator import Narrator

ACTIVITY = Activity()


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
        await narrator.on_goal_set(
            GoalSetEvent(goal="through_night()", reason="日が暮れる", mid_goal="")
        )
        await narrator.drain()

        assert _events(llm) == [
            [
                "小目標 placed(bed, home) が未達成でやめた（stalled）",
                "新しい小目標: through_night()（身を守るため。日が暮れる）",
            ]
        ]
        assert llm.generate_commentary.call_args.kwargs["activity"] is ACTIVITY
        assert said == ["次は羊を探すよ"]

    @pytest.mark.asyncio
    async def test_the_mid_goal_served_is_told(self, narrator, llm):
        """次の小目標は、どの中目標のためかを添えて言う。"""
        await narrator.on_goal_set(
            GoalSetEvent(goal="have(log, 3)", reason="剣の材料", mid_goal="身を守る道具を持つ")
        )
        await narrator.drain()

        assert _events(llm) == [
            ["新しい小目標: have(log, 3)（「身を守る道具を持つ」のため。剣の材料）"]
        ]

    @pytest.mark.asyncio
    async def test_mid_goals_ended_are_told_with_the_next_goal(self, narrator, llm):
        """完了した中目標とやめた中目標（視聴者のものも）は言う。黙って消さない。"""
        await narrator.on_mid_goal_completed(MidGoalCompletedEvent(title="自分の家を作る"))
        await narrator.on_mid_goal_dropped(
            MidGoalDroppedEvent(title="探検", reason="予算を超えた", requested_by="tori")
        )
        await narrator.on_goal_set(
            GoalSetEvent(goal="placed(bed, home)", reason="", mid_goal="寝る")
        )
        await narrator.drain()

        assert _events(llm)[0][:2] == [
            "中目標「自分の家を作る」が完了した",
            "中目標「探検」（toriさんの頼み）をやめた（予算を超えた）",
        ]

    @pytest.mark.asyncio
    async def test_own_new_mid_goal_is_told_a_viewers_is_not(self, narrator, llm):
        """受けた頼みはもう一度言わない（返答で言った）。"""
        await narrator.on_mid_goal_added(
            MidGoalAddedEvent(title="ベッド", reason="頼まれた", requested_by="neko", position=2)
        )
        await narrator.on_mid_goal_added(
            MidGoalAddedEvent(title="剣を持つ", reason="夜に備える", position=3)
        )
        await narrator.on_goal_set(GoalSetEvent(goal="built()", reason="", mid_goal="家"))
        await narrator.drain()

        assert _events(llm)[0][0] == "中目標「剣を持つ」をリストの 3 番目に足した（夜に備える）"
        assert len(_events(llm)[0]) == 2

    @pytest.mark.asyncio
    async def test_empty_commentary_says_nothing(self, narrator, llm, said):
        """生成に失敗したら何も言わない。"""
        llm.generate_commentary.return_value = ""
        await narrator.on_goal_set(GoalSetEvent(goal="built()", reason=""))
        await narrator.drain()

        assert said == []

    @pytest.mark.asyncio
    async def test_activity_is_taken_when_the_event_happens(self, narrator, llm):
        """実況を生成している間に決まった小目標は、その実況に紛れ込まない。"""
        await narrator.on_goal_set(GoalSetEvent(goal="built()", reason=""))
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
        await narrator.on_goal_set(
            GoalSetEvent(goal="have(wooden_sword, 1)", reason="身を守る", mid_goal="剣")
        )
        await narrator.drain()

        assert _events(llm) == [
            [
                "家「ぽかぽか」が完成した",
                "小目標 built() が達成（met）",
                "新しい小目標: have(wooden_sword, 1)（「剣」のため。身を守る）",
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
        await narrator.on_goal_set(GoalSetEvent(goal="at_home()", reason="夜", mid_goal=""))
        await narrator.drain()

        assert _events(llm)[0][:3] == [
            "街「いしのまち」の場所を決めた（そこに家を建てて引っ越す）: 石が多い",
            "大目標の街をこう決めた: 小さな街（段階: 備蓄 → 明かり）",
            "街が完成した（小さな街）",
        ]
