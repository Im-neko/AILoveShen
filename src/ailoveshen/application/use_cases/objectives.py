"""大目標の管理: 世界から判定し、プランの上限の中で編集し、保存し、伝える。"""

from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import replace
from collections.abc import Awaitable, Callable, Sequence
from typing import Optional

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore
from ailoveshen.application.use_cases.builds import BuildDesigner
from ailoveshen.application.use_cases.checks import CheckWriter
from ailoveshen.application.use_cases.facility_design import FacilityDesigner
from ailoveshen.application.use_cases.facility_satisfy import SATISFY_NEAR_HOME_M, check_satisfy
from ailoveshen.application.use_cases.plan_lint import (
    fake_infrastructure,
    unregistered_builds,
    unregistered_facilities,
)
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
    ObjectivesResetEvent,
    TownCompletedEvent,
)
from ailoveshen.domain.exceptions import AILoveShenError, GoalRejectedError
from ailoveshen.domain.value_objects import (
    BuildDesign,
    GoalPredicate,
    GoalSpec,
    Objective,
    Job,
    PlaybookStep,
)

MAX_STAGE_DESIGN_TRIES = 2
# 設計に失敗した名前を、世界が変わったかもしれないとして試し直すまでの秒数（失敗の数えは再起動をまたいで残す。設計書 195）
DESIGN_RETRY_SECONDS = 3600.0
PLACED_PROBE = 8  # 置いた数を調べるとき、今の数に足して試す数（1 回の判定にまとめて送る）
# 定石（docs/design/74）: 施設の段は、家のそばで設計する（区画が読み込まれている範囲 m）。待つ間に調べ直す間隔と、
# 設計に失敗しても試す回数
PLAYBOOK_NEAR_HOME_M = SATISFY_NEAR_HOME_M
PLAYBOOK_RETRY_SECONDS = 60.0
PLAYBOOK_TRIES = 3
PLAYBOOK_PARALLEL = 2  # 育成を待つ間に、同時に進める定石の段の数


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
        self._playbook_failures: dict[str, int] = {}  # 定石の段を足せなかった回数
        self._playbook_at = -PLAYBOOK_RETRY_SECONDS  # 施設の段の置き場所を最後に調べた時刻（time.monotonic）
        self._playbook_site = False  # そのときの答え
        # 返事のあとに裏で設計している頼み（完了条件 → 設計の仕事。docs/design/107）
        self._designing: dict[tuple[GoalSpec, ...], asyncio.Task[None]] = {}
        # 裏の設計が断られたとき、頼んだ視聴者・題名・理由を受ける（配信が一言話す）。None: 話さない
        self.on_design_failed: Optional[Callable[[str, str, str], Awaitable[None]]] = None

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
        await self._drop_unjudgeable(plan)
        await self._reconcile(plan)
        await self._design_stage_builds(plan)
        events.extend(await self._ensure_playbook(plan))
        events.extend(self._next_stage(plan))
        if plan.town_complete and not was_complete:
            assert plan.town is not None
            logger.info(f"街が完成した: {plan.town.text}")
            events.append(TownCompletedEvent(text=plan.town.text))
        self._store.save(plan)
        await self._publish(events)

    async def _drop_unjudgeable(self, plan: ObjectivePlan) -> None:
        """
        存在しない名前の建物 `built(name)`・施設 `facility(name)` が条件の中目標を外す（`plan_lint` が新しい
        計画では止める。書いてあったものは、いつまでも判定できず、小目標にもできない）。視聴者の頼み・助言の
        中目標は外さない。中目標が尽きた大目標は、次の切れ目で計画を書き直す。
        """
        suspects = [
            (g, j)
            for g in plan.pending
            for j in g.open_jobs
            if not (j.requested_by or j.suggested_by)
            and (unregistered_builds(j.specs, g, ()) or unregistered_facilities(j.specs, g, ()))
        ]
        if not suspects:
            return
        try:
            builds = {str(b.get("name")) for b in await self._bridge.builds()}
            facilities = {str(f.get("name")) for f in await self._bridge.facilities()}
        except AILoveShenError as e:
            logger.debug(f"建物・施設の一覧を読めなかった: {e}")
            return
        for goal, job in suspects:
            if not (
                unregistered_builds(job.specs, goal, builds)
                or unregistered_facilities(job.specs, goal, facilities)
            ):
                continue
            logger.warning(
                f"中目標 {job.describe()} を外す（大目標 {goal.id}）: その名前の建物・施設はなく、判定できない"
            )
            plan.drop_job(goal.id, job.id, "it names a build or facility that does not exist")

    async def _reconcile(self, plan: ObjectivePlan) -> None:
        """
        すべての大目標の、すべての中目標（済みも）とその計画の小目標を、世界と照合して「今満たして
        いるか」を残す（docs/design/50 §19）。済み（ラッチ）は変えない。今までは一番上の大目標の
        まだ済んでいない中目標しか判定せず、ほかは印がないまま「待ち」と表示していた。
        判定できない条件は満たしていない扱い。ブリッジにつながらなければ、前の照合のまま。
        """
        specs: dict[str, GoalSpec] = {}
        for goal in plan.pending:
            for job in goal.jobs:
                for spec in job.all_specs():
                    specs.setdefault(spec.describe(), spec)
        if not specs:
            return
        try:
            met = await self._met_now(list(specs.values()))
        except AILoveShenError as e:
            logger.debug(f"照合できなかった: {e}")
            return
        for goal in plan.pending:
            plan.reconcile(
                goal.id,
                {
                    job.id: [s.describe() for s in job.all_specs() if met.get(s.describe())]
                    for job in goal.jobs
                },
            )

    async def _met_now(self, specs: list[GoalSpec]) -> dict[str, bool]:
        """条件を 1 回で判定する。1 つでも拒否されたら、半分ずつに分けて、拒否された条件だけ満たしていない扱いにする。"""
        try:
            statuses = await self._bridge.check(specs)
        except GoalRejectedError:
            if len(specs) == 1:
                return {specs[0].describe(): False}
            mid = len(specs) // 2
            return {**await self._met_now(specs[:mid]), **await self._met_now(specs[mid:])}
        return {s.describe(): st.met for s, st in zip(specs, statuses)}

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
        # 定石の段が先（街の段階は、定石が土台の上に載る。docs/design/74）。段階の条件は家を基準に判定する:
        # 引っ越す前の家で満たしても意味がない
        if plan.site is None or plan.preparing_town or plan.playbook_goal() is not None:
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
            and not self._design_blocked(plan, c.name)
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
                self._design_failed(plan, name, e)
                logger.error(f"街の段階の建物 {name} を設計できない: {e}")

    def _design_blocked(self, plan: ObjectivePlan, name: str) -> bool:
        """設計に MAX_STAGE_DESIGN_TRIES 回失敗した名前か（一定時間たてば試し直す）。"""
        count, at = plan.design_failed.get(name, (0, 0.0))
        if count < MAX_STAGE_DESIGN_TRIES:
            return False
        if time.time() - at >= DESIGN_RETRY_SECONDS:
            plan.forget_design_failure(name)
            return False
        return True

    def _design_failed(self, plan: ObjectivePlan, name: str, error: GoalRejectedError) -> None:
        """設計の失敗を数えて保存する（再起動で数え直さない）。"""
        plan.note_design_failure(name, time.time())
        self._store.save(plan)

    async def _design_stage_facilities(self, plan: ObjectivePlan) -> None:
        """今の街の段階の条件に、まだない施設（facility(name)）があれば設計させる（失敗は建物と同じく数える）。"""
        stage = plan.current_stage
        assert stage is not None
        names = [
            c.name
            for c in plan.stage_remaining
            if c.predicate == GoalPredicate.FACILITY
            and c.name is not None
            and not self._design_blocked(plan, c.name)
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
                if getattr(e, "world", False):  # 区画がまだ読み込まれていない: 近づけば直る。失敗に数えない
                    logger.warning(f"街の段階の施設 {name} はまだ設計できない: {e}")
                    continue
                self._design_failed(plan, name, e)
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
            and not self._design_blocked(plan, c.name)
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
                self._design_failed(plan, c.name, e)
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

    async def _beyond_what_is_placed(self, proposal: ObjectiveProposal) -> ObjectiveProposal:
        """
        頼まれた時点でもう置いてある物は、「もう 1 つ」の頼みとして、今の数より 1 つ多い数に直す。

        「チェストの増設」は placed(chest, home) と書かれ、家にチェストがあるので、足した途端に済みになった
        （数は「その数以上ある」の条件で、増やす数ではない。2026-10-08, user: 「チェストの増設を依頼しても既に
        チェストがあるからかすぐに目標達成扱いになってしまいます」）。置いてある物を置けという頼みに、ほかの
        読みはない。判定できなければ、そのまま。
        """
        placed = [c for c in proposal.conditions if c.predicate == GoalPredicate.PLACED]
        if not placed:
            return proposal
        try:
            met = [st.met for st in await self._bridge.check(placed)]
            fixed: dict[GoalSpec, GoalSpec] = {}
            for spec, is_met in zip(placed, met):
                if not is_met:
                    continue
                base = spec.count or 1
                tries = [replace(spec, count=n) for n in range(base + 1, base + 1 + PLACED_PROBE)]
                for bigger, ok in zip(tries, [st.met for st in await self._bridge.check(tries)]):
                    if not ok:
                        fixed[spec] = bigger
                        break
        except AILoveShenError as e:
            logger.debug(f"置いた数を判定できなかった: {e}")
            return proposal
        if not fixed:
            return proposal
        for old, new in fixed.items():
            logger.info(f"もう置いてある {old.describe()} は、もう 1 つの頼みとして {new.describe()} にする")
        return replace(proposal, conditions=tuple(fixed.get(c, c) for c in proposal.conditions))

    @staticmethod
    def _check_infrastructure(proposal: ObjectiveProposal) -> None:
        """畑・植林場の題名で、それ自体が条件にない提案は、直し方を添えて断る。"""
        why = "" if proposal.job else fake_infrastructure(proposal.title, proposal.conditions)
        if why:
            raise GoalRejectedError(why)

    async def _ensure_playbook(self, plan: ObjectivePlan) -> list[DomainEvent]:
        """
        定石（docs/design/74）の今の段を、リストの先頭の大目標にする（コードが決める）。

        段は設定の順（寝床 → 食料 → 建材 → 資材…）で、今の段は、まだ済んでいない最初のもの。
        済みは、大目標の完了（`ObjectivePlan.complete`）か、足す前にもう条件が満たされていたことで決まり、
        一度済んだら戻さない（持ち物が減っても畑を作り直さない）。

        育成中（`ready` が満たされている: 畑ができて作物が育つのを待つだけ）の段があれば、次の段を同時に
        進めてよい。ただし同時に進めるのは 2 段まで: 3 段目は、2 つとも済んでから（非同期に始めても、
        待つのは必ず完成まで）。手を動かす段が先頭、育成中の段が後ろ。

        施設の段は、設計できる場所で足す: 家が建っていて家から PLAYBOOK_NEAR_HOME_M 以内（区画が読み込まれて
        いる）、街の引っ越し中でない。そうでない間は、その段で待つ（次の段へ飛ばさない）。設計や判定に
        PLAYBOOK_TRIES 回失敗した段は飛ばす（それまでは、次の機会にまたその段から。ログ）。
        """
        events: list[DomainEvent] = []
        growing: dict[str, bool] = {}
        for step in plan.playbook:
            if step.keep and step.name in plan.playbook_done and await self._playbook_lost(step):
                plan.unmark_playbook(step.name)
                logger.info(f"定石の段 {step.name} は持っていた物を失った: また今の段にする")
        for step in plan.playbook:
            if step.name in plan.playbook_done:
                continue
            if self._playbook_failures.get(step.name, 0) >= PLAYBOOK_TRIES:
                continue
            opened = await self._playbook_open(plan, step, events)
            if opened == "done":
                continue
            if opened == "wait":
                break
            growing[step.name] = await self._playbook_growing(step)
            if not growing[step.name] or len(growing) >= PLAYBOOK_PARALLEL:
                break
        plan.order_playbook([n for n, g in growing.items() if not g] + [n for n, g in growing.items() if g])
        for step in plan.playbook:
            if step.name in growing:
                self._absorb(plan, step)
        return events

    @staticmethod
    def _absorb(plan: ObjectivePlan, step: PlaybookStep) -> None:
        """
        定石の段に含まれる、配信者自身の大目標（条件がすべてその段の条件の一部）を閉じる。同じ施設を作る
        旧い大目標（設計書 72 の `facility(wheat_farm_1)` など）が枠を取って、リストがいっぱいになっていた
        （2026-10-01）。視聴者の頼み、採った助言、街の段階と準備は閉じない。
        """
        mine = {c.describe() for c in step.conditions}
        for goal in plan.pending:
            if (
                goal.playbook is None
                and goal.stage is None
                and not goal.prepares_town
                and goal.requested_by is None
                and goal.suggested_by is None
                and {c.describe() for c in goal.conditions} <= mine
            ):
                plan.drop(goal.id, f"the playbook step {step.name} covers it")
                logger.info(f"大目標 {goal.id} {goal.title} は定石の段 {step.name} に含まれる: 閉じた")

    async def _playbook_lost(self, step: PlaybookStep) -> bool:
        """済みにした `keep` の段の条件が、今は満たされていないか（調べられなければ失っていない）。"""
        try:
            return not all(s.met for s in await self._bridge.check(step.conditions))
        except AILoveShenError as e:
            logger.warning(f"定石の段 {step.name} の持ち物を調べられない: {e}")
            return False

    async def _playbook_growing(self, step: PlaybookStep) -> bool:
        """設営ができて、育つのを待つだけの段か（`ready` が満たされている）。"""
        if not step.ready:
            return False
        try:
            return all(s.met for s in await self._bridge.check(step.ready))
        except AILoveShenError as e:
            logger.warning(f"定石の段 {step.name} の育成を調べられない: {e}")
            return False

    async def _playbook_open(
        self, plan: ObjectivePlan, step: PlaybookStep, events: list[DomainEvent]
    ) -> str:
        """
        定石の段を大目標にする。返すのは、"open"（大目標がある）、"done"（もう済んでいる）、
        "wait"（今は足せない）。
        """
        if any(g.playbook == step.name for g in plan.pending):
            return "open"  # 済んだかは judge が判定する
        brief = f"{step.title}: {step.reason}".strip(": ")
        try:
            if any(c.predicate == GoalPredicate.FACILITY for c in step.conditions):
                if not await self._playbook_site_ready(plan):
                    return "wait"
                await self._design_facilities(step.conditions, brief)
            statuses = await self._bridge.check(step.conditions)
        except GoalRejectedError as e:
            failed = self._playbook_failures.get(step.name, 0) + 1
            self._playbook_failures[step.name] = failed
            logger.warning(f"定石の段 {step.name} を足せない（{failed} 回目）: {e}")
            return "wait" if failed < PLAYBOOK_TRIES else "done"  # 飛ばす（次の段へ）
        except AILoveShenError as e:
            logger.warning(f"定石の段 {step.name} を調べられない: {e}")
            return "wait"
        if all(s.met for s in statuses):
            plan.mark_playbook(step.name)
            logger.info(f"定石の段 {step.name} はもう済んでいる")
            return "done"
        self._absorb(plan, step)  # リストがいっぱいでも、含まれる旧い大目標を閉じてから足す
        try:
            goal = plan.add(
                title=step.title,
                conditions=step.conditions,
                reason=step.reason,
                position=0,
                playbook=step.name,
            )
        except ValueError as e:
            logger.warning(f"定石の段 {step.name} はリストに空きが出るのを待つ: {e}")
            return "wait"
        logger.info(f"定石の段 {step.name} を大目標 {goal.id} にした: {goal.describe()}")
        events.append(_added(plan, goal))
        return "open"

    async def _playbook_site_ready(self, plan: ObjectivePlan) -> bool:
        """
        施設を設計できる場所か: 家が建っていて近く、街の引っ越し中でない。答えは PLAYBOOK_RETRY_SECONDS の間
        使い回す（1 回の `judge` で施設の段が 2 つあっても、ブリッジに 1 回しか聞かない）。
        """
        if self._facilities is None or plan.preparing_town:
            return False
        now = time.monotonic()
        if now - self._playbook_at < PLAYBOOK_RETRY_SECONDS:
            return self._playbook_site
        self._playbook_at = now
        obs = await self._bridge.observe()
        near = obs.home_distance_m
        self._playbook_site = bool(
            obs.has_home and obs.house_complete and near is not None and near <= PLAYBOOK_NEAR_HOME_M
        )
        return self._playbook_site

    async def check_new(self, plan: ObjectivePlan, changes: Sequence[PlanChange]) -> None:
        """
        変更が足す大目標の条件と、済みにする根拠（satisfy の施設）を、ブリッジに確かめさせる。

        Raises:
            GoalRejectedError: 条件を判定できないとき（メッセージに理由がある）
        """
        for change in changes:
            if change.op == PlanOp.SATISFY:
                await self._check_satisfy(plan, change)
            if change.proposal is not None:
                self._check_infrastructure(change.proposal)
                await self._design_builds(change.proposal)
                await self._design_facilities(change.proposal.conditions, _brief(change.proposal))
                await self._write_checks(change.proposal.conditions, _brief(change.proposal))
                await self._bridge.check(change.proposal.conditions)

    async def _check_satisfy(self, plan: ObjectivePlan, change: PlanChange) -> None:
        """
        satisfy の根拠を、ブリッジの記憶（施設の一覧）と世界の判定に突き合わせる。モデルの言うことだけでは
        済みにしない（畑が満杯と誤診して 3 つ目の畑を作らせた前例がある: 2026-10-09）。

        Raises:
            GoalRejectedError: 大目標がないか、施設の条件がないか、根拠が合わないとき
        """
        goal = plan.get(change.objective_id)
        if goal is None:
            raise GoalRejectedError(f"no pending objective {change.objective_id} to satisfy")
        if not any(c.predicate == GoalPredicate.FACILITY for c in goal.conditions):
            raise GoalRejectedError(
                f"{goal.id} has no facility condition: satisfy is only for those (the world judges the rest)"
            )
        statuses = await self._bridge.check(goal.conditions)
        check_satisfy(statuses, change.by, await self._bridge.facilities())

    def rehearse(self, plan: ObjectivePlan, changes: Sequence[PlanChange]) -> ObjectivePlan:
        """
        変更を反映したらどうなるかのプラン（プラン自体には触れない）。

        Raises:
            ValueError: 変更が上限を破るか、未完了の大目標を指していないとき
        """
        preview = copy.deepcopy(plan)
        _apply(preview, changes)
        return preview

    def without_refused(
        self, plan: ObjectivePlan, changes: Sequence[PlanChange]
    ) -> tuple[PlanChange, ...]:
        """
        プランが断る並べ替えと断念を除いた変更（プラン自体には触れない）。

        定石の大目標は動かせず、やめられず、ほかのものはその前に出せない（設計書 74）。モデルが
        同じ `move` を書き続けると、決定ごと差し戻され続けて何も進まない（2026-10-03: 夜の野外で
        4 分以上）。最後の試行で、この種の編集だけ捨てて、決定の残りを通すのに使う。足す変更は
        そのまま残す（断られるなら、いつもどおり差し戻す）。
        """
        trial = copy.deepcopy(plan)
        kept: list[PlanChange] = []
        for change in changes:
            try:
                _apply(trial, [change])
            except ValueError as e:
                if change.op != PlanOp.ADD:
                    logger.warning(
                        f"プランが断る編集を捨てた: {change.op.value} {change.objective_id}: {e}"
                    )
                    continue
            kept.append(change)
        return tuple(kept)

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
        on_added: Optional[Callable[[Objective], None]] = None,
    ) -> Optional[Objective]:
        """
        視聴者の頼みを大目標として足す（今取り組んでいるものの後ろに）。`proposal.now`
        （配信者が今やると判断した）なら先頭に。呼び出し側が今の小目標を区切る。

        `advice`: 視聴者の助言を配信者が納得して採った。配信者自身の大目標にし（提案者は
        `suggested_by`）、視聴者の頼みの上限と予算に数えない（2026-09-28: 助言をくれる人が
        1 人 1 つの上限に当たり、もっともな助言を「後で」と流していた）。

        `on_added` を渡すと、まだない建物の設計（Gemini で 1 回 40〜200 秒）を待たない: 断る理由になる確認
        （上限・家がない・同じ頼み）だけ先にして、設計と登録は裏で行い、足せたら `on_added(大目標)`、
        断られたら `on_design_failed` を呼んで、ここでは None を返す（docs/design/107。返事が設計を待って、
        あとのコメントごと 8 分遅れた）。設計が要らなければ、今までどおり足して返す。

        Raises:
            GoalRejectedError: 条件を判定できないとき
            ValueError: 上限を破るとき（例: その視聴者はもう 1 つ持っている）
        """
        what = "の助言" if advice else "の頼み"
        self._check_infrastructure(proposal)
        proposal = await self._beyond_what_is_placed(proposal)
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
        max_blocks = BuildDesign.MAX_BLOCKS if advice else BuildDesign.VIEWER_MAX_BLOCKS
        if on_added is not None and await self._designs_later(proposal):
            key = tuple(proposal.conditions)
            if key in self._designing:
                logger.info(f"{requested_by} {what}は、同じ完了条件の設計がもう裏で進んでいる: 足さない")
                return None
            task = asyncio.create_task(
                self._design_and_add(plan, proposal, requested_by, advice, max_blocks, on_added)
            )
            self._designing[key] = task
            task.add_done_callback(lambda _t, k=key: self._designing.pop(k, None))
            logger.info(f"{requested_by} {what}の建物は、返事のあとに裏で設計する: {proposal.title}")
            return None
        blocks = await self._design_builds(proposal, max_blocks)
        return await self._add_objective(plan, proposal, requested_by, advice, blocks)

    async def all_met(self, specs: Sequence[GoalSpec]) -> bool:
        """条件がどれも、もう満たされているか（判定できなければ偽）。"""
        try:
            return bool(specs) and all(s.met for s in await self._bridge.check(specs))
        except AILoveShenError as e:
            logger.debug(f"条件を判定できなかった: {e}")
            return False

    async def _designs_later(self, proposal: ObjectiveProposal) -> bool:
        """まだない建物があり、設計できる（家がある）なら真。家がなければ、ここで断る（設計しても必ず断られる）。"""
        if self._builder is None:
            return False
        names = _build_names(proposal)
        if not names:
            return False
        known = await self._builder.known()
        if all(n in known for n in names):
            return False
        await self._builder.require_home()
        return True

    async def _design_and_add(
        self,
        plan: ObjectivePlan,
        proposal: ObjectiveProposal,
        requested_by: str,
        advice: bool,
        max_blocks: int,
        on_added: Callable[[Objective], None],
    ) -> None:
        """裏で、建物を設計して登録し、大目標を足す。足せなかったら、理由を `on_design_failed` に渡す。"""
        try:
            blocks = await self._design_builds(proposal, max_blocks)
            goal = await self._add_objective(plan, proposal, requested_by, advice, blocks)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 裏の仕事の失敗で配信を止めない
            logger.warning(f"{requested_by} の頼み「{proposal.title}」を、裏で足せなかった: {e}")
            if self.on_design_failed is not None:
                try:
                    await self.on_design_failed(requested_by, proposal.title, str(e))
                except Exception as say_error:  # noqa: BLE001
                    logger.warning(f"設計が断られたことを話せなかった: {say_error}")
            return
        on_added(goal)

    async def drain(self) -> None:
        """裏の設計がすべて終わるのを待つ（テスト用）。"""
        await asyncio.gather(*self._designing.values(), return_exceptions=True)

    def cancel_designing(self) -> None:
        """裏の設計をやめる（配信を止めるとき）。"""
        for task in list(self._designing.values()):
            task.cancel()

    async def _add_objective(
        self,
        plan: ObjectivePlan,
        proposal: ObjectiveProposal,
        requested_by: str,
        advice: bool,
        blocks: int,
    ) -> Objective:
        """設計のあとの、施設・判定・条件の確認と、大目標の登録。"""
        what = "の助言" if advice else "の頼み"
        viewer = None if advice else requested_by
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
        names = _build_names(proposal)
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
        self,
        plan: ObjectivePlan,
        objective_id: str,
        jobs: tuple[Job, ...],
        planned: Optional[bool] = None,
        keep_done_steps: bool = True,
    ) -> None:
        """
        大目標の中目標（Gemini が書いた小目標の並び）を置き換えて保存する（docs/design/26）。
        `planned` は、計画（中目標と小目標）を書いたかの印（docs/design/50 §18）。
        """
        goal = plan.set_jobs(
            objective_id, jobs, planned=planned, keep_done_steps=keep_done_steps
        )
        logger.info(f"大目標 {goal.id} の中目標: " + " → ".join(s.spec.describe() for s in jobs))
        self._store.save(plan)

    async def reset(self, plan: ObjectivePlan) -> None:
        """
        大目標を最初のリストに戻して保存する（管理画面。視聴者のものも消す）。足せずに飛ばした定石の段や
        設計に失敗した数えも消す: 次の判定で最初からやり直す。
        """
        plan.reset()
        plan.forget_design_failure()
        self._playbook_failures.clear()
        self._playbook_at = -PLAYBOOK_RETRY_SECONDS
        logger.info(f"大目標を最初のリストに戻した: {', '.join(g.describe() for g in plan.pending)}")
        self._store.save(plan)
        await self._publish([ObjectivesResetEvent(count=len(plan.pending))])

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
    # 大目標の断念は先に（足す・動かすより前）: 「足す、落とす」の順に書かれても、落として空いた枠に足せる
    # （2026-10-01: 6 件で満杯のとき、add を先に書いた編集が drop を待たずに断られ、14 回続けて決定に失敗した）
    def frees_a_slot(c: PlanChange) -> bool:
        return c.op == PlanOp.DROP and not c.job_id

    for change in sorted(changes, key=lambda c: not frees_a_slot(c)):  # 安定: 同じ組の中は書いた順
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
        elif change.op == PlanOp.SATISFY:
            done = plan.complete(change.objective_id, f"already served by the facility {change.by}")
            events.append(
                ObjectiveCompletedEvent(
                    title=done.title,
                    requested_by=done.requested_by or "",
                    suggested_by=done.suggested_by or "",
                )
            )
        elif change.job_id:
            job = plan.drop_job(change.objective_id, change.job_id, change.reason)
            events.append(_dropped_job(job, change.reason))
        else:
            events.append(_dropped(plan.drop(change.objective_id, change.reason)))
    return events


def _build_names(proposal: ObjectiveProposal) -> list[str]:
    """条件の built(name) の name。"""
    return [
        c.name
        for c in proposal.conditions
        if c.predicate == GoalPredicate.BUILT and c.name is not None
    ]


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
