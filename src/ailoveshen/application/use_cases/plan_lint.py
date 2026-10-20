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
- 中目標のほかの条件の材料にない物を集める小目標・前提（`unneeded_materials`、設計書 67）: 「チェストを
  作るための鉄インゴット」と書かれた（チェストは板材だけで作れる）。ブリッジが返す材料の木と照らす
- 大目標につながらない中目標（`aimless_jobs`、設計書 50 §20）: 探すだけの大目標の計画に、ドアの製作・整地・
  作る物の材料が並んだ。中目標にできるのは備え（食料・明かり・武器と防具・道具・乗り物・ベッド）と、材料の木が
  ある大目標ではその木に出る物だけ
- 存在しない名前の施設 `facility(mine_7)`: 建物と同じ。採掘場は、その鉱石を要る手が作って掘る。2026-10-17 の 3.5 時間で、
  採掘場を中目標に書いた計画が 4 回あり、小目標にしようとして 47 回断られた
- もう建ててある建物の `built`（`already_built`、設計書 50 §20）: 「夜に家の中にいる」の代わりに `built()` を
  中目標にして、書いた瞬間に済んだ（at_home は計画の条件にない）
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Optional

from ailoveshen.domain.value_objects import GoalPredicate, GoalSpec, Job, MaterialTree, Objective


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


def unregistered_facilities(
    specs: Iterable[GoalSpec], objective: Optional[Objective], facilities: Collection[str]
) -> list[GoalSpec]:
    """
    存在しない名前の `facility(name)`。設計されるのは大目標の条件になった名前だけなので、その名前と
    登録済みの施設（`facilities`）は除く（建物の `unregistered_builds` と同じ）。
    """
    known = set(facilities) | {
        c.name
        for c in (objective.conditions if objective else ())
        if c.predicate == GoalPredicate.FACILITY and c.name
    }
    found: dict[str, GoalSpec] = {}
    for spec in specs:
        if spec.predicate == GoalPredicate.FACILITY and spec.name and spec.name not in known:
            found.setdefault(spec.describe(), spec)
    return list(found.values())


NAMES_SHOWN = 10  # 材料の木を返すとき、見せるアイテムの数

# 材料の木も、作る物もない大目標（探すだけ、戦うだけ）: 中目標にできるのは、体や身の安全のための備えだけ
_NO_MATERIALS = frozenset(
    {
        GoalPredicate.SURVEYED,
        GoalPredicate.EXPLORED,
        GoalPredicate.CLEARED,
        GoalPredicate.AT_HOME,
        GoalPredicate.THROUGH_NIGHT,
    }
)
# 備えの種類 → その品目（名前の正規表現）。遠出の食料・明かり・武器と防具・道具・乗り物・ベッド
_PREPARATIONS = {
    "food": re.compile(
        r"^(food|bread|apple|carrot|potato|baked_potato|beetroot|melon_slice|sweet_berries|cooked_\w+|"
        r"beef|porkchop|mutton|chicken|rabbit|cod|salmon)$"
    ),
    "light": re.compile(r"^(torch|lantern|coal|charcoal)$"),
    "weapon or armor": re.compile(
        r"^(\w+_sword|bow|crossbow|arrow|shield|\w+_(helmet|chestplate|leggings|boots))$"
    ),
    "tool": re.compile(r"^(\w+_(pickaxe|axe|shovel|hoe)|bucket|shears|fishing_rod)$"),
    "transport": re.compile(r"^(boat|\w+_boat)$"),
    "bed": re.compile(r"^(bed|\w+_bed)$"),
}


def preparation_of(spec: GoalSpec) -> Optional[str]:
    """
    備え（食料・明かり・武器と防具・道具・乗り物・ベッド）の条件なら、その種類（違えば None）。
    `lit` は明かり。`have` `placed` `stored` は品目で見る。
    """
    if spec.predicate == GoalPredicate.LIT:
        return "light"
    if spec.predicate not in (GoalPredicate.HAVE, GoalPredicate.PLACED, GoalPredicate.STORED):
        return None
    item = spec.item or ""
    return next((kind for kind, pattern in _PREPARATIONS.items() if pattern.match(item)), None)


