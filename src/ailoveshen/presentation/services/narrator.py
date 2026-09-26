"""Narrator: 配信者の行動が変わる理由を声に出して言う。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventSubscriber
from ailoveshen.domain.events import (
    GoalEndedEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
    SkillLearnedEvent,
    SkillRevisedEvent,
    TownCompletedEvent,
    TownDefinedEvent,
    TownSiteChosenEvent,
)
from ailoveshen.domain.value_objects import Activity
from ailoveshen.presentation.services.llm_service import LLMService


PENDING_KEPT = 8  # 話さずに取っておく出来事の数（古いものから落とす）


class Narrator:
    """
    目標の変化を実況にして、配信者がなぜ別のことを始めたのかを視聴者に伝える。

    小目標の切れ目で起きたことは、次の目標と一緒に（1回の発話で）話す:
    小目標の終わり、完了した中目標とやめた中目標（視聴者の頼みをやめたときは
    必ず話す）、配信者が自分で加えた中目標、家の完成、街（選んだ場所とその理由、
    決めたときの中身、完成）。受けた視聴者の頼みは改めて話さない（いつやるかは返答で言っている）。

    activity はイベントが起きたときに取る。そのため実況は、生成中に決まった
    目標ではなく、そのときボットが取り組んでいた目標について話す。
    実況はバックグラウンドで生成し、プレイのループはそれを待たない。
    """

    def __init__(
        self,
        llm: LLMService,
        activity: Callable[[], Activity | None],
        say: Callable[[str], Awaitable[None]],
        gate: Callable[[list[str], float], Awaitable[bool]] | None = None,
        max_silence_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Narrator を初期化する。

        Args:
            llm: 実況を生成する
            activity: 配信者が今していること（例: ゲームのセッションの activity）
            say: 実況の出し先（TTS かログ）
            gate: 小目標の切れ目で、今話す価値があるか（Jev。docs/design/34 §6）。None なら毎回話す
            max_silence_seconds: 最後に話してからこれだけたっていれば、gate に聞かずに話す
            clock: 時計（テストで差し替える）
        """
        self._llm = llm
        self._activity = activity
        self._say = say
        self._gate = gate
        self._max_silence = max_silence_seconds
        self._clock = clock
        self._last_spoke: float | None = None
        self._must_speak = False  # 見どころがあった（gate に聞かずに話す）
        self._pending: list[str] = []  # 起きたこと。次の目標と一緒に話す
        self._tasks: set[asyncio.Task[None]] = set()

    def subscribe(self, bus: IEventSubscriber) -> None:
        """目標のイベントを購読する。"""
        bus.subscribe(GoalEndedEvent, self.on_goal_ended)
        bus.subscribe(GoalSetEvent, self.on_goal_set)
        bus.subscribe(MidGoalAddedEvent, self.on_mid_goal_added)
        bus.subscribe(MidGoalCompletedEvent, self.on_mid_goal_completed)
        bus.subscribe(MidGoalDroppedEvent, self.on_mid_goal_dropped)
        bus.subscribe(HouseCompletedEvent, self.on_house_completed)
        bus.subscribe(TownSiteChosenEvent, self.on_town_site_chosen)
        bus.subscribe(TownDefinedEvent, self.on_town_defined)
        bus.subscribe(TownCompletedEvent, self.on_town_completed)
        bus.subscribe(SkillLearnedEvent, self.on_skill_learned)
        bus.subscribe(SkillRevisedEvent, self.on_skill_revised)

    async def on_goal_ended(self, event: GoalEndedEvent) -> None:
        """小目標の終わりを取っておき、次の目標と一緒に話す。"""
        result = "達成" if event.met else "未達成でやめた"
        self._pending.append(f"小目標 {event.goal} が{result}（{event.ended_because}）")

    async def on_goal_set(self, event: GoalSetEvent) -> None:
        """
        起きたことと次の目標を 1回の発話で話す。gate に聞くときは、聞くところからバックグラウンドで
        （イベントの発行はハンドラーを待つので、プレイのループを止めない）。「今はいい」なら取っておく。
        """
        serves = f"「{event.mid_goal}」のため" if event.mid_goal else "身を守るため"
        self._pending.append(f"新しい小目標: {event.goal}（{serves}。{event.reason}）")
        quiet = (self._clock() - self._last_spoke) if self._last_spoke is not None else None
        if self._gate is None or self._must_speak or quiet is None or quiet >= self._max_silence:
            events, self._pending = self._pending, []
            self._must_speak = False
            self._comment(events)
            return
        self._spawn(self._ask_then_comment(list(self._pending), quiet))

    async def _ask_then_comment(self, events: list[str], quiet: float) -> None:
        assert self._gate is not None
        try:
            speak = await self._gate(events, quiet)
        except Exception as e:  # noqa: BLE001 - 聞けなければ話す
            logger.warning(f"実況の間合いを聞けなかった: {e}")
            speak = True
        if not speak:
            # 話さなかったことは捨てずに、次に話すとき一緒に（古いものから落とす）
            self._pending = self._pending[-PENDING_KEPT:]
            return
        events, self._pending = self._pending, []
        if not events:
            return  # ほかの実況が先に話した
        self._must_speak = False
        await self._generate(events, self._activity())

    async def on_mid_goal_added(self, event: MidGoalAddedEvent) -> None:
        """配信者が自分で加えた中目標は次の目標と一緒に話す。視聴者のものは返答で話している。"""
        if event.requested_by:
            return
        self._pending.append(
            f"中目標「{event.title}」をリストの {event.position} 番目に足した（{event.reason}）"
        )

    async def on_mid_goal_completed(self, event: MidGoalCompletedEvent) -> None:
        """完了した中目標を取っておき、次の目標と一緒に話す。"""
        self._pending.append(f"中目標「{event.title}」{_requested(event.requested_by)}が完了した")
        self._must_speak = True

    async def on_mid_goal_dropped(self, event: MidGoalDroppedEvent) -> None:
        """やめた中目標を取っておき、次の目標と一緒に話す（黙ってやめない）。"""
        self._pending.append(
            f"中目標「{event.title}」{_requested(event.requested_by)}をやめた（{event.reason}）"
        )
        self._must_speak = True

    async def on_house_completed(self, event: HouseCompletedEvent) -> None:
        """家の完成。次の目標と一緒に話す（建築の小目標も同時に終わる）。"""
        self._pending.append(f"家「{event.name}」が完成した")
        self._must_speak = True

    async def on_town_site_chosen(self, event: TownSiteChosenEvent) -> None:
        """候補地から選んだ街の場所と、その理由。次の目標と一緒に話す。"""
        move = "そこに家を建てて引っ越す" if event.moving else "最初の家の場所のまま"
        self._pending.append(f"街「{event.name}」の場所を決めた（{move}）: {event.reason}")
        self._must_speak = True

    async def on_town_defined(self, event: TownDefinedEvent) -> None:
        """配信者が決めた街は、次の目標と一緒に話す。"""
        self._pending.append(
            f"大目標の街をこう決めた: {event.text}（段階: {' → '.join(event.stages)}）"
        )

    async def on_town_completed(self, event: TownCompletedEvent) -> None:
        """街の完成。次の目標と一緒に話す。"""
        self._pending.append(f"街が完成した（{event.text}）")
        self._must_speak = True

    async def on_skill_learned(self, event: SkillLearnedEvent) -> None:
        """技を覚えた（初めて成功した）。見どころなので、次の目標を待たずにすぐ話す。"""
        self._comment(
            [f"新しい技を覚えた: {event.description}（{event.name}。初めてうまくいった）"]
        )

    async def on_skill_revised(self, event: SkillRevisedEvent) -> None:
        """技を書いた・直した。次の目標と一緒に話す。"""
        if event.version <= 1:
            self._pending.append(f"技「{event.description}」を書いて試した")
        else:
            why = f"（前の失敗: {event.reason[:120]}）" if event.reason else ""
            self._pending.append(f"技「{event.description}」を直した（v{event.version}）{why}")

    async def drain(self) -> None:
        """生成中の実況を待つ。"""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _comment(self, events: list[str]) -> None:
        self._spawn(self._generate(events, self._activity()))

    def _spawn(self, work) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _generate(self, events: list[str], activity: Activity | None) -> None:
        try:
            text = await self._llm.generate_commentary(recent_events=events, activity=activity)
            if text:
                self._last_spoke = self._clock()
                await self._say(text)
        except Exception as e:
            logger.error(f"目標の変化の実況に失敗した: {e}")


def _requested(user_name: str) -> str:
    return f"（{user_name}さんの頼み）" if user_name else ""
