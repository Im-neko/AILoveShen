"""
書いた計画（中目標と小目標）の検査（docs/design/50 §19）。

LLM が書いた中目標を、世界に照らす前に形で確かめる。通らなければ理由を添えて書き直させる
（計画を書く `JobPlanner` と、目標の決定が中目標を書き直す `_decide_goal` の両方が使う）。
2026-09-30 の `mission.json` で見つかった食い違いだけを止める:

- 存在しない名前の建物 `built(house)`: 建物は大目標の条件になった名前だけが設計される。家そのものは
  名前なしの `built`。ブリッジは「まだない」と答えるので、いつまでも判定できず、小目標にもなれない
- 大目標の条件そのものを、最後でない中目標にする: 最後の中目標は大目標の条件を満たす段階でよい
  （計画の決まり）が、途中に同じ条件があると、大目標と中目標が同じ文で並んで進み具合も同じになる
- 同じ条件の中目標、同じ名前で条件の違う中目標が並ぶ（「石材の確保と工具作成」が 2 つ）
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Optional

from ailoveshen.domain.value_objects import GoalPredicate, GoalSpec, Job, Objective


def unregistered_builds(
    specs: Iterable[GoalSpec], objective: Optional[Objective], builds: Collection[str]
) -> list[GoalSpec]:
    """
    存在しない名前の `built(name)`。設計されるのは大目標の条件になった名前だけなので、その名前と
    登録済みの建物（`builds`）は除く。
    """
    known = set(builds) | {
        c.name
        for c in (objective.conditions if objective else ())
        if c.predicate == GoalPredicate.BUILT and c.name
    }
    found: dict[str, GoalSpec] = {}
    for spec in specs:
        if spec.predicate == GoalPredicate.BUILT and spec.name and spec.name not in known:
            found.setdefault(spec.describe(), spec)
    return list(found.values())


def check_plan(
    jobs: Sequence[Job],
    objective: Optional[Objective],
    builds: Optional[Collection[str]] = None,
) -> None:
    """
    中目標の並びを検査する。

    Args:
        jobs: 書いた中目標（順番どおり）
        objective: それを書く大目標（条件と済んだ中目標を見る。なければ、その分は見ない）
        builds: 登録済みの建物の名前（None: 分からないので、建物の名前は確かめない）

    Raises:
        ValueError: 直させる理由（英語。モデルに返す）
    """
    if builds is not None:
        missing = unregistered_builds((x for j in jobs for x in j.all_specs()), objective, builds)
        if missing:
            raise ValueError(
                f"{', '.join(s.describe() for s in missing)} names a build that does not exist, so it "
                "can never be judged or worked on: only a build that is one of the objective's own "
                "conditions gets designed. The house itself is built(), with no name. Write the "
                "materials and the steps as have / placed conditions instead"
            )
    done = list(objective.done_jobs) if objective is not None else []
    fresh = [j for j in jobs if not any(d.same_as(j) for d in done)]
    own = {c.describe() for c in (objective.conditions if objective else ())}
    for i, job in enumerate(fresh):
        if own and i < len(fresh) - 1 and any(s.describe() in own for s in job.specs):
            raise ValueError(
                f"job {job.title or job.describe()} is the objective's own condition "
                f"({', '.join(sorted(own))}) but it is not the last job: the objective and the job "
                "would read the same. Only the last job reaches the objective's conditions; put "
                "the preparation before it"
            )
    for i, job in enumerate(fresh):
        for other in fresh[i + 1 :]:
            if job.same_as(other):
                raise ValueError(f"two jobs have the same condition {job.describe()}: write it once")
            if job.title and job.title == other.title:
                raise ValueError(
                    f"two jobs are both named {job.title} ({job.describe()} and "
                    f"{other.describe()}): name each stage by what it reaches, or merge them"
                )
        taken = next((d for d in done if job.title and d.title == job.title), None)
        if taken is not None:
            raise ValueError(
                f"the job {job.title} ({job.describe()}) has the name of the job already done "
                f"({taken.describe()}): name each stage by what it reaches"
            )
