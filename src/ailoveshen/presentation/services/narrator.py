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
    ViewerRequestRejectedEvent,
    ViewerRequestReplacedEvent,
)
from ailoveshen.domain.value_objects import Activity
from ailoveshen.presentation.services.llm_service import LLMService


class Narrator:
    """
    Turns goal changes into commentary, so viewers hear why the streamer does
    something else now.

    A goal's end (and the house's completion) is told together with what comes
    next (one utterance). A goal set for a viewer's request is not announced
    again (the reply already said it), unless another viewer's request had to
    be dropped for it. A request that could not be taken after all, one that
    gave way to a newer request, and one that ended unmet are always told: a
    promise is never dropped silently.

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
        self._dropped = False  # a viewer's request among them ended unmet
        self._tasks: set[asyncio.Task[None]] = set()

    def subscribe(self, bus: IEventSubscriber) -> None:
        """Listen to the goal events."""
        bus.subscribe(GoalEndedEvent, self.on_goal_ended)
        bus.subscribe(GoalSetEvent, self.on_goal_set)
        bus.subscribe(ViewerRequestRejectedEvent, self.on_request_rejected)
        bus.subscribe(ViewerRequestReplacedEvent, self.on_request_replaced)
        bus.subscribe(HouseCompletedEvent, self.on_house_completed)

    async def on_goal_ended(self, event: GoalEndedEvent) -> None:
        """Keep the end to tell it with the next goal."""
        self._pending.append(_describe_end(event))
        self._dropped = self._dropped or (bool(event.requested_by) and not event.met)

    async def on_goal_set(self, event: GoalSetEvent) -> None:
        """Tell why the goal changed, unless the reply to the viewer already did."""
        events, self._pending = self._pending, []
        dropped, self._dropped = self._dropped, False
        if event.requested_by and not dropped:
            return
        events.append(f"新しい目標: {event.goal}（{event.reason}）")
        self._comment(events)

    async def on_request_rejected(self, event: ViewerRequestRejectedEvent) -> None:
        """A promise that cannot be kept is told right away."""
        self._comment(
            [
                f"{event.user_name}さんに引き受けた {event.goal} は、やっぱりできなかった"
                f"（{event.reason}）"
            ]
        )

    async def on_request_replaced(self, event: ViewerRequestReplacedEvent) -> None:
        """A promise that gave way to a newer request is told right away."""
        self._comment(
            [
                f"{event.user_name}さんに引き受けた {event.goal} は、"
                f"{event.replaced_by}さんの頼みに替えたのでやらない"
            ]
        )

    async def on_house_completed(self, event: HouseCompletedEvent) -> None:
        """The house is done: told with the next goal (the build goal ends with it)."""
        self._pending.append(f"家「{event.name}」が完成した")

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


def _describe_end(e: GoalEndedEvent) -> str:
    who = f"（{e.requested_by}さんの頼み）" if e.requested_by else ""
    result = "達成" if e.met else "未達成でやめた"
    return f"目標 {e.goal}{who} が{result}（{e.ended_because}）"
