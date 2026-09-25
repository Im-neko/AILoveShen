"""Keeping the mid goals: judged from the world, edited within the plan's limits, saved, told."""

from __future__ import annotations

import copy
from collections.abc import Sequence

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.use_cases.goal_vocabulary import (
    MidGoalProposal,
    PlanChange,
    PlanOp,
)
from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.events import (
    DomainEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
    TownCompletedEvent,
)
from ailoveshen.domain.exceptions import GoalRejectedError
from ailoveshen.domain.value_objects import MidGoal


class MidGoalKeeper:
    """
    The one way the mid-goal list changes after the plan is made.

    Two callers edit it concurrently: the goal decision (the streamer's own
    edits, at small-goal boundaries) and chat replies (a viewer's request
    accepted). Edits are rehearsed on a copy, then applied to the plan with
    no await in between, so neither overwrites the other; the plan object is
    never replaced (the goal board and the narrator hold it).

    A mid goal's conditions are checked by the bridge before it is added, so
    nothing is promised that the world cannot judge. Every change is saved
    and published (MidGoalAdded/Completed/DroppedEvent).
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        store: IMissionStore,
    ) -> None:
        """
        Initialize with dependencies (Dependency Injection).

        Args:
            bridge: Minecraft bridge adapter (judges conditions)
            event_publisher: Event publisher for domain events
            store: Keeps the plan across restarts
        """
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._store = store

    async def judge(self, plan: MidGoalPlan) -> None:
        """
        Judge the pending mid goals from the world; those whose conditions all hold are done.

        A mid goal whose conditions the bridge rejects (it could not have been
        added so, but the world's rules may have changed) is dropped with the
        reason rather than stopping play. A town stage is never dropped: it is
        logged and waits for its conditions to be fixed.

        Then the town moves on: what can be done now of the stage worked on
        (its conditions not met yet) becomes a mid goal at the top of the list;
        the parts left for abilities not there yet wait. The town's completion
        is told when its last stage is done.
        """
        was_complete = plan.town_complete
        events: list[DomainEvent] = []
        for goal in plan.pending:
            try:
                statuses = await self._bridge.check(goal.conditions)
            except GoalRejectedError as e:
                if goal.stage is not None:
                    logger.error(f"Town stage mid goal {goal.describe()} cannot be judged: {e}")
                elif plan.get(goal.id) is not None:
                    events.append(
                        _dropped(plan.drop(goal.id, f"its conditions cannot be judged: {e}"))
                    )
                continue
            if plan.get(goal.id) is None:
                continue
            plan.judged(goal.id, tuple(line for s in statuses for line in s.lines))
            if all(s.met for s in statuses):
                done = plan.complete(goal.id)
                logger.info(f"Mid goal done: {done.describe()}")
                events.append(
                    MidGoalCompletedEvent(title=done.title, requested_by=done.requested_by or "")
                )
        events.extend(self._next_stage(plan))
        if plan.town_complete and not was_complete:
            assert plan.town is not None
            logger.info(f"Town complete: {plan.town.text}")
            events.append(TownCompletedEvent(text=plan.town.text))
        self._store.save(plan)
        await self._publish(events)

    def _next_stage(self, plan: MidGoalPlan) -> list[DomainEvent]:
        plan.settle_stage()  # a stage written again may be done already
        stage = plan.current_stage
        if stage is None or not plan.stage_remaining or plan.stage_goal() is not None:
            return []
        try:
            goal = plan.add(
                title=stage.title,
                conditions=plan.stage_remaining,
                reason=stage.why,
                position=0,
                stage=plan.town_stage,
            )
        except ValueError as e:
            logger.warning(f"Town stage {plan.town_stage + 1} waits for room in the list: {e}")
            return []
        logger.info(
            f"Town stage {plan.town_stage + 1} is now mid goal {goal.id}: {goal.describe()}"
        )
        return [_added(plan, goal)]

    async def check_new(self, changes: Sequence[PlanChange]) -> None:
        """
        Have the bridge check the conditions of the mid goals the changes add.

        Raises:
            GoalRejectedError: If a condition cannot be judged (the message says why)
        """
        for change in changes:
            if change.proposal is not None:
                await self._bridge.check(change.proposal.conditions)

    def rehearse(self, plan: MidGoalPlan, changes: Sequence[PlanChange]) -> MidGoalPlan:
        """
        The plan as it would be after the changes (the plan itself is untouched).

        Raises:
            ValueError: If a change breaks a limit or names no pending mid goal
        """
        preview = copy.deepcopy(plan)
        _apply(preview, changes)
        return preview

    async def commit(self, plan: MidGoalPlan, changes: Sequence[PlanChange]) -> None:
        """
        Apply the streamer's own changes to the plan, save and publish them.

        Raises:
            ValueError: If a change breaks a limit (nothing is applied then)
        """
        if not changes:
            return
        self.rehearse(plan, changes)
        events = _apply(plan, changes)
        self._store.save(plan)
        await self._publish(events)

    async def accept(
        self, plan: MidGoalPlan, proposal: MidGoalProposal, requested_by: str
    ) -> MidGoal:
        """
        Add a viewer's request as a mid goal (behind the one worked on now).

        Raises:
            GoalRejectedError: If a condition cannot be judged
            ValueError: If it breaks a limit (e.g. the viewer already has one)
        """
        await self._bridge.check(proposal.conditions)
        goal = plan.add(
            title=proposal.title,
            conditions=proposal.conditions,
            reason=proposal.reason,
            requested_by=requested_by,
            position=proposal.position,
        )
        logger.info(
            f"Viewer request accepted as mid goal {goal.id}: {goal.describe()} ({requested_by})"
        )
        self._store.save(plan)
        await self._publish([_added(plan, goal)])
        return goal

    async def step_counted(self, plan: MidGoalPlan, goal: MidGoal | None) -> None:
        """Save the plan after a step was counted, telling a mid goal dropped over its budget."""
        self._store.save(plan)
        if goal is not None:
            logger.info(f"Mid goal dropped: {goal.describe()} ({goal.ended_because})")
            await self._publish([_dropped(goal)])

    async def _publish(self, events: list[DomainEvent]) -> None:
        for event in events:
            await self._event_publisher.publish(event)


def _apply(plan: MidGoalPlan, changes: Sequence[PlanChange]) -> list[DomainEvent]:
    events: list[DomainEvent] = []
    for change in changes:
        if change.op == PlanOp.ADD:
            assert change.proposal is not None
            p = change.proposal
            goal = plan.add(
                title=p.title,
                conditions=p.conditions,
                reason=p.reason or change.reason,
                position=p.position,
            )
            events.append(_added(plan, goal))
        elif change.op == PlanOp.MOVE:
            assert change.position is not None
            plan.move(change.mid_goal_id, change.position)
        else:
            events.append(_dropped(plan.drop(change.mid_goal_id, change.reason)))
    return events


def _added(plan: MidGoalPlan, goal: MidGoal) -> MidGoalAddedEvent:
    position = next(i for i, g in enumerate(plan.pending) if g.id == goal.id) + 1
    return MidGoalAddedEvent(
        title=goal.title,
        reason=goal.reason,
        requested_by=goal.requested_by or "",
        position=position,
    )


def _dropped(goal: MidGoal) -> MidGoalDroppedEvent:
    return MidGoalDroppedEvent(
        title=goal.title, reason=goal.ended_because, requested_by=goal.requested_by or ""
    )
