"""ドメイン層のエンティティの基底クラス。"""

from __future__ import annotations

import uuid
from abc import ABC
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from ailoveshen.domain.value_objects import (
    NOTE_LIFETIME_DAYS,
    ActionResult,
    Activity,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalOutcome,
    GoalPredicate,
    GoalSpec,
    HouseBlueprint,
    JobState,
    Lesson,
    MessageRole,
    MAX_OPEN_JOBS,
    MessageType,
    Objective,
    ObjectiveState,
    Mission,
    Note,
    NoteKind,
    Job,
    ScreenNote,
    TownDefinition,
    TownMap,
    TownSite,
    TownStage,
)

if TYPE_CHECKING:
    from ailoveshen.domain.events import DomainEvent


def generate_id() -> str:
    """一意な識別子を作る。"""
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    """今の UTC の日時を返す。"""
    return datetime.now(timezone.utc)


@dataclass
class Entity(ABC):
    """
    すべてのドメインエンティティの基底クラス。

    エンティティは、時間や状態が変わっても続く固有の同一性を持つオブジェクト。
    2 つのエンティティは、id が同じなら等しい。
    """

    id: str = field(default_factory=generate_id)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __eq__(self, other: Any) -> bool:
        """id が同じなら等しい。"""
        if not isinstance(other, Entity):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        """set や dict で使うための、id に基づくハッシュ。"""
        return hash(self.id)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.id!r})"


@dataclass
class AggregateRoot(Entity):
    """
    集約ルートの基底クラス。

    集約ルートは、関連するオブジェクトのまとまりへの入り口になるエンティティ。
    外からの参照は、すべて集約ルートに向ける。
    """

    _domain_events: list["DomainEvent"] = field(default_factory=list, repr=False)

    def add_domain_event(self, event: "DomainEvent") -> None:
        """配信するドメインイベントを加える。"""
        self._domain_events.append(event)

    def clear_domain_events(self) -> list["DomainEvent"]:
        """ドメインイベントをすべて返し、空にする。"""
        events = self._domain_events.copy()
        self._domain_events.clear()
        return events


@dataclass(eq=False)
class Conversation(Entity):
    """
    配信の短期の会話履歴。

    視聴者のチャットと配信者の発言を順に持つ。最新の max_history 件だけを残す。

    Raises:
        ValueError: max_history が正でないとき。
    """

    max_history: int = 20
    _messages: deque[ConversationMessage] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """max_history を検証し、上限つきの履歴を作る。"""
        if self.max_history <= 0:
            raise ValueError(f"max_history must be positive, got {self.max_history}")
        self._messages = deque(maxlen=self.max_history)

    def add_viewer_message(
        self,
        content: str,
        user_name: str,
        user_id: Optional[str] = None,
    ) -> ConversationMessage:
        """視聴者のチャットのメッセージを記録する。"""
        return self._append(ConversationMessage.from_viewer(content, user_name, user_id))

    def add_streamer_message(
        self,
        content: str,
        message_type: MessageType,
    ) -> ConversationMessage:
        """配信者が言ったことを記録する。"""
        return self._append(ConversationMessage.from_streamer(content, message_type))

    def recent_messages(self, limit: int = 10) -> tuple[ConversationMessage, ...]:
        """最新のメッセージを、古い順に返す。"""
        if limit <= 0:
            return ()
        return tuple(self._messages)[-limit:]

    def recent_viewer_messages(self, limit: int = 5) -> tuple[ConversationMessage, ...]:
        """視聴者の最新のチャットのメッセージを、古い順に返す。"""
        if limit <= 0:
            return ()
        viewer = [m for m in self._messages if m.role == MessageRole.VIEWER]
        return tuple(viewer[-limit:])

    def clear(self) -> None:
        """履歴をすべて忘れる。"""
        self._messages.clear()
        self.updated_at = _utc_now()

    def _append(self, message: ConversationMessage) -> ConversationMessage:
        self._messages.append(message)
        self.updated_at = _utc_now()
        return message

    def __iter__(self) -> Iterator[ConversationMessage]:
        return iter(self._messages)

    def __len__(self) -> int:
        return len(self._messages)