def aimless_jobs(
    jobs: Iterable[Job],
    objective: Optional[Objective],
    trees: Optional[Mapping[str, MaterialTree]] = None,
) -> str:
    """
    大目標のどれにもつながらない中目標の理由（なければ空）。書いた LLM の的外れ（探す大目標の計画に
    「ドアの製作」や整地、家のある家の「ドア」）を、世界の事実で止める（設計書 50 §20）。

    - 探すだけの大目標（`_NO_MATERIALS` の条件だけ）: 中目標は備え（食料・明かり・武器と防具・道具・乗り物・
      ベッド）だけ。整地や、作る物の材料は大目標に関係がない
    - 材料の木がある大目標（`trees` に、条件のどれも木がある）: `have` `placed` `stored` の中目標の物は、
      大目標の材料の木に出るか、備えのどれか。ほかの条件（畑、建物、整地）が 1 つでもあれば、何が要るか
      分からないので確かめない
    """
    if objective is None or not objective.conditions:
        return ""
    conditions = objective.conditions
    prep = ", ".join(_PREPARATIONS)
    if all(c.predicate in _NO_MATERIALS for c in conditions):
        for job in jobs:
            for spec in job.specs:
                if preparation_of(spec) is None:
                    return (
                        f"the job {job.title or job.describe()} ({spec.describe()}) does not lead to "
                        f"the objective {', '.join(c.describe() for c in conditions)}: this objective "
                        "builds nothing, so a job is only a preparation for the trip or the fight "
                        f"({prep}). Leave it out; the trips themselves are chosen as small goals"
                    )
        return ""
    if trees is None:
        return ""
    own = [trees.get(c.describe()) for c in conditions]
    if any(t is None for t in own):
        return ""
    used = {name for t in own if t for name in t.all}
    for job in jobs:
        for spec in job.specs:
            if spec.predicate not in (GoalPredicate.HAVE, GoalPredicate.PLACED, GoalPredicate.STORED):
                continue
            if preparation_of(spec) is not None:
                continue
            mine = trees.get(spec.describe())
            if mine is not None and not (used & set(mine.root)):
                return (
                    f"the job {job.title or job.describe()} ({spec.describe()}) is not part of what "
                    f"the objective needs ({', '.join(sorted(used)[:NAMES_SHOWN])}) and is not a "
                    f"preparation ({prep}). Leave it out, or write what the objective really uses"
                )
    return ""


def already_built(jobs: Sequence[Job], own: Collection[str], met: Mapping[str, bool]) -> str:
    """
    もう建ててある建物の `built` を書いた中目標・小目標の理由（なければ空）。建物は一度建てば消えないので、
    書いた瞬間に済んで何もしない。夜に家の中にいる・家で待つの代わりに書かれていた（設計書 50 §20）。
    大目標自身の条件は、その大目標を書く所なので除く。
    """
    for job in jobs:
        for spec in job.all_specs():
            key = spec.describe()
            if spec.predicate == GoalPredicate.BUILT and key not in own and met.get(key):
                return (
                    f"{key} already holds (the building is built), so a job or step with it is done "
                    "the moment it is written and does nothing. Leave it out. Spending the night "
                    "or waiting inside is not a job: the survival goals do that by themselves. If "
                    "you meant a door, bed, chest or light, write that as a have / placed condition"
                )
    return ""


def _short(trees: Iterable[Optional[MaterialTree]]) -> Optional[set[str]]:
    """木のどれかに、足りない物（`missing`）が分からないものがあれば None。"""
    out: set[str] = set()
    for t in trees:
        if t is None or t.missing is None:
            return None
        out |= set(t.missing)
    return out


def unneeded_materials(
    jobs: Iterable[Job],
    objective: Optional[Objective],
    trees: Mapping[str, MaterialTree],
) -> Optional[str]:
    """
    中目標のほかの条件のどの材料の木にも出てこない物を、`have` で集める小目標・前提（最初の 1 つの説明）。

    `trees` は条件の書き方（`describe()`）から材料の木。ほかの条件に木がないもの（畑、整地、施設、建物…）が
    1 つでもある中目標は、何が要るかわからないので確かめない（間違いを止めるだけで、正しい計画を止めない）。
    大目標の条件は、木があるものだけ材料に足す。
    """
    extra = [
        t
        for c in (objective.conditions if objective else ())
        if (t := trees.get(c.describe())) is not None
    ]
    for job in jobs:
        specs = job.all_specs()
        # 疑うのは小目標と前提だけ。中目標の条件そのものは、その段階が届く先（何のためかを問わない）
        target = {s.describe() for s in job.specs}
        steps = [x.spec for step in job.steps for x in step.walk() if x.key not in target]
        for spec in steps:
            mine = trees.get(spec.describe()) if spec.predicate == GoalPredicate.HAVE else None
            others = [s for s in specs if s.describe() != spec.describe()]
            if mine is None or not others:
                continue
            others_trees = [trees.get(s.describe()) for s in others]
            if any(t is None for t in others_trees):
                continue
            used = {name for t in (*others_trees, *extra) if t for name in t.all}
            if used & set(mine.root):
                # 材料の木にはある。持ち物を数えて、まだ足りない物の中にもなければ、もう持っている（板材と棒を持って
                # いるのに、木のツルハシの前に原木を集める計画: 2026-10-07）。足りない物が分からなければ確かめない
                short = _short(others_trees)
                if short is None or short & set(mine.root):
                    continue
                return (
                    f"{spec.describe()} is not missing: the rest of the job needs "
                    f"{', '.join(sorted(short)[:NAMES_SHOWN]) or 'nothing more'} and you already carry what "
                    f"its recipe uses. List what the recipe uses directly, subtract what you carry (the "
                    f"inventory in the state), and write steps only for what is still short, nearest the "
                    f"goal first. Remove it"
                )
            needs = "; ".join(
                f"{s.describe()} needs {', '.join(t.all[:NAMES_SHOWN])}"
                for s, t in zip(others, others_trees, strict=True)
                if t
            )
            return (
                f"{spec.describe()} is not needed for the rest of the job "
                f"{job.title or job.describe()}: {spec.item} is not part of what the other conditions "
                f"of the job need ({needs}). Do not add materials from guesswork: write only what the "
                f"recipe of the item uses. Remove it, or write the condition that really needs "
                f"{spec.item}"
            )
    return None


