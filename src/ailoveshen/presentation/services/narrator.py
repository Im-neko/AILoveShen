"""Narrator: says aloud why the streamer's actions change."""

from __future__ import annotations

import asyncio
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
    TownCompletedEvent,
    TownDefinedEvent,
)
from ailoveshen.domain.value_objects import Activity
from ailoveshen.presentation.services.llm_service import LLMService


class Narrator:
    """
    Turns goal changes into commentary, so viewers hear why the streamer does
    something else now.

    What happens at a small-goal boundary is told together with the next goal
    (one utterance): the small goal's end, mid goals completed or dropped
    (a viewer's request dropped is never silent), mid goals the streamer added,
    the house's completion, and the town (what it is when decided, and its
    completion). A viewer's request accepted is not told again
    (the reply already said when it will be done).

    The activity is taken when the event happens, so the commentary talks
    about the goal the bot is on then, not one set while it was generated.
    Commentary runs in the background so the play loop does not wait for it.
    """

    def __init__(
        self,
        llm: LLMService,
        activity: Callable[[], Activity | None],
        say: Callable[[str], Awaitable[None]],
    ) -> None:
        """
        Initialize the narrator.

        Args:
            llm: Generates the commentary
            activity: What the streamer is doing now (e.g. the game session's activity)
            say: Where the commentary goes (TTS, or a log)
        """
        self._llm = llm
        self._activity = activity
        self._say = say
        self._pending: list[str] = []  # what happened, told with the next goal
        self._tasks: set[asyncio.Task[None]] = set()

    def subscribe(self, bus: IEventSubscriber) -> None:
        """Listen to the goal events."""
        bus.subscribe(GoalEndedEvent, self.on_goal_ended)
        bus.subscribe(GoalSetEvent, self.on_goal_set)
        bus.subscribe(MidGoalAddedEvent, self.on_mid_goal_added)
        bus.subscribe(MidGoalCompletedEvent, self.on_mid_goal_completed)
        bus.subscribe(MidGoalDroppedEvent, self.on_mid_goal_dropped)
        bus.subscribe(HouseCompletedEvent, self.on_house_completed)
        bus.subscribe(TownDefinedEvent, self.on_town_defined)
        bus.subscribe(TownCompletedEvent, self.on_town_completed)

    async def on_goal_ended(self, event: GoalEndedEvent) -> None:
        """Keep the end to tell it with the next goal."""
        result = "達成" if event.met else "未達成でやめた"
        self._pending.append(f"小目標 {event.goal} が{result}（{event.ended_because}）")

    async def on_goal_set(self, event: GoalSetEvent) -> None:
        """Tell what happened and the next goal in one utterance."""
        events, self._pending = self._pending, []
        serves = f"「{event.mid_goal}」のため" if event.mid_goal else "身を守るため"
        events.append(f"新しい小目標: {event.goal}（{serves}。{event.reason}）")
        self._comment(events)

    async def on_mid_goal_added(self, event: MidGoalAddedEvent) -> None:
        """The streamer's own new mid goal is told with the next goal; a viewer's was replied."""
        if event.requested_by:
            return
        self._pending.append(
            f"中目標「{event.title}」をリストの {event.position} 番目に足した（{event.reason}）"
        )

    async def on_mid_goal_completed(self, event: MidGoalCompletedEvent) -> None:
        """Keep a completed mid goal to tell it with the next goal."""
        self._pending.append(f"中目標「{event.title}」{_requested(event.requested_by)}が完了した")

    async def on_mid_goal_dropped(self, event: MidGoalDroppedEvent) -> None:
        """Keep a dropped mid goal to tell it with the next goal (never silent)."""
        self._pending.append(
            f"中目標「{event.title}」{_requested(event.requested_by)}をやめた（{event.reason}）"
        )

    async def on_house_completed(self, event: HouseCompletedEvent) -> None:
        """The house is done: told with the next goal (the build goal ends with it)."""
        self._pending.append(f"家「{event.name}」が完成した")

    async def on_town_defined(self, event: TownDefinedEvent) -> None:
        """The town the streamer decided on is told with the next goal."""
        self._pending.append(
            f"大目標の街をこう決めた: {event.text}（段階: {' → '.join(event.stages)}）"
        )

    async def on_town_completed(self, event: TownCompletedEvent) -> None:
        """The town is done: told with the next goal."""
        self._pending.append(f"街が完成した（{event.text}）")

    async def drain(self) -> None:
        """Wait for the commentary still being generated."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _comment(self, events: list[str]) -> None:
        task = asyncio.create_task(self._generate(events, self._activity()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _generate(self, events: list[str], activity: Activity | None) -> None:
        try:
            text = await self._llm.generate_commentary(recent_events=events, activity=activity)
            if text:
                await self._say(text)
        except Exception as e:
            logger.error(f"Narration failed: {e}")


def _requested(user_name: str) -> str:
    return f"（{user_name}さんの頼み）" if user_name else ""