@dataclass(eq=False)
class ObjectivePlan(Entity):
    """
    最終目標と、優先度順の大目標（未完了の最初のものに今取り組む）。それと、最終目標が
    作る街: その定義と、済んだ段階の数。取り組んでいる段階のうち今できることは、
    配信者自身の大目標になる。ほかの大目標と同じように（世界から）完了し、リストの
    下に移すことはできるが、断念はできない。段階は、条件をすべて満たし、まだない
    能力のために残した部分がなくなったときに済み、街は次に進む。

    街の場所は、段階より先に決める（docs/design/16_town_site.md）: 候補地を調べ、
    配信者が 1 か所選ぶ（一度だけ）。調査と引っ越しは街の準備の大目標で、段階と
    同じくやめられない。準備が残っている間は、段階の大目標を足さない（段階の条件は
    家を基準に判定するので、引っ越す前の家で満たしても意味がない）。

    上限は、視聴者が配信を乗っ取らないためにある: 視聴者の大目標は取り組んでいる
    ものの後ろに入り、1 人 1 つ、全部で `max_viewer_goals` までで、`viewer_budget`
    ステップを使ったら断念する。大目標は世界から完了にする（ブリッジが条件を判定
    する）。モデルが済んだと言っても完了にはしない。

    Raises:
        ValueError: 上限が正でないとき（作るとき）、または操作が上限を破るとき
            （どの上限かをメッセージで言い、モデルにやり直させる）。
    """

    mission: Mission = field(kw_only=True)
    max_goals: int = 6
    max_viewer_goals: int = 2
    viewer_budget: int = 80
    finished_shown: int = 3
    _goals: list[Objective] = field(default_factory=list, init=False, repr=False)
    _finished: deque[Objective] = field(init=False, repr=False)
    _next_id: int = field(default=1, init=False, repr=False)
    _next_job_id: int = field(default=1, init=False, repr=False)
    _dropped_jobs: list[Job] = field(default_factory=list, init=False, repr=False)
    _town: Optional[TownDefinition] = field(default=None, init=False, repr=False)
    _town_stage: int = field(default=0, init=False, repr=False)
    _stage_met: tuple[GoalSpec, ...] = field(default=(), init=False, repr=False)
    _site: Optional[TownSite] = field(default=None, init=False, repr=False)
    _town_map: Optional[TownMap] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """上限を検証する。"""
        for label in ("max_goals", "max_viewer_goals", "viewer_budget", "finished_shown"):
            if getattr(self, label) <= 0:
                raise ValueError(f"{label} must be positive, got {getattr(self, label)}")
        self._finished = deque(maxlen=self.finished_shown)

    @property
    def pending(self) -> tuple[Objective, ...]:
        """まだやる大目標（優先度順）。"""
        return tuple(self._goals)

    @property
    def finished(self) -> tuple[Objective, ...]:
        """最近完了した、または断念した大目標（古い順）。終わった大目標に残った中目標は閉じて見せる。"""
        return tuple(g.closed() for g in self._finished)

    @property
    def current(self) -> Optional[Objective]:
        """今取り組んでいる大目標（未完了の最初のもの）。"""
        return self._goals[0] if self._goals else None

    @property
    def town(self) -> Optional[TownDefinition]:
        """街がどんなものかと、その段階（定めるまでは None）。"""
        return self._town

    @property
    def site(self) -> Optional[TownSite]:
        """選んだ街の場所（候補地を調べて選ぶまでは None）。"""
        return self._site

    def choose_site(self, site: TownSite) -> None:
        """
        街の場所を決める。一度だけ（決めた後は変えない）。

        Raises:
            ValueError: もう決めてあるとき
        """
        if self._site is not None:
            raise ValueError(f"the town site is already chosen ({self._site.site_id})")
        self._site = site
        self.updated_at = _utc_now()

    @property
    def town_map(self) -> Optional[TownMap]:
        """街の理想図（docs/design/47 §6。まだ描いていなければ None）。"""
        return self._town_map

    def set_town_map(self, town_map: TownMap) -> None:
        """理想図を描いた・描き直した。"""
        self._town_map = town_map

    @property
    def preparing_town(self) -> bool:
        """街の準備（調査、引っ越し）の大目標がまだ残っているか。"""
        return any(g.prepares_town for g in self._goals)

    @property
    def town_stage(self) -> int:
        """街の段階のうち、済んだものの数。"""
        return self._town_stage

    @property
    def current_stage(self) -> Optional[TownStage]:
        """取り組んでいる街の段階（None: 街がまだないか、完成した）。"""
        if self._town is None or self._town_stage >= len(self._town.stages):
            return None
        return self._town.stages[self._town_stage]

    @property
    def town_complete(self) -> bool:
        """街のすべての段階が済んだか。"""
        return self._town is not None and self._town_stage >= len(self._town.stages)

    @property
    def stage_met(self) -> tuple[GoalSpec, ...]:
        """今の段階の条件のうち、（その大目標で）これまでに満たしたもの。"""
        return self._stage_met

    @property
    def stage_remaining(self) -> tuple[GoalSpec, ...]:
        """今の段階の条件のうち、まだ満たしていないもの（次の大目標が求めるもの）。"""
        stage = self.current_stage
        if stage is None:
            return ()
        return tuple(c for c in stage.conditions if c not in self._stage_met)

    def settle_stage(self) -> bool:
        """済んだ段階の先へ街を進める。進んだかを返す。"""
        moved = False
        while (
            (stage := self.current_stage) is not None and stage.ready and not self.stage_remaining
        ):
            self._town_stage += 1
            self._stage_met = ()
            moved = True
        return moved

    def define_town(self, town: TownDefinition) -> None:
        """街がどんなものかを設定する（未解決の段階は、能力が増えたら書き直すことがある）。"""
        self._town = town
        self.updated_at = _utc_now()

    def stage_goal(self) -> Optional[Objective]:
        """街の今の段階を表す、未完了の大目標。"""
        return next((g for g in self._goals if g.stage == self._town_stage), None)

    def get(self, objective_id: str) -> Optional[Objective]:
        """id で指した未完了の大目標。"""
        return next((g for g in self._goals if g.id == objective_id), None)

    def viewers(self) -> list[str]:
        """今リストに頼みがある視聴者（大目標と、その中目標の分を合わせて数える。docs/design/50 §5）。"""
        names = [g.requested_by for g in self._goals if g.requested_by is not None]
        names += [
            s.requested_by
            for g in self._goals
            for s in g.open_jobs
            if s.requested_by is not None
        ]
        return names

    def check_room(self, requested_by: Optional[str] = None, job: bool = False) -> None:
        """
        大目標をもう 1 つ足せるか（設計などの前に確かめる）。`job`: 中目標として足す（リストの長さの
        上限は受けない）。視聴者の上限は、大目標と中目標を合わせて数える。

        Raises:
            ValueError: リストがいっぱい、その視聴者の頼みがもうある、視聴者の頼みが上限のとき
        """
        if not job and len(self._goals) >= self.max_goals:
            raise ValueError(f"the objective list is full ({self.max_goals})")
        if requested_by is not None:
            viewers = self.viewers()
            if requested_by in viewers:
                raise ValueError(f"{requested_by} already has a request in the list")
            if len(viewers) >= self.max_viewer_goals:
                raise ValueError(f"the list already has {self.max_viewer_goals} viewers' requests")

    def add(
        self,
        title: str,
        conditions: tuple[GoalSpec, ...],
        reason: str = "",
        requested_by: Optional[str] = None,
        position: Optional[int] = None,
        stage: Optional[int] = None,
        prepares_town: bool = False,
        budget: Optional[int] = None,
        now: bool = False,
        suggested_by: Optional[str] = None,
    ) -> Objective:
        """
        `position`（未完了のものの中で 0 始まり。None: 最後）に大目標を足す。

        視聴者の大目標は、position が何でも、取り組んでいるものの後ろに入る。ただし `now`
        （配信者が待っている間に今やると引き受けた頼み）は先頭にも入れる。`suggested_by`
        （納得して採った助言）は配信者自身の大目標なので、視聴者の上限に数えない。
        """
        self.check_room(requested_by)
        goal = Objective(
            id=f"m{self._next_id}",
            title=title,
            conditions=conditions,
            reason=reason,
            requested_by=requested_by,
            stage=stage,
            prepares_town=prepares_town,
            budget=budget,
            suggested_by=suggested_by,
        )
        earliest = 1 if requested_by is not None and self._goals and not now else 0
        at = (
            len(self._goals) if position is None else min(max(position, earliest), len(self._goals))
        )
        self._goals.insert(at, goal)
        self._next_id += 1
        self.updated_at = _utc_now()
        return goal

    def same_as(self, conditions: Sequence[GoalSpec]) -> Optional[Objective]:
        """未完了の大目標のうち、完了条件が同じもの（順番は問わない）。なければ None。"""
        want = {c.describe() for c in conditions}
        return next((g for g in self.pending if {c.describe() for c in g.conditions} == want), None)

    def move(self, objective_id: str, position: int) -> Objective:
        """
        未完了の大目標の優先度を変える（position は 0 始まり）。配信者自身の判断なので、
        視聴者の頼みも先頭にできる（合理的なら優先を変えながら進める。13 §14）。
        """
        goal = self._require(objective_id)
        self._goals.remove(goal)
        self._goals.insert(min(max(position, 0), len(self._goals)), goal)
        self.updated_at = _utc_now()
        return goal

    def drop(self, objective_id: str, reason: str) -> Objective:
        """大目標を断念する。理由は必須（配信で言う）。"""
        if not reason:
            raise ValueError(f"dropping {objective_id} needs a reason")
        goal = self._require(objective_id)
        if goal.stage is not None:
            raise ValueError(
                f"{objective_id} is a stage of the town: move it down instead of dropping it"
            )
        if goal.prepares_town:
            raise ValueError(
                f"{objective_id} prepares the town (its site): move it down instead of dropping it"
            )
        return self._finish(objective_id, ObjectiveState.DROPPED, reason)

    def complete(self, objective_id: str) -> Objective:
        """大目標を完了にする（条件が満たされている）。段階の大目標なら、街が進むことがある。"""
        done = self._finish(objective_id, ObjectiveState.DONE, "its conditions hold")
        if done.stage is not None and done.stage == self._town_stage:
            self._stage_met += tuple(c for c in done.conditions if c not in self._stage_met)
            self.settle_stage()
        return done

    def set_jobs(
        self,
        objective_id: str,
        jobs: tuple[Job, ...],
        planned: Optional[bool] = None,
        keep_done_steps: bool = True,
    ) -> Objective:
        """
        大目標の中目標を書き直す（Gemini が書いた、これから残す小目標の並び。docs/design/26、50 §4）。

        済んだ中目標は残し（書き直しの中に同じ条件があっても足さない）、同じ条件の中目標は id・出どころ・
        使ったステップ・進み具合を引き継ぎ、視聴者と助言の中目標は Gemini が書かなくても残す
        （やめるのは `drop_job` だけ）。計画（名前・小目標）は、書き直しに書いてなければ前のものを残し、
        済みにした小目標は今の計画に残っているものだけ引き継ぐ（`keep_done_steps` が偽なら捨てる:
        死んで持ち物を失ったあとなど）。`planned` を渡すと、計画を書いたかの印もそれにする。

        Raises:
            ValueError: まだ済んでいない中目標が MAX_OPEN_JOBS を超えるとき
        """
        goal = self._require(objective_id)
        done = [s for s in goal.jobs if s.state == JobState.DONE]
        old_open = list(goal.open_jobs)
        used: set[str] = set()
        merged: list[Job] = []
        for job in jobs:
            if any(d.same_as(job) for d in done):
                continue
            same = next((o for o in old_open if o.id not in used and o.same_as(job)), None)
            if same is None:
                merged.append(replace(job, id=self._new_job_id()))
                continue
            used.add(same.id)
            steps = job.steps or same.steps
            keys = {x.key for s in steps for x in s.walk()}
            merged.append(
                replace(
                    job,
                    id=same.id,
                    requested_by=same.requested_by,
                    suggested_by=same.suggested_by,
                    budget_used=same.budget_used,
                    progress=same.progress,
                    title=job.title or same.title,
                    steps=steps,
                    steps_done=tuple(k for k in same.steps_done if k in keys)
                    if keep_done_steps
                    else (),
                )
            )
        merged += [o for o in old_open if o.id not in used and (o.requested_by or o.suggested_by)]
        if len(merged) > MAX_OPEN_JOBS:
            raise ValueError(f"an objective holds at most {MAX_OPEN_JOBS} jobs not done yet")
        return self._replace(
            goal,
            replace(
                goal,
                jobs=(*done, *merged),
                planned=goal.planned if planned is None else planned,
            ),
        )

    def set_planned(self, objective_id: str, planned: bool) -> Objective:
        """計画を書いたかの印を付け替える（中目標が尽きた大目標は、また書かせる）。"""
        goal = self._require(objective_id)
        return self._replace(goal, replace(goal, planned=planned))

    def reconcile(self, objective_id: str, met: Mapping[str, Sequence[str]]) -> None:
        """
        世界と照合した結果を反映する（中目標の id → 今満たしていた条件の名前）。済みの中目標も、
        一番上でない大目標の中目標も。済み（ラッチ）は変えない。表示が使う（docs/design/50 §19）。
        """
        goal = self._require(objective_id)
        jobs = tuple(replace(j, met_now=tuple(met.get(j.id, ()))) for j in goal.jobs)
        if jobs != goal.jobs:
            self._replace(goal, replace(goal, jobs=jobs))

    def judge_steps(self, objective_id: str, met: Mapping[str, Sequence[str]]) -> None:
        """
        計画の小目標を判定した結果を反映する（中目標の id → 今満たしていた小目標の `Step.key`）。
        一度満たしたら済み（ラッチ）のままにする。
        """
        goal = self._require(objective_id)
        jobs = []
        for job in goal.jobs:
            keys = met.get(job.id)
            if job.state != JobState.PENDING or not keys:
                jobs.append(job)
                continue
            valid = set(job.step_keys())
            done = tuple(dict.fromkeys((*job.steps_done, *(k for k in keys if k in valid))))
            jobs.append(replace(job, steps_done=done))
        self._replace(goal, replace(goal, jobs=tuple(jobs)))

    def add_job(
        self,
        objective_id: str,
        job: Job,
        position: Optional[int] = None,
        now: bool = False,
    ) -> Job:
        """
        大目標に中目標を 1 つ足す（視聴者の頼みや採った助言。`position` はまだ済んでいない中目標の中での
        0 始まりの位置、None: 最後）。視聴者の中目標は、今取り組んでいる中目標の後ろに入る（`now` を除く）。
        同じ条件の中目標がもうあれば、それを返す。

        Raises:
            ValueError: 視聴者の上限を破るか、中目標が MAX_OPEN_JOBS を超えるとき
        """
        goal = self._require(objective_id)
        same = next((s for s in goal.jobs if s.same_as(job)), None)
        if same is not None:
            return same
        self.check_room(job.requested_by, job=True)
        if len(goal.open_jobs) >= MAX_OPEN_JOBS:
            raise ValueError(f"an objective holds at most {MAX_OPEN_JOBS} jobs not done yet")
        new = replace(job, id=self._new_job_id(), state=JobState.PENDING)
        jobs = list(goal.jobs)
        open_at = [i for i, s in enumerate(jobs) if s.state == JobState.PENDING]
        earliest = 1 if job.requested_by is not None and open_at and not now else 0
        at = (
            len(jobs)
            if position is None or max(position, earliest) >= len(open_at)
            else open_at[max(position, earliest)]
        )
        jobs.insert(at, new)
        self._replace(goal, replace(goal, jobs=tuple(jobs)))
        return new

    def judge_jobs(
        self, objective_id: str, judged: Sequence[tuple[str, bool, tuple[str, ...]]]
    ) -> tuple[Job, ...]:
        """
        まだ済んでいない中目標を判定した結果を反映する: (中目標の id, 満たしたか, 進み具合の行)。
        一度満たしたら済み（ラッチ）のままにする。今回済みにした中目標を返す。
        """
        goal = self._require(objective_id)
        by_id = {job_id: (met, lines) for job_id, met, lines in judged}
        finished: list[Job] = []
        jobs: list[Job] = []
        for s in goal.jobs:
            if s.state != JobState.PENDING or s.id not in by_id:
                jobs.append(s)
                continue
            met, lines = by_id[s.id]
            s = replace(s, progress=lines, state=JobState.DONE if met else JobState.PENDING)
            if met:
                finished.append(s)
            jobs.append(s)
        self._replace(goal, replace(goal, jobs=tuple(jobs)))
        return tuple(finished)

    def drop_job(self, objective_id: str, job_id: str, reason: str) -> Job:
        """中目標を断念してリストから外す。理由は必須（視聴者の中目標なら配信で言う）。"""
        if not reason:
            raise ValueError(f"dropping job {job_id} needs a reason")
        goal = self._require(objective_id)
        job = next((s for s in goal.open_jobs if s.id == job_id), None)
        if job is None:
            ids = ", ".join(s.id for s in goal.open_jobs) or "none"
            raise ValueError(f"no job {job_id} to do in {objective_id} (jobs: {ids})")
        self._replace(
            goal, replace(goal, jobs=tuple(s for s in goal.jobs if s is not job))
        )
        return replace(job, state=JobState.DROPPED, ended_because=reason)

    def viewer_job(
        self, viewer: str, advice: bool = False
    ) -> Optional[tuple[Objective, Job]]:
        """
        その視聴者の頼みの中目標（advice なら、採った助言の中目標。まだ済んでいないもの）と、
        それがある大目標。なければ None。
        """
        for g in self._goals:
            for s in g.open_jobs:
                if (s.suggested_by if advice else s.requested_by) == viewer:
                    return g, s
        return None

    def judged(self, objective_id: str, progress: tuple[str, ...]) -> None:
        """大目標の条件の進み具合を保つ。"""
        goal = self._require(objective_id)
        self._replace(goal, replace(goal, progress=progress))

    def charge(
        self, objective_id: Optional[str], spec: Optional[GoalSpec] = None
    ) -> Optional[Objective]:
        """
        大目標のステップを 1 つ数える。予算を超えた視聴者のものは断念して返す。小目標 `spec` が視聴者
        の中目標のものなら、その中目標にも数える（予算は中目標ごと）。中目標が予算を超えたら外し、
        `take_dropped_jobs` で受け取る。
        """
        goal = self.get(objective_id) if objective_id else None
        if goal is None:
            return None
        job = next(
            (
                s
                for s in goal.open_jobs
                if spec is not None and s.requested_by is not None and spec in s.specs
            ),
            None,
        )
        jobs = goal.jobs
        if job is not None:
            jobs = tuple(replace(s, budget_used=s.budget_used + 1) if s is job else s for s in jobs)
        goal = self._replace(goal, replace(goal, budget_used=goal.budget_used + 1, jobs=jobs))
        if job is not None:
            used = next(s for s in goal.jobs if s.id == job.id).budget_used
            if used >= self.viewer_budget:
                self._dropped_jobs.append(
                    self.drop_job(
                        goal.id, job.id, f"it took {used} steps, over the budget for a request"
                    )
                )
        budget = goal.budget or self.viewer_budget
        if goal.requested_by is not None and goal.budget_used >= budget:
            return self.drop(
                goal.id, f"it took {goal.budget_used} steps, over the budget for a request"
            )
        return None

    def take_dropped_jobs(self) -> tuple[Job, ...]:
        """予算を超えて断念した視聴者の中目標を受け取る（受け取ったら空になる）。"""
        dropped, self._dropped_jobs = tuple(self._dropped_jobs), []
        return dropped

    def restore(
        self,
        pending: list[Objective],
        finished: list[Objective],
        next_id: int,
        town: Optional[TownDefinition] = None,
        town_stage: int = 0,
        stage_met: tuple[GoalSpec, ...] = (),
        site: Optional[TownSite] = None,
        town_map: Optional[TownMap] = None,
        next_job_id: int = 1,
    ) -> None:
        """保存したプランを戻す（id はそのまま）。"""
        self._goals = list(pending)
        self._finished.clear()
        self._finished.extend(finished)
        self._next_id = next_id
        self._next_job_id = next_job_id
        self._town = town
        self._town_stage = town_stage
        self._stage_met = stage_met
        self._site = site
        self._town_map = town_map

    @property
    def next_id(self) -> int:
        """次の大目標の id につく番号（保存するときに残す）。"""
        return self._next_id

    @property
    def next_job_id(self) -> int:
        """次の中目標の id につく番号（保存するときに残す）。"""
        return self._next_job_id

    def _new_job_id(self) -> str:
        job_id = f"j{self._next_job_id}"
        self._next_job_id += 1
        return job_id

    def _require(self, objective_id: str) -> Objective:
        goal = self.get(objective_id)
        if goal is None:
            ids = ", ".join(g.id for g in self._goals) or "none"
            raise ValueError(f"no pending objective {objective_id} (pending: {ids})")
        return goal

    def _replace(self, old: Objective, new: Objective) -> Objective:
        self._goals[self._goals.index(old)] = new
        self.updated_at = _utc_now()
        return new

    def _finish(self, objective_id: str, state: ObjectiveState, because: str) -> Objective:
        goal = self._require(objective_id)
        self._goals.remove(goal)
        ended = replace(goal, state=state, ended_because=because).closed()
        self._finished.append(ended)
        self.updated_at = _utc_now()
        return ended


