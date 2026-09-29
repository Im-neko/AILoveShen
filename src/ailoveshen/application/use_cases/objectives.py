"""大目標の管理: 世界から判定し、プランの上限の中で編集し、保存し、伝える。"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Optional

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.use_cases.builds import BuildDesigner
from ailoveshen.application.use_cases.checks import CheckWriter
from ailoveshen.application.use_cases.facility_design import FacilityDesigner
from ailoveshen.application.use_cases.goal_vocabulary import (
    ObjectiveProposal,
    PlanChange,
    PlanOp,
)
from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.events import (
    DomainEvent,
    JobDoneEvent,
    ObjectiveAddedEvent,
    ObjectiveCompletedEvent,
    ObjectiveDroppedEvent,
    TownCompletedEvent,
)
from ailoveshen.domain.exceptions import AILoveShenError, GoalRejectedError
from ailoveshen.domain.value_objects import (
    BuildDesign,
    GoalPredicate,
    GoalSpec,
    Objective,
    Job,
)

MAX_STAGE_DESIGN_TRIES = 2


class ObjectiveKeeper:
    """
    プランを作ったあと、大目標のリストを変える唯一の経路。

    2 つの呼び出し元が並行して編集する: 目標の決定（小目標の切れ目での配信者自身の
    編集）と、チャットの返答（視聴者の頼みを受けたとき）。編集は写しの上で試して
    から、await を挟まずにプランへ反映する。こうして互いに上書きしない。プランの
    オブジェクトは差し替えない（ゴールボードとナレーターが持っている）。

    大目標の条件は、足す前にブリッジが確かめる。世界から判定できないことは約束
    しない。変更はすべて保存し、発行する（ObjectiveAdded/Completed/DroppedEvent）。
    """

    def __init__(
        self,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        store: IMissionStore,
        builder: BuildDesigner | None = None,
        checker: CheckWriter | None = None,
        facilities: FacilityDesigner | None = None,
    ) -> None:
        """
        依存を受け取って初期化する（依存性の注入）。

        Args:
            bridge: Minecraft ブリッジのアダプター（条件を判定する）
            event_publisher: ドメインイベントの発行器
            store: 再起動をまたいでプランを保つ
            builder: 条件にまだない建物（built(name)）があるとき、先に設計させる
                （docs/design/25_builds.md）。None なら、ない建物は判定できずに断られる
            checker: 条件にまだない判定（custom(name)）があるとき、先に判定を書かせる
                （docs/design/42_custom_conditions.md）。None なら、ない判定は断られる
            facilities: 条件にまだない施設（facility(name)）があるとき、先に設計させる
                （docs/design/47 §7）。None なら、ない施設は判定できずに断られる
        """
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._store = store
        self._builder = builder
        self._checker = checker
        self._facilities = facilities
        self._design_failures: dict[str, int] = {}  # 街の段階の建物の設計に失敗した回数

    async def judge(self, plan: ObjectivePlan) -> None:
        """
        未完了の大目標を世界から判定する。条件がすべて満たされたものは完了にする。

        ブリッジが条件を拒否した大目標（そのままでは足せなかったはずだが、世界の
        ルールが変わったのかもしれない）は、プレイを止めずに理由をつけて断念する。
        街の段階と準備は断念しない: ログに残し、条件が直るのを待つ。

        次に街を進める: 取り組んでいる段階のうち今できること（まだ満たしていない
        条件）を、リストの一番上の大目標にする。場所が決まって準備（調査、引っ越し）が
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
                    logger.error(f"街の大目標 {goal.describe()} を判定できない: {e}")
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
                logger.info(f"大目標を達成した: {done.describe()}")
                events.append(
                    ObjectiveCompletedEvent(
                        title=done.title,
                        requested_by=done.requested_by or "",
                        suggested_by=done.suggested_by or "",
                    )
                )
        events.extend(await self._judge_steps(plan))
        await self._design_stage_builds(plan)
        events.extend(self._next_stage(plan))
        if plan.town_complete and not was_complete:
            assert plan.town is not None
            logger.info(f"街が完成した: {plan.town.text}")
            events.append(TownCompletedEvent(text=plan.town.text))
        self._store.save(plan)
        await self._publish(events)

    async def _judge_steps(self, plan: ObjectivePlan) -> list[DomainEvent]:
        """
        一番上の大目標の、まだ済んでいない中目標を判定する。満たしたものは済みにして、そのままにする
        （あとで材料を使って条件が崩れても戻さない。docs/design/50 §4）。進み具合も保つ（停滞の目安）。
        判定できなければ何もしない。済みにしたものは JobDoneEvent にして返す。
        """
        current = plan.current
        if current is None or not current.open_jobs:
            return []
        jobs = current.open_jobs
        try:
            statuses = list(await self._bridge.check([x for s in jobs for x in s.specs]))
        except AILoveShenError as e:
            logger.debug(f"中目標を判定できなかった: {e}")
            return []
        judged: list[tuple[str, bool, tuple[str, ...]]] = []
        for job in jobs:
            mine, statuses = statuses[: len(job.specs)], statuses[len(job.specs) :]
            judged.append(
                (
                    job.id,
                    all(c.met for c in mine),
                    tuple(line for c in mine for line in c.lines),
                )
            )
        events: list[DomainEvent] = []
        for job in plan.judge_jobs(current.id, judged):
            logger.info(f"中目標が済んだ: {job.describe()}（大目標 {current.id}）")
            events.append(
                JobDoneEvent(
                    objective=current.title,
                    job=job.describe(),
                    reason=job.reason,
                    requested_by=job.requested_by or "",
                    suggested_by=job.suggested_by or "",
                )
            )
        return events

    def _next_stage(self, plan: ObjectivePlan) -> list[DomainEvent]:
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
        logger.info(f"街の段階 {plan.town_stage + 1} を大目標 {goal.id} にした: {goal.describe()}")
        return [_added(plan, goal)]

    async def _design_stage_builds(self, plan: ObjectivePlan) -> None:
        """
        今の街の段階の条件に、まだない建物（built(name)）があれば、大目標にする前に設計させる。
        失敗した名前は MAX_STAGE_DESIGN_TRIES 回まで（毎回の切れ目で呼び続けない）。
        """
        stage = plan.current_stage
        if stage is None or plan.site is None or plan.preparing_town:
            return
        await self._write_stage_checks(plan)
        await self._design_stage_facilities(plan)
        if self._builder is None:
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

    async def _design_stage_facilities(self, plan: ObjectivePlan) -> None:
        """今の街の段階の条件に、まだない施設（facility(name)）があれば設計させる（失敗は建物と同じく数える）。"""
        stage = plan.current_stage
        assert stage is not None
        names = [
            c.name
            for c in plan.stage_remaining
            if c.predicate == GoalPredicate.FACILITY
            and c.name is not None
            and self._design_failures.get(c.name, 0) < MAX_STAGE_DESIGN_TRIES
        ]
        if not names or self._facilities is None:
            return
        known = await self._facilities.known()
        for name in names:
            if name in known:
                continue
            try:
                await self._facilities.design(name, f"{stage.title}: {stage.why}")
            except GoalRejectedError as e:
                self._design_failures[name] = self._design_failures.get(name, 0) + 1
                logger.error(f"街の段階の施設 {name} を設計できない: {e}")

    async def _write_stage_checks(self, plan: ObjectivePlan) -> None:
        """今の街の段階の条件に、まだない判定（custom(name)）があれば書かせる（失敗は建物と同じく数える）。"""
        stage = plan.current_stage
        assert stage is not None
        pending = [
            c
            for c in plan.stage_remaining
            if c.predicate == GoalPredicate.CUSTOM
            and c.name is not None
            and self._design_failures.get(c.name, 0) < MAX_STAGE_DESIGN_TRIES
        ]
        if not pending or self._checker is None:
            return
        known = await self._checker.known()
        for c in pending:
            if c.name in known:
                continue
            try:
                await self._checker.write(c.name, f"{stage.title}: {stage.why}")
            except GoalRejectedError as e:
                self._design_failures[c.name] = self._design_failures.get(c.name, 0) + 1
                logger.error(f"街の段階の判定 {c.name} を書けない: {e}")

    async def add_town_goal(
        self,
        plan: ObjectivePlan,
        title: str,
        conditions: tuple[GoalSpec, ...],
        reason: str,
        position: Optional[int] = None,
    ) -> Optional[Objective]:
        """
        街の準備の大目標（候補地の調査、引っ越し）を足す。条件はコードが決めたもの。
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
        logger.info(f"街の準備を大目標 {goal.id} にした: {goal.describe()}")
        self._store.save(plan)
        await self._publish([_added(plan, goal)])
        return goal

    async def check_new(self, changes: Sequence[PlanChange]) -> None:
        """
        変更が足す大目標の条件を、ブリッジに確かめさせる。

        Raises:
            GoalRejectedError: 条件を判定できないとき（メッセージに理由がある）
        """
        for change in changes:
            if change.proposal is not None:
                await self._design_builds(change.proposal)
                await self._design_facilities(change.proposal.conditions, _brief(change.proposal))
                await self._write_checks(change.proposal.conditions, _brief(change.proposal))
                await self._bridge.check(change.proposal.conditions)

    def rehearse(self, plan: ObjectivePlan, changes: Sequence[PlanChange]) -> ObjectivePlan:
        """
        変更を反映したらどうなるかのプラン（プラン自体には触れない）。

        Raises:
            ValueError: 変更が上限を破るか、未完了の大目標を指していないとき
        """
        preview = copy.deepcopy(plan)
        _apply(preview, changes)
        return preview

    async def commit(self, plan: ObjectivePlan, changes: Sequence[PlanChange]) -> None:
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
        self,
        plan: ObjectivePlan,
        proposal: ObjectiveProposal,
        requested_by: str,
        advice: bool = False,
    ) -> Objective:
        """
        視聴者の頼みを大目標として足す（今取り組んでいるものの後ろに）。`proposal.now`
        （配信者が今やると判断した）なら先頭に。呼び出し側が今の小目標を区切る。

        `advice`: 視聴者の助言を配信者が納得して採った。配信者自身の大目標にし（提案者は
        `suggested_by`）、視聴者の頼みの上限と予算に数えない（2026-09-28: 助言をくれる人が
        1 人 1 つの上限に当たり、もっともな助言を「後で」と流していた）。

        Raises:
            GoalRejectedError: 条件を判定できないとき
            ValueError: 上限を破るとき（例: その視聴者はもう 1 つ持っている）
        """
        what = "の助言" if advice else "の頼み"
        if proposal.job:
            return await self._accept_job(plan, proposal, requested_by, advice)
        same = plan.same_as(proposal.conditions)
        if same is not None:
            # もう同じ完了条件の大目標がある: 足さない（2026-09-28: 同じ整地が 2 つ並んだ）。今やるなら
            # それを先頭に（呼び出し側が今の小目標を区切る）
            if proposal.now:
                plan.move(same.id, 0)
                self._store.save(plan)
            where = "先頭に移した" if proposal.now else "そのまま"
            logger.info(f"{requested_by} {what}は大目標 {same.id} と同じ完了条件: 足さずに{where}")
            return same
        viewer = None if advice else requested_by
        plan.check_room(viewer)  # 設計（建物・施設の登録）の前に: 上限で断るなら何も残さない
        blocks = await self._design_builds(
            proposal, BuildDesign.MAX_BLOCKS if advice else BuildDesign.VIEWER_MAX_BLOCKS
        )
        await self._design_facilities(proposal.conditions, _brief(proposal))
        await self._write_checks(proposal.conditions, _brief(proposal))
        await self._bridge.check(proposal.conditions)
        goal = plan.add(
            title=proposal.title,
            conditions=proposal.conditions,
            reason=proposal.reason,
            requested_by=viewer,
            suggested_by=requested_by if advice else None,
            position=0 if proposal.now else proposal.position,
            # 建物は大きさに合わせて予算を広げる（1 ブロック 2 ステップと、材料集め）
            budget=max(plan.viewer_budget, 2 * blocks + 40) if blocks and not advice else None,
            now=proposal.now,
        )
        logger.info(f"{requested_by} {what}を大目標 {goal.id} にした: {goal.describe()}")
        self._store.save(plan)
        await self._publish([_added(plan, goal)])
        return goal

    async def _accept_job(
        self, plan: ObjectivePlan, proposal: ObjectiveProposal, requested_by: str, advice: bool
    ) -> Objective:
        """
        頼みを、今の大目標の中目標として足す（docs/design/50 §5）。条件の先頭が中目標の条件で、
        残りは一緒に追う。今の中目標の後ろに入る（`proposal.now` なら先頭。呼び出し側が小目標を区切る）。
        今の大目標を返す。

        Raises:
            GoalRejectedError: 条件を判定できないとき
            ValueError: 上限を破るか、中目標の形が正しくないとき、足す大目標がないとき
        """
        current = plan.current
        if current is None:
            raise ValueError("there is no objective to add the job to")
        viewer = None if advice else requested_by
        job = Job(
            proposal.conditions[0],
            proposal.reason,
            together=proposal.conditions[1:],
            requested_by=viewer,
            suggested_by=requested_by if advice else None,
        )
        plan.check_room(viewer, job=True)  # 設計の前に: 上限で断るなら何も残さない
        await self._design_builds(
            proposal, BuildDesign.MAX_BLOCKS if advice else BuildDesign.VIEWER_MAX_BLOCKS
        )
        await self._design_facilities(proposal.conditions, _brief(proposal))
        await self._write_checks(proposal.conditions, _brief(proposal))
        await self._bridge.check(proposal.conditions)
        # 今の中目標のすぐ後ろで、先に頼まれた中目標の後ろ（材料はそれを使う中目標より前に要る）。now なら先頭
        queued = current.open_jobs
        at = 1
        while at < len(queued) and (queued[at].requested_by or queued[at].suggested_by):
            at += 1
        position = 0 if proposal.now else at
        added = plan.add_job(current.id, job, position=position, now=proposal.now)
        logger.info(
            f"{requested_by} {'の助言' if advice else 'の頼み'}を大目標 {current.id} の中目標 "
            f"{added.id} にした: {added.describe()}"
        )
        self._store.save(plan)
        goal = plan.get(current.id)
        assert goal is not None
        return goal

    async def withdraw(self, plan: ObjectivePlan, requested_by: str) -> Objective | None:
        """
        視聴者が自分の頼みや助言を取り下げた（「もういいよ」「やっぱりいいや」）: その人の大目標
        （中目標も）をやめる。頼みが先で、なければ採った助言。なければ None。やめられるのは本人の
        ものだけ（ほかの人のものや配信者自身の大目標は変えない）。言ったこと（「要らないんだね」）と
        リストを合わせるので、助言で採ったものも本人が取り下げれば外す。
        """
        for advice in (False, True):
            mine = next(
                (
                    g
                    for g in plan.pending
                    if (g.suggested_by if advice else g.requested_by) == requested_by
                ),
                None,
            )
            if mine is not None:
                dropped = plan.drop(mine.id, f"{requested_by} withdrew the request")
                logger.info(f"視聴者の頼みを取り下げた: {dropped.describe()}（{requested_by}）")
                self._store.save(plan)
                await self._publish([_dropped(dropped)])
                return dropped
            found = plan.viewer_job(requested_by, advice=advice)
            if found is not None:
                goal, job = found
                reason = f"{requested_by} withdrew the request"
                plan.drop_job(goal.id, job.id, reason)
                logger.info(f"視聴者の頼みの中目標を取り下げた: {job.describe()}（{requested_by}）")
                self._store.save(plan)
                await self._publish([_dropped_job(job, reason)])
                return goal
        return None

    async def _design_builds(
        self, proposal: ObjectiveProposal, max_blocks: int = BuildDesign.MAX_BLOCKS
    ) -> int:
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
        for name in names:
            if name not in known:
                await self._builder.design(name, _brief(proposal), max_blocks)
        totals = {str(b["name"]): int(b.get("total", 0)) for b in await self._bridge.builds()}
        return sum(totals.get(name, 0) for name in names)

    async def _design_facilities(self, conditions: Sequence[GoalSpec], brief: str) -> None:
        """
        条件の facility(name) のうち、まだない施設を設計させて登録する（docs/design/47 §7）。

        Raises:
            GoalRejectedError: 設計できなかったとき（FacilityNotDesignedError）
        """
        names = [
            c.name
            for c in conditions
            if c.predicate == GoalPredicate.FACILITY and c.name is not None
        ]
        if not names:
            return
        if self._facilities is None:
            known = {str(f["name"]) for f in await self._bridge.facilities()}
            missing = [n for n in names if n not in known]
            if missing:
                raise GoalRejectedError(
                    f"no facility named {', '.join(missing)} (cannot design here)"
                )
            return
        known = await self._facilities.known()
        for name in names:
            if name not in known:
                await self._facilities.design(name, brief)

    async def _write_checks(self, conditions: Sequence[GoalSpec], brief: str) -> None:
        """
        条件の custom(name) のうち、まだない判定を書かせて登録する。

        Raises:
            GoalRejectedError: 書けなかったとき
        """
        names = [
            c.name for c in conditions if c.predicate == GoalPredicate.CUSTOM and c.name is not None
        ]
        if not names:
            return
        if self._checker is None:
            known = {str(c["name"]) for c in await self._bridge.conditions()}
            missing = [n for n in names if n not in known]
            if missing:
                raise GoalRejectedError(f"no check named {', '.join(missing)} (cannot write here)")
            return
        known = await self._checker.known()
        for name in names:
            if name not in known:
                await self._checker.write(name, brief)

    async def set_jobs(
        self, plan: ObjectivePlan, objective_id: str, jobs: tuple[Job, ...]
    ) -> None:
        """大目標の中目標（Gemini が書いた小目標の並び）を置き換えて保存する（docs/design/26）。"""
        goal = plan.set_jobs(objective_id, jobs)
        logger.info(f"大目標 {goal.id} の中目標: " + " → ".join(s.spec.describe() for s in jobs))
        self._store.save(plan)

    async def step_counted(self, plan: ObjectivePlan, goal: Objective | None) -> None:
        """
        ステップを数えたあとにプランを保存する。予算を超えて断念した大目標（視聴者の中目標も）が
        あれば伝える。
        """
        self._store.save(plan)
        events: list[DomainEvent] = []
        for job in plan.take_dropped_jobs():
            logger.info(f"視聴者の中目標を断念した: {job.describe()}（{job.ended_because}）")
            events.append(_dropped_job(job, job.ended_because))
        if goal is not None:
            logger.info(f"大目標を断念した: {goal.describe()}（{goal.ended_because}）")
            events.append(_dropped(goal))
        await self._publish(events)

    async def _publish(self, events: list[DomainEvent]) -> None:
        for event in events:
            await self._event_publisher.publish(event)


def _apply(plan: ObjectivePlan, changes: Sequence[PlanChange]) -> list[DomainEvent]:
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
            plan.move(change.objective_id, change.position)
        elif change.job_id:
            job = plan.drop_job(change.objective_id, change.job_id, change.reason)
            events.append(_dropped_job(job, change.reason))
        else:
            events.append(_dropped(plan.drop(change.objective_id, change.reason)))
    return events


def _brief(proposal: ObjectiveProposal) -> str:
    """設計や判定を頼むときの、何のための大目標か（題名と理由）。"""
    return f"{proposal.title}: {proposal.reason}".strip(": ")


def _added(plan: ObjectivePlan, goal: Objective) -> ObjectiveAddedEvent:
    position = next(i for i, g in enumerate(plan.pending) if g.id == goal.id) + 1
    return ObjectiveAddedEvent(
        title=goal.title,
        reason=goal.reason,
        requested_by=goal.requested_by or "",
        position=position,
        suggested_by=goal.suggested_by or "",
    )


def _dropped_job(job: Job, reason: str) -> ObjectiveDroppedEvent:
    """視聴者の中目標の断念も、大目標の断念と同じく配信で言う（黙って約束を捨てない）。"""
    return ObjectiveDroppedEvent(
        title=job.describe(),
        reason=reason,
        requested_by=job.requested_by or "",
        suggested_by=job.suggested_by or "",
    )


def _dropped(goal: Objective) -> ObjectiveDroppedEvent:
    return ObjectiveDroppedEvent(
        title=goal.title,
        reason=goal.ended_because,
        requested_by=goal.requested_by or "",
        suggested_by=goal.suggested_by or "",
    )
