"""中目標の管理: 世界から判定し、プランの上限の中で編集し、保存し、伝える。"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Optional

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.use_cases.builds import BuildDesigner
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
from ailoveshen.domain.value_objects import GoalPredicate, GoalSpec, MidGoal

MAX_STAGE_DESIGN_TRIES = 2


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
        builder: BuildDesigner | None = None,
    ) -> None:
        """
        依存を受け取って初期化する（依存性の注入）。

        Args:
            bridge: Minecraft ブリッジのアダプター（条件を判定する）
            event_publisher: ドメインイベントの発行器
            store: 再起動をまたいでプランを保つ
            builder: 条件にまだない建物（built(name)）があるとき、先に設計させる
                （docs/design/25_builds.md）。None なら、ない建物は判定できずに断られる
        """
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._store = store
        self._builder = builder
        self._design_failures: dict[str, int] = {}  # 街の段階の建物の設計に失敗した回数

    async def judge(self, plan: MidGoalPlan) -> None:
        """
        未完了の中目標を世界から判定する。条件がすべて満たされたものは完了にする。

        ブリッジが条件を拒否した中目標（そのままでは足せなかったはずだが、世界の
        ルールが変わったのかもしれない）は、プレイを止めずに理由をつけて断念する。
        街の段階と準備は断念しない: ログに残し、条件が直るのを待つ。

        次に街を進める: 取り組んでいる段階のうち今できること（まだ満たしていない
        条件）を、リストの一番上の中目標にする。場所が決まって準備（調査、引っ越し）が
        済むまでと、まだない能力のために残した部分は待つ。最後の段階が済んだら、
        街の完成を伝える。
        """
        was_complete = plan.town_complete
        events: list[DomainEvent] = []
        for goal in plan.pending:
            try:
                statuses = await self._bridge.check(goal.conditions)
            except GoalRejectedError as e:
                if goal.stage is not None or goal.prepares_town:
                    logger.error(f"街の中目標 {goal.describe()} を判定できない: {e}")
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
        await self._design_stage_builds(plan)
        events.extend(self._next_stage(plan))
        if plan.town_complete and not was_complete:
            assert plan.town is not None
            logger.info(f"街が完成した: {plan.town.text}")
            events.append(TownCompletedEvent(text=plan.town.text))
        self._store.save(plan)
        await self._publish(events)

    def _next_stage(self, plan: MidGoalPlan) -> list[DomainEvent]:
        # 段階の条件は家を基準に判定する: 引っ越す前の家で満たしても意味がない
        if plan.site is None or plan.preparing_town:
            return []
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

    async def _design_stage_builds(self, plan: MidGoalPlan) -> None:
        """
        今の街の段階の条件に、まだない建物（built(name)）があれば、中目標にする前に設計させる。
        失敗した名前は MAX_STAGE_DESIGN_TRIES 回まで（毎回の切れ目で呼び続けない）。
        """
        stage = plan.current_stage
        if self._builder is None or stage is None or plan.site is None or plan.preparing_town:
            return
        names = [
            c.name
            for c in plan.stage_remaining
            if c.predicate == GoalPredicate.BUILT
            and c.name is not None
            and self._design_failures.get(c.name, 0) < MAX_STAGE_DESIGN_TRIES
        ]
        if not names:
            return
        known = await self._builder.known()
        for name in names:
            if name in known:
                continue
            try:
                await self._builder.design(name, f"{stage.title}: {stage.why}")
            except GoalRejectedError as e:
                self._design_failures[name] = self._design_failures.get(name, 0) + 1
                logger.error(f"街の段階の建物 {name} を設計できない: {e}")

    async def add_town_goal(
        self,
        plan: MidGoalPlan,
        title: str,
        conditions: tuple[GoalSpec, ...],
        reason: str,
        position: Optional[int] = None,
    ) -> Optional[MidGoal]:
        """
        街の準備の中目標（候補地の調査、引っ越し）を足す。条件はコードが決めたもの。
        リストがいっぱいなら足さずに None を返す（次の切れ目でまた試す）。
        """
        try:
            goal = plan.add(
                title=title,
                conditions=conditions,
                reason=reason,
                position=position,
                prepares_town=True,
            )
        except ValueError as e:
            logger.warning(f"街の準備「{title}」はリストに空きが出るのを待つ: {e}")
            return None
        logger.info(f"街の準備を中目標 {goal.id} にした: {goal.describe()}")
        self._store.save(plan)
        await self._publish([_added(plan, goal)])
        return goal

    async def check_new(self, changes: Sequence[PlanChange]) -> None:
        """
        変更が足す中目標の条件を、ブリッジに確かめさせる。

        Raises:
            GoalRejectedError: 条件を判定できないとき（メッセージに理由がある）
        """
        for change in changes:
            if change.proposal is not None:
                await self._design_builds(change.proposal)
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
        blocks = await self._design_builds(proposal)
        await self._bridge.check(proposal.conditions)
        goal = plan.add(
            title=proposal.title,
            conditions=proposal.conditions,
            reason=proposal.reason,
            requested_by=requested_by,
            position=proposal.position,
            # 建物は大きさに合わせて予算を広げる（1 ブロック 2 ステップと、材料集め）
            budget=max(plan.viewer_budget, 2 * blocks + 40) if blocks else None,
        )
        logger.info(
            f"視聴者の頼みを中目標 {goal.id} として受けた: {goal.describe()}（{requested_by}）"
        )
        self._store.save(plan)
        await self._publish([_added(plan, goal)])
        return goal

    async def _design_builds(self, proposal: MidGoalProposal) -> int:
        """
        条件の built(name) のうち、まだない建物を設計させて登録する。建物のブロックの合計を返す。

        Raises:
            GoalRejectedError: 設計できなかったとき
        """
        names = [
            c.name
            for c in proposal.conditions
            if c.predicate == GoalPredicate.BUILT and c.name is not None
        ]
        if not names:
            return 0
        if self._builder is None:
            known = {str(b["name"]) for b in await self._bridge.builds()}
            missing = [n for n in names if n not in known]
            if missing:
                raise GoalRejectedError(f"no build named {', '.join(missing)} (cannot design here)")
            return 0
        known = await self._builder.known()
        brief = f"{proposal.title}: {proposal.reason}".strip(": ")
        for name in names:
            if name not in known:
                await self._builder.design(name, brief)
        totals = {str(b["name"]): int(b.get("total", 0)) for b in await self._bridge.builds()}
        return sum(totals.get(name, 0) for name in names)

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