@dataclass(eq=False)
class Notebook(Entity):
    """
    配信者が自分で書き残すメモ（docs/design/18_notes.md）。確かめていないので、世界の
    事実とは別に見せる。

    量と寿命をコードが守る: 全部で `max_notes` 件まで（いっぱいなら先に消す）、書いた
    日から `lifetime_days` 日で消える（keep すると、その日から延びる）。

    Raises:
        ValueError: 上限が正でないとき（作るとき）、または操作が上限を破るか、ないメモを
            指すとき（モデルにやり直させる）。
    """

    max_notes: int = 12
    lifetime_days: int = NOTE_LIFETIME_DAYS
    _notes: list[Note] = field(default_factory=list, init=False, repr=False)
    _next_id: int = field(default=1, init=False, repr=False)

    def __post_init__(self) -> None:
        """上限を検証する。"""
        for label in ("max_notes", "lifetime_days"):
            if getattr(self, label) <= 0:
                raise ValueError(f"{label} must be positive, got {getattr(self, label)}")

    @property
    def notes(self) -> tuple[Note, ...]:
        """残っているメモ（書いた順）。"""
        return tuple(self._notes)

    @property
    def next_id(self) -> int:
        """次のメモの id につく番号（保存するときに残す）。"""
        return self._next_id

    def add(self, kind: NoteKind, text: str, day: int, about: str = "") -> Note:
        """メモを書く。`day` の日から寿命を数える。"""
        if len(self._notes) >= self.max_notes:
            raise ValueError(f"the notebook is full ({self.max_notes} notes): drop one first")
        note = Note(
            id=f"n{self._next_id}",
            kind=kind,
            text=text.strip(),
            written_day=day,
            expires_day=day + self.lifetime_days,
            about=about,
        )
        self._next_id += 1
        self._notes.append(note)
        self.updated_at = _utc_now()
        return note

    def keep(self, note_id: str, day: int) -> Note:
        """まだ正しいメモの寿命を、`day` の日から数え直す。"""
        note = self._require(note_id)
        kept = replace(note, expires_day=day + self.lifetime_days)
        self._notes[self._notes.index(note)] = kept
        self.updated_at = _utc_now()
        return kept

    def drop(self, note_id: str) -> Note:
        """メモを消す。"""
        note = self._require(note_id)
        self._notes.remove(note)
        self.updated_at = _utc_now()
        return note

    def expire(self, day: int) -> tuple[Note, ...]:
        """寿命が過ぎたメモ（期限の日が `day` より前）を消す。消したものを返す。"""
        expired = tuple(n for n in self._notes if n.expires_day < day)
        if expired:
            self._notes = [n for n in self._notes if n.expires_day >= day]
            self.updated_at = _utc_now()
        return expired

    def restore(self, notes: list[Note], next_id: int) -> None:
        """保存したメモを戻す（id はそのまま）。"""
        self._notes = list(notes)
        self._next_id = next_id

    def _require(self, note_id: str) -> Note:
        for note in self._notes:
            if note.id == note_id:
                return note
        ids = ", ".join(n.id for n in self._notes) or "none"
        raise ValueError(f"no note {note_id} (notes: {ids})")


