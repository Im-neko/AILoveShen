"""中目標の管理: 世界から判定し、プランの上限の中で編集し、保存し、伝える。"""

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
    プランを作ったあと、中目標のリストを変える唯一の経路。

    2 つの呼び出し元が並行して編集する: 目標の決定（小目標の切れ目での配信者自身の
    編集）と、チャットの返答（視聴者の頼みを受けたとき）。編集は写しの上で試して
    から、await を挟まずにプランへ反映する。こうして互いに上書きしない。プランの
    オブジェクトは差し替えない（ゴールボードとナレーターが持っている）。

    中目標の条件は、足す前にブリッジが確かめる。世界から判定できないことは約束
    しない。変更はすべて保存し、発行する（MidGoalAdded/Completed/DroppedEvent）。
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        store: IMissionStore,
    ) -> None:
        """
        依存を受け取って初期化する（依存性の注入）。

        Args:
            bridge: Minecraft ブリッジのアダプター（条件を判定する）
            event_publisher: ドメインイベントの発行器
            store: 再起動をまたいでプランを保つ
        """
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._store = store

    async def judge(self, plan: MidGoalPlan) -> None:
        """
        未完了の中目標を世界から判定する。条件がすべて満たされたものは完了にする。

        ブリッジが条件を拒否した中目標（そのままでは足せなかったはずだが、世界の
        ルールが変わったのかもしれない）は、プレイを止めずに理由をつけて断念する。
        街の段階は断念しない: ログに残し、条件が直るのを待つ。

        次に街を進める: 取り組んでいる段階のうち今できること（まだ満たしていない
        条件）を、リストの一番上の中目標にする。まだない能力のために残した部分は
        待つ。最後の段階が済んだら、街の完成を伝える。
        """
        was_complete = plan.town_complete
        events: list[DomainEvent] = []
        for goal in plan.pending:
            try:
                statuses = await self._bridge.check(goal.conditions)
            except GoalRejectedError as e:
                if goal.stage is not None:
                    logger.error(f"街の段階の中目標 {goal.describe()} を判定できない: {e}")
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
                logger.info(f"中目標を達成した: {done.describe()}")
                events.append(
                    MidGoalCompletedEvent(title=done.title, requested_by=done.requested_by or "")
                )
        events.extend(self._next_stage(plan))
        if plan.town_complete and not was_complete:
            assert plan.town is not None
            logger.info(f"街が完成した: {plan.town.text}")
            events.append(TownCompletedEvent(text=plan.town.text))
        self._store.save(plan)
        await self._publish(events)

    def _next_stage(self, plan: MidGoalPlan) -> list[DomainEvent]:
        plan.settle_stage()  # 書き直した段階は、もう済んでいるかもしれない
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
            logger.warning(f"街の段階 {plan.town_stage + 1} はリストに空きが出るのを待つ: {e}")
            return []
        logger.info(f"街の段階 {plan.town_stage + 1} を中目標 {goal.id} にした: {goal.describe()}")
        return [_added(plan, goal)]

    async def check_new(self, changes: Sequence[PlanChange]) -> None:
        """
        変更が足す中目標の条件を、ブリッジに確かめさせる。

        Raises:
            GoalRejectedError: 条件を判定できないとき（メッセージに理由がある）
        """
        for change in changes:
            if change.proposal is not None:
                await self._bridge.check(change.proposal.conditions)

    def rehearse(self, plan: MidGoalPlan, changes: Sequence[PlanChange]) -> MidGoalPlan:
        """
        変更を反映したらどうなるかのプラン（プラン自体には触れない）。

        Raises:
            ValueError: 変更が上限を破るか、未完了の中目標を指していないとき
        """
        preview = copy.deepcopy(plan)
        _apply(preview, changes)
        return preview

    async def commit(self, plan: MidGoalPlan, changes: Sequence[PlanChange]) -> None:
        """
        配信者自身の変更をプランに反映し、保存して発行する。

        Raises:
            ValueError: 変更が上限を破るとき（そのときは何も反映しない）
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
        視聴者の頼みを中目標として足す（今取り組んでいるものの後ろに）。

        Raises:
            GoalRejectedError: 条件を判定できないとき
            ValueError: 上限を破るとき（例: その視聴者はもう 1 つ持っている）
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
            f"視聴者の頼みを中目標 {goal.id} として受けた: {goal.describe()}（{requested_by}）"
        )
        self._store.save(plan)
        await self._publish([_added(plan, goal)])
        return goal

    async def step_counted(self, plan: MidGoalPlan, goal: MidGoal | None) -> None:
        """ステップを数えたあとにプランを保存する。予算を超えて断念した中目標があれば伝える。"""
        self._store.save(plan)
        if goal is not None:
            logger.info(f"中目標を断念した: {goal.describe()}（{goal.ended_because}）")
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