def own_condition_last(jobs: Sequence, objective) -> list:
    """
    大目標の条件そのものの中目標を最後に回す（その 1 つだけのとき）。

    最後の中目標だけが大目標の条件に届くという規則を、モデルは何度書き直しても守れないことがある
    （2026-10-01: 街の段階 2 の `have(food, 16)` の計画を 3 回差し戻し、書けないまま 2 回、約 40 秒ずつ
    使った）。並べ替えるだけで済むので、差し戻さずに直す。条件に当たる中目標が複数あれば、並べ替えても
    規則に合わないので、そのまま（差し戻す）。
    """
    own = {c.describe() for c in (objective.conditions if objective else ())}
    hits = [j for j in jobs if own and any(s.describe() in own for s in j.specs)]
    if len(hits) != 1:
        return list(jobs)
    return [j for j in jobs if j is not hits[0]] + hits


def check_plan(
    jobs: Sequence[Job],
    objective: Optional[Objective],
    builds: Optional[Collection[str]] = None,
    trees: Optional[Mapping[str, MaterialTree]] = None,
    met: Optional[Mapping[str, bool]] = None,
    facilities: Optional[Collection[str]] = None,
) -> None:
    """
    中目標の並びを検査する。

    Args:
        jobs: 書いた中目標（順番どおり）
        objective: それを書く大目標（条件と済んだ中目標を見る。なければ、その分は見ない）
        builds: 登録済みの建物の名前（None: 分からないので、建物の名前は確かめない）
        trees: 条件の書き方から材料の木（None: 分からないので、材料は確かめない）
        met: 条件の書き方から、今もう満たしているか（None: 分からないので、確かめない）
        facilities: 登録済みの施設の名前（None: 分からないので、施設の名前は確かめない）

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
    if facilities is not None:
        missing = unregistered_facilities((x for j in jobs for x in j.all_specs()), objective, facilities)
        if missing:
            raise ValueError(
                f"{', '.join(s.describe() for s in missing)} names a facility that does not exist, so it "
                "can never be judged or worked on: only a facility that is one of the objective's own "
                "conditions gets designed. A mine is not a step of its own: it is made and dug by the "
                "step that needs its ore (write have(raw_iron, n) or have(diamond, n); the bridge offers "
                "'make a mine' for it). Write the materials and the steps as have / placed conditions instead"
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
    if met:
        why = already_built(fresh, own, met)
        if why:
            raise ValueError(why)
    why = aimless_jobs(fresh, objective, trees)
    if why:
        raise ValueError(why)
    if trees:
        why = unneeded_materials(fresh, objective, trees)
        if why:
            raise ValueError(why)
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


# 畑・植林場を作る大目標の題名（採石・採掘場は入れない: 掘る場所はブリッジが自分で設計し、石や鉱石を集める
# 大目標の題名に「採石」と書くのは普通のこと）
_INFRASTRUCTURE_TITLE = re.compile(r"(畑|農場|農園|植林|果樹園|farm|grove|orchard|plantation)", re.IGNORECASE)
_INFRASTRUCTURE_CONDITIONS = (GoalPredicate.FACILITY, GoalPredicate.FARMED, GoalPredicate.PLANTED)


def fake_infrastructure(title: str, conditions: Iterable[GoalSpec]) -> str:
    """
    畑や植林場を作ると題名に書いたのに、条件にそれが入っていないとき、直し方（なければ空）。

    2026-10-01: 「小麦畑を作る」が have(wheat_seeds, 4) と have(hoe, 1) だけで書かれ、木のクワを作った
    時点で達成になった。畑は 1 マスも耕されていなかった。
    """
    if not _INFRASTRUCTURE_TITLE.search(title):
        return ""
    if any(c.predicate in _INFRASTRUCTURE_CONDITIONS for c in conditions):
        return ""
    return (
        f"the objective '{title}' is about building a farm or a tree farm, but none of its conditions is "
        "facility(<new name>) (the farm itself, designed on the town map), farmed(...) or planted(...): "
        "have(...) items alone would finish it with no farm. Put facility(<new name>) in its conditions "
        "(seeds, hoe and the rest go in its jobs)"
    )