@dataclass(eq=False)
class PlaySession(Entity):
    """
    プレイセッション: 建てる家、最終目標とその大目標、自分のメモ、今の（小）目標のライフサイクル。

    目標を達成したかは、ブリッジが世界から判定する。新しい目標が要る時はセッションが
    決める: まだ目標がない、達成した、役立っていた大目標が完了または断念した、行き
    詰まった（行動が失敗し続ける）、進まない（残りの作業が減らなくなった）、予算を
    超えた、時間帯が変わった。

    配信者が今していること（`activity()`）を持つのはセッションだけ: 目標の決定、
    実況、チャットの返答はすべてこれを読む。小目標を変えるのはステップのループだけ。
    チャットの返答は（プランの上限の中で）大目標を足すことはあるが、小目標に割り
    込むことはない。

    Raises:
        ValueError: 上限が正でないとき。
    """

    blueprint: Optional[HouseBlueprint] = field(kw_only=True)  # None: 家は前に建った
    plan: ObjectivePlan = field(kw_only=True)
    notebook: Notebook = field(default_factory=Notebook, kw_only=True)
    max_steps_per_goal: int = 40
    # 小目標の束（docs/design/44）: 一緒に追う条件 1 つにつき足す歩数
    steps_per_bundled_goal: int = 20
    max_consecutive_failures: int = 3
    max_stalled_steps: int = 8
    goal_history: int = 5
    goal: Optional[Goal] = field(default=None, init=False)
    goal_phase: str = field(default="", init=False)
    steps_in_goal: int = field(default=0, init=False)
    consecutive_failures: int = field(default=0, init=False)
    stalled_steps: int = field(default=0, init=False)
    least_remaining: Optional[int] = field(default=None, init=False)
    # 束の条件のうち、済んだもの（ブリッジの label。済んだことを 1 回だけ伝える）
    members_met: frozenset[str] = field(default=frozenset(), init=False)
    completion_announced: bool = field(default=False, init=False)
    last_observation: Optional[GameObservation] = field(default=None, init=False)
    # 道具で操作しているとき、今やろうとしていること（実況と返答も見る。12 の一致）
    intent: str = field(default="", init=False)
    # 行動を実行している間だけ真（選んでいる間・目標を決めている間は偽）。一言の実況はこの間だけ話す
    acting: bool = field(default=False, init=False)
    # 位置・持ち物・小目標の進み具合が変わらない時間（秒。動かないと数えるときだけ 0 より大きい。一言は黙る）
    stagnant_seconds: float = field(default=0.0, init=False)
    # 定期の見直しで画面について書いたこと（確かめていない。docs/design/23）
    screen_note: Optional[ScreenNote] = field(default=None, init=False)
    # 次の切れ目で今の小目標を終わらせる理由（コメントの指摘、画面の見直し、行き詰まりの確認）。
    # 番号つきで溜める: 後から来た理由で前の理由を消さない、決めている間に来た理由を残す
    _rethinks: list[tuple[int, str]] = field(default_factory=list, init=False, repr=False)
    _rethink_seq: int = field(default=0, init=False, repr=False)
    # 今の状況で思い出した教訓（小目標の始まりか目標の決定の前。docs/design/35）
    lessons: tuple[Lesson, ...] = field(default=(), init=False)
    # 小目標を決めたときのブリッジの死んだ回数（増えたら、死んでリスポーンした: 小目標を選び直す）
    deaths_at_goal: Optional[int] = field(default=None, init=False)
    # 直前のステップが襲われて止まった: 次の読み取りを「進まない」に数えない（docs/design/28）
    _skip_stall: bool = field(default=False, init=False, repr=False)
    _recent_goals: deque[GoalOutcome] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """上限を検証し、上限つきの目標の履歴を作る。"""
        for label in (
            "max_steps_per_goal",
            "max_consecutive_failures",
            "max_stalled_steps",
            "goal_history",
        ):
            if getattr(self, label) <= 0:
                raise ValueError(f"{label} must be positive, got {getattr(self, label)}")
        self._recent_goals = deque(maxlen=self.goal_history)

    def observe(self, obs: GameObservation) -> None:
        """最新の観測を保つ（チャットの返答が見るゲームの様子）。"""
        self.last_observation = obs

    def activity(self) -> Activity:
        """配信者が今していることとその理由（目標の決定、実況、返答が見る）。"""
        return Activity(
            mission=self.plan.mission,
            town=self.plan.town,
            site=self.plan.site,
            town_stage=self.plan.town_stage,
            stage_met=self.plan.stage_met,
            objectives=self.plan.pending + self.plan.finished,
            goal=self.goal,
            observation=self.last_observation,
            recent_goals=self.recent_goals,
            notes=self.notebook.notes,
            intent=self.intent,
            acting=self.acting,
            failing=self.consecutive_failures if self.goal else 0,
            stagnant_seconds=self.stagnant_seconds,
            screen_note=self.screen_note,
            lessons=self.lessons,
            town_map=self.plan.town_map,
        )

    @property
    def recent_goals(self) -> tuple[GoalOutcome, ...]:
        """最近の目標と、それぞれの終わり方（古い順）。"""
        return tuple(self._recent_goals)

    def end_goal(self, ended_because: str, met: bool) -> Optional[GoalOutcome]:
        """今の目標の終わり方を記録する（目標がなければ何もしない）。"""
        if self.goal is None:
            return None
        outcome = GoalOutcome(goal=self.goal, ended_because=ended_because, met=met)
        self._recent_goals.append(outcome)
        return outcome

    def track_progress(self, obs: GameObservation) -> None:
        """今の目標の残りの作業を記録する。減らなければ、進んでいないと数える。"""
        if self.goal is None or obs.goal is None:
            return
        remaining = obs.goal.remaining
        skip, self._skip_stall = self._skip_stall, False
        if self.least_remaining is None or remaining < self.least_remaining:
            self.least_remaining = remaining
            self.stalled_steps = 0
        elif self.steps_in_goal > 0 and not skip:
            self.stalled_steps += 1

    def phase_changed(self, obs: GameObservation) -> bool:
        """目標を設定してから時間帯が進んだ（例: 夕暮れが始まった）。"""
        return self.goal is not None and obs.time_phase != self.goal_phase

    def needs_new_goal(self, obs: GameObservation) -> bool:
        """
        新しい目標が要る: まだない、達成した、その大目標が終わった、行き詰まった、進まない、
        長すぎる、夕暮れ。
        """
        return bool(self.goal_end_reason(obs))

    def goal_end_reason(self, obs: GameObservation) -> str:
        """新しい目標が要る理由。LLM のプロンプトとログに使う（目標が続く間は ""）。"""
        if self.goal is None or obs.goal is None:
            return "no goal yet"
        name = self.goal.describe()
        if self.died_since_goal(obs):
            # 場所も持ち物も変わった: 達成より先に見る（リスポーンで状況が一変する）
            return (
                f"goal {name} is cut because the streamer died and respawned "
                "(the position changed and the items carried may be lost)"
            )
        if obs.goal.met:
            return f"goal {name} is met"
        if self.rethink_reason:
            return f"goal {name} is reconsidered: {self.rethink_reason}"
        objective_id = self.goal.objective_id
        if objective_id is not None and self.plan.get(objective_id) is None:
            return f"the objective {objective_id} that goal {name} served has ended"
        if self.consecutive_failures >= self.max_consecutive_failures:
            return f"goal {name} is stuck (actions keep failing)"
        if self.stalled_steps >= self.max_stalled_steps:
            return f"goal {name} stalled (no progress in {self.stalled_steps} steps)"
        # 夜を越す目標は夕暮れ、夜、明け方にまたがり、朝になると自ら終わる
        spans_the_night = self.goal.spec.predicate == GoalPredicate.THROUGH_NIGHT
        if self.steps_in_goal >= self.step_budget and not spans_the_night:
            return f"goal {name} ran for {self.steps_in_goal} steps"
        if self.phase_changed(obs) and not spans_the_night:
            return f"the time of day changed from {self.goal_phase} to {obs.time_phase}"
        return ""

    @property
    def step_budget(self) -> int:
        """今の小目標の歩数の上限（束なら一緒に追う条件の分を足す）。"""
        extra = len(self.goal.together) if self.goal is not None else 0
        return self.max_steps_per_goal + self.steps_per_bundled_goal * extra

    def newly_met_members(self, obs: GameObservation) -> tuple[str, ...]:
        """
        束の条件のうち、今回初めて済んだもの（束全体が済んだときは返さない: 小目標の終わりで話す）。
        """
        if self.goal is None or obs.goal is None or not obs.goal.members or obs.goal.met:
            return ()
        met = [m.label for m in obs.goal.members if m.met and m.label not in self.members_met]
        self.members_met = self.members_met | set(met)
        return tuple(met)

    def died_since_goal(self, obs: GameObservation) -> bool:
        """今の小目標を決めてから死んだか（ブリッジの死んだ回数が増えた）。"""
        deaths = obs.state.get("deaths")
        return (
            isinstance(deaths, int)
            and self.deaths_at_goal is not None
            and deaths > self.deaths_at_goal
        )

    def set_goal(self, goal: Goal, time_phase: str, rethinks_seen: Optional[int] = None) -> None:
        """
        与えられた時間帯に、新しい目標を追い始める。

        Args:
            rethinks_seen: この目標を決めたときに読んだ考え直しの番号（`rethink_mark`）。それより
                後に頼まれた考え直し（決めている間に来たコメントの指摘）は残し、次の切れ目で使う。
                None なら全部使ったことにする
        """
        seen = self.last_observation.state.get("deaths") if self.last_observation else None
        self.deaths_at_goal = seen if isinstance(seen, int) else None
        self.goal = goal
        self.goal_phase = time_phase
        self.steps_in_goal = 0
        self.consecutive_failures = 0
        self.stalled_steps = 0
        self.least_remaining = None
        self.members_met = frozenset()
        self.intent = ""
        seen = self._rethink_seq if rethinks_seen is None else rethinks_seen
        self._rethinks = [r for r in self._rethinks if r[0] > seen]
        self.updated_at = _utc_now()

    def resume_goal(
        self,
        goal: Goal,
        time_phase: str,
        steps_in_goal: int,
        consecutive_failures: int,
        stalled_steps: int,
        least_remaining: Optional[int],
        deaths_at_goal: Optional[int],
    ) -> None:
        """再起動のあとに、保存しておいた小目標を、歩みの数えごと続きから追う。"""
        self.goal = goal
        self.goal_phase = time_phase
        self.steps_in_goal = steps_in_goal
        self.consecutive_failures = consecutive_failures
        self.stalled_steps = stalled_steps
        self.least_remaining = least_remaining
        self.deaths_at_goal = deaths_at_goal
        self.updated_at = _utc_now()

    @property
    def rethink_reason(self) -> str:
        """次の切れ目で今の小目標を終わらせる理由（溜まった順に「; 」でつなぐ。なければ ""）。"""
        return "; ".join(text for _, text in self._rethinks)

    @property
    def rethink_mark(self) -> int:
        """今までに頼まれた考え直しの番号（切れ目で読み、set_goal に渡す）。"""
        return self._rethink_seq

    def request_rethink(self, reason: str) -> None:
        """
        次の切れ目で今の小目標を終わらせ、この理由で次の小目標を決めさせる。まだ使われていない
        理由があれば、置き換えずに足す（コメントの指摘を画面の見直しで消さない）。
        """
        text = reason.strip()
        if not text or any(text == t for _, t in self._rethinks):
            return
        self._rethink_seq += 1
        self._rethinks.append((self._rethink_seq, text))
        self.updated_at = _utc_now()

    def note_screen(self, note: ScreenNote) -> None:
        """画面の見直しで書いたことを持つ（1 件だけ。新しいもので置き換える）。"""
        self.screen_note = note
        self.updated_at = _utc_now()

    def start_action(self, intent: str = "") -> None:
        """
        行動を実行し始める。道具を呼ぶときに配信者が書いた、今やろうとしていること（intent）を
        持つ。
        """
        self.acting = True
        self.intent = intent.strip()
        self.updated_at = _utc_now()

    def end_action(self) -> None:
        """
        行動が終わった: 今やろうとしていることを消す（2026-09-28: 終わった・断られた行動の intent が
        残り、次の行動を決めている間の一言が「〜するね」と何度も言った）。
        """
        self.acting = False
        self.intent = ""
        self.updated_at = _utc_now()

    def skip_stall_once(self) -> None:
        """次の進み具合の記録を「進まない」に数えない（ついでの作業、襲われて止まった行動）。"""
        self._skip_stall = True

    def record(self, result: ActionResult) -> Optional[Objective]:
        """今の目標とその大目標の分としてステップを 1 つ数える。予算を超えて断念した大目標
        （視聴者の頼み）があれば返す。"""
        self.steps_in_goal += 1
        if result.cut_by_attack:
            # 襲われて止まったのは、やり方の失敗ではない（行き詰まりで Gemini を呼ばない）
            self._skip_stall = True
        elif result.missed_by_chance:
            # 草から種が落ちなかった（8 回に 1 回ほど）: 進まないに数えない
            self._skip_stall = True
            self.consecutive_failures = 0
        else:
            self.consecutive_failures = 0 if result.ok else self.consecutive_failures + 1
        self.updated_at = _utc_now()
        return self.plan.charge(
            self.goal.objective_id if self.goal else None, self.goal.spec if self.goal else None
        )
