"""
次の小目標を、Gemini が書いた中目標の中から Jev に選ばせる（docs/design/26_steps_and_context.md）。

候補は、一番上の大目標の中目標のうちまだ済んでいないもの（ブリッジの `/check` で判定）と、今必要な
身を守る小目標（夜、夕方、空腹）。Jev は 1 回の `Choice` で 1 つ選ぶ。選べないとき（候補がない、
中目標が全部済んだのに大目標が済んでいない、確信度が低い、Jev が失敗した）は理由を返し、呼び出し側が
Gemini に回す。済んだかどうかは Jev に判定させない（ブリッジが世界から判定する）。
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Collection, Optional, Sequence

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import (
    CONDITION_PREDICATES,
    FastQuestion,
    FastQuestionKind,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    JobState,
    Step,
    Supply,
    SupplyTier,
)

# 食べ物の量は満腹度で決める（個数でなく。じゃがいも 8 個は 8、焼いた肉 8 個は 64）。目標は個数
# （`have(food, n)`）なので、足りない満腹度を 1 個あたりの見積もりで割って個数にする。見積もりが外れても、
# 次の切れ目で満腹度がまだ足りなければ、また出る（docs/design/84 §8）
FOOD_ITEM_POINTS = 5  # 生の肉（3）と焼いた肉（8）の間、パン（5）
FOOD_FULL = 20  # 満腹度の満タン
ASK_STREAMER = "rethink"  # 失敗の後に Jev が「Gemini に考え直させる」を選ぶときの答え


@dataclass(frozen=True)
class Option:
    """Jev に示す候補: 小目標、なぜそれか、何のためか（大目標の id。身を守るものは None）。"""

    spec: GoalSpec
    reason: str
    objective_id: Optional[str] = None
    # 中目標が束のとき（docs/design/44）: まだ済んでいない、一緒に追う条件と進め方
    together: tuple[GoalSpec, ...] = ()
    approach: str = ""
    # Jev に聞かずに選ぶ身を守る小目標（暗くて松明がない、松明を切らして今作れる。設計書 50 §14）
    forced: bool = False

    def describe(self) -> str:
        return " + ".join(s.describe() for s in (self.spec, *self.together))


@dataclass(frozen=True)
class ChosenGoal:
    """Jev が選んだ次の小目標。`objective_id` は中目標なら大目標、身を守るものなら None。"""

    spec: GoalSpec
    reason: str
    objective_id: Optional[str]
    confidence: float
    together: tuple[GoalSpec, ...] = ()
    approach: str = ""

    def describe(self) -> str:
        return " + ".join(s.describe() for s in (self.spec, *self.together))


@dataclass(frozen=True)
class NotChosen:
    """Jev では決められなかった理由（Gemini に回す）。"""

    why: str


# 手持ちのストック（2026-10-01、user: 「食料や松明はその時必要なぶんじゃなくて、ある程度手持ちに
# ストックするようにしてほしい」）。切らしてから集めに行くと、暗い所で動けなくなり、空腹で飢えた。
# 量は生活インフラの整い方で変える（`SupplyTier`、設定 `minecraft.supplies`、2026-10-02、docs/design/84）。
# TORCH_LOW はブリッジの知らせと同じ「もう危ない」本数（段階によらない）
TORCH_LOW = 4  # これより少なければ、暗い所で置けない: 切らしかけている
CHEST_SLOTS_USED = 20  # 持ち物のスロットがこれだけ埋まって、家にチェストがなければ、置く小目標を出す
MAX_HOME_CHESTS = 6  # 家に置くチェストの上限（ブロック数。ラージチェストは 2）
CHEST_FREE_MIN = 4  # 家のチェストの空きがこれ以下（か総スロットの CHEST_FREE_RATIO 以下）になったら増やす
CHEST_FREE_RATIO = 0.2
CHEST_DEFRAG_ENOUGH = 3  # まとめると、これだけ空くなら増やさない（先にまとめる）
SLOT_ITEMS = 64  # 1 スロットに入る数（16 個や 1 個のものは少なく見積もる）


def _count(inventory: dict, *names: str) -> int:
    return sum(int(inventory.get(n) or 0) for n in names)


def current_tier(
    tiers: Sequence[SupplyTier], done: Collection[str], has_mine: bool
) -> Optional[SupplyTier]:
    """着いている段階のうち、一覧で一番後ろのもの（どれにも着いていなければ None: ストックの小目標は出さない）。"""
    reached = [t for t in tiers if t.reached(done, has_mine)]
    return reached[-1] if reached else None


def _stored(obs: GameObservation, key: str) -> int:
    """家のチェストにある数（最後に開けたときの中身）。チェストがなければ 0。"""
    if not obs.chest_in_home:
        return 0
    return int((obs.state.get("supplies") or {}).get(f"{key}_stored") or 0)


def _reserve_options(
    obs: GameObservation,
    tier: SupplyTier,
    supply: Supply,
    item: str,
    label: str,
    carried: int,
    stored: int,
    *,
    have_items: Optional[int] = None,
    stored_items: Optional[int] = None,
    items_for=lambda n: n,
    show=str,
) -> list[Option]:
    """
    合計が足りているときの 2 つ: 出かける前にチェストから手に取る、手に持つぶんを超えるぶんをチェストに貯める。
    どちらも昼に、家のチェストがあるときだけ。強制はしない（Jev が中目標と比べて選ぶ）。

    `carried` / `stored` は段階の `Supply` と同じ単位（松明は本数、食べ物は満腹度）。目標は個数なので、
    単位が個数でないとき（食べ物）は、`have_items` / `stored_items`（今の個数）と `items_for`（その単位の
    量を個数にする）で目標の個数を出す。
    """
    if not obs.chest_in_home or obs.time_phase != "day":
        return []
    have_items = carried if have_items is None else have_items
    stored_items = stored if stored_items is None else stored_items
    out = []
    if obs.inside_home and stored > 0 and carried <= supply.carry // 2:
        count = have_items + min(stored_items, items_for(supply.carry - carried))
        out.append(
            Option(
                GoalSpec(GoalPredicate.HAVE, item=item, count=count),
                f"家のチェストに{label}が {show(stored)}: 出かける前に手に取っておく"
                f"（手持ち {show(carried)}、{tier.name} の段階では {show(supply.carry)} を持つ）",
            )
        )
    reserve = supply.stock - supply.carry
    if reserve > 0 and carried >= supply.carry and stored < reserve:
        out.append(
            Option(
                GoalSpec(GoalPredicate.STORED, item=item, count=stored_items + items_for(reserve - stored)),
                f"{label}を家のチェストに {show(reserve)} まで貯めておく（今 {show(stored)}、{tier.name} の段階の備蓄）",
            )
        )
    return out


def torch_options(obs: GameObservation, tier: Optional[SupplyTier]) -> list[Option]:
    """
    松明を多めに持つ（手持ちと家のチェストの合計が段階の `low` を下回ったら、手に `carry` 本まで）。暗い所では
    何も見えないので、暗くて松明がなければ引き返して作る。材料（石炭か木炭と、棒か板材・原木）が手元に
    あれば、作るだけなので Jev に聞かずに選ぶ。材料がなければ、集める小目標を身を守るものとして出す
    （暗くて持っていなければ聞かずに）。足りているときは、チェストから手に取る・チェストに貯める小目標を出す。
    """
    if tier is None or "inventory" not in obs.state:
        return []
    inventory = obs.state["inventory"] or {}
    supply = tier.torch
    torches = _count(inventory, "torch")
    stored = _stored(obs, "torch")
    # 深い地下は、暗くて松明がなければ引き返して作る（採掘場は松明がないと掘り進めない）
    dark = any("dark here" in n for n in obs.needs) or (obs.underground and torches == 0)
    fuel = _count(inventory, "coal", "charcoal")
    sticks = _count(inventory, "stick") + 2 * _count(inventory, "oak_planks", "birch_planks", "spruce_planks")
    sticks += 8 * _count(inventory, "oak_log", "birch_log", "spruce_log")
    crafts = min(fuel, sticks)  # 1 回（石炭か木炭 1 つと棒 1 本）で 4 本
    # 地下で松明が足りず、作る材料もない: 洞窟の中では石炭や棒が集まらないことが多いので、集めようとせず
    # 地上に出る（家のチェストにあれば、そこで取れる。2026-10-03: 深い地下で持ち物が空のまま、松明 128 本を集めようとした）
    if obs.underground and torches < TORCH_LOW and not crafts:
        have = f"、家のチェストに {stored} 本" if stored else ""
        return [
            Option(
                GoalSpec(GoalPredicate.AT_HOME),
                f"地下で松明が {torches} 本しかなく{have}、作る材料もない: 洞窟では石炭や棒が集まりにくい。地上に出て、家で松明を用意する",
                forced=True,
            )
        ]
    if torches + stored >= supply.low and not (dark and torches == 0):
        return _reserve_options(obs, tier, supply, "torch", "松明", torches, stored)
    # 暗くて松明がなく引き返すときは、備蓄の数まで作る（4 本では 40 m で尽きて、また止まって引き返した: 2026-10-03）
    want = supply.stock if dark and torches == 0 else supply.carry
    if crafts:
        target = min(want, torches + stored + 4 * crafts)
    elif stored:
        target = min(want, torches + stored)  # チェストにあるぶんを取りに行く
    else:
        target = supply.carry  # 材料がなければ、備蓄の数でなく、手に持つ数まで
    if dark and torches == 0:
        reason = "暗くて先が見えず、松明がない: 引き返して松明を作る（石炭か木炭と棒）"
    elif torches < TORCH_LOW:
        reason = f"松明が {torches} 本しかない: 暗い所で置けるよう、切らさず持つ"
    else:
        have = f"手持ち {torches} 本とチェスト {stored} 本" if stored else f"{torches} 本"
        reason = f"松明が {have}: {supply.low} 本を下回ったので、{supply.carry} 本まで多めに持っておく（{tier.name} の段階）"
    return [Option(GoalSpec(GoalPredicate.HAVE, item="torch", count=target), reason, forced=bool(crafts) or (dark and torches == 0))]


def _items_for(points: int) -> int:
    """満腹度 `points` ぶんの食べ物の個数の見積もり（1 個もいらなければ 0）。"""
    return math.ceil(points / FOOD_ITEM_POINTS) if points > 0 else 0


def _food(obs: GameObservation) -> tuple[int, int, int, int]:
    """手持ちの満腹度と個数、家のチェストの満腹度と個数。"""
    supplies = obs.state.get("supplies") or {}
    return (
        int(supplies.get("food_points") or 0),
        int(supplies.get("food") or 0),
        _stored(obs, "food_points"),
        _stored(obs, "food"),
    )


def food_options(obs: GameObservation, tier: Optional[SupplyTier]) -> list[Option]:
    """
    食べ物を多めに持つ（手持ちと家のチェストの満腹度の合計が段階の `low` を下回ったら、手に `carry` まで）。
    量は個数でなく満腹度（食べ物の種類で 1 個の回復量が 1 から 8 まで違う）。空腹になってから探すと、遠い牛
    まで走るぶんの空腹で食べ物の得が残らず、夜に外で飢えた（2026-10-01）。昼で、地下でないときだけ（夜・
    夕方は家へ、地下は掘る手を止めない）。空腹の食べ物集めは別（`hungry_option`）。1 個もなければ聞かずに
    選ぶ。足りているときは、チェストから手に取る・チェストに貯める小目標を出す。
    """
    supplies = obs.state.get("supplies") or {}
    if tier is None or "food_points" not in supplies or obs.underground or obs.time_phase != "day":
        return []
    if any("hunger" in n for n in obs.needs):
        return []
    supply = tier.food
    points, items, stored, stored_items = _food(obs)
    show = lambda n: f"満腹度 {n}"  # noqa: E731
    if points + stored >= supply.low:
        return _reserve_options(
            obs, tier, supply, "food", "食べ物", points, stored,
            have_items=items, stored_items=stored_items, items_for=_items_for, show=show,
        )
    if points + stored == 0:
        reason = "食べ物が 1 個もない: 空腹になる前に、昼のうちに集めておく"
    else:
        have = f"手持ち {show(points)}とチェスト {show(stored)}" if stored else show(points)
        reason = (
            f"食べ物が{have}しかない（満腹は 20、歩くと 40〜70 m で 1 減る）: 空腹になる前に、昼のうちに"
            f"{show(supply.carry)}ぶんまで集めておく（{tier.name} の段階）"
        )
    count = items + max(1, _items_for(supply.carry - points))
    return [Option(GoalSpec(GoalPredicate.HAVE, item="food", count=count), reason, forced=points + stored == 0)]


def hungry_option(obs: GameObservation, tier: Optional[SupplyTier]) -> Option:
    """
    お腹が空いたときの食べ物集め: 満タン（20）にしたうえで、段階の `low` に届くぶん（歩く分の余裕）。
    いま手に持っている個数に足す。
    """
    points, items, _, _ = _food(obs)
    want = max(FOOD_FULL, tier.food.low if tier is not None else 0)
    count = items + max(1, _items_for(want - points))
    return Option(
        GoalSpec(GoalPredicate.HAVE, item="food", count=count),
        f"お腹が空いたので食べ物を集める（満腹度 {obs.food}/{FOOD_FULL}、手持ち {points}）",
    )


def chest_options(obs: GameObservation) -> list[Option]:
    """
    家に預けるチェストがなく、持ち物が溜まってきたら、チェストを作って置く小目標を出す（強制はしない）。
    整地や採掘で土や石が溜まり続け、預ける先がなくて食料も拾えなくなった（2026-09-30: 土 447、丸石 320）。
    """
    if obs.has_home and obs.chest_in_home:
        return _more_chests(obs)
    if not obs.has_home or obs.chest_in_home or "inventory" not in obs.state:
        return []
    used = sum(math.ceil(int(n) / SLOT_ITEMS) for n in (obs.state["inventory"] or {}).values() if n)
    if used < CHEST_SLOTS_USED:
        return []
    return [
        Option(
            GoalSpec(GoalPredicate.PLACED, item="chest", where="home"),
            f"持ち物が {used}/36 スロット埋まってきたのに、預けるチェストが家にない: チェストを作って家に置く",
        )
    ]


def _more_chests(obs: GameObservation) -> list[Option]:
    """
    家のチェストがいっぱいになってきたら、チェストを増やす小目標を出す（強制はしない。2026-10-04, user:
    「チェストいっぱいになってきたらチェスト増やしてほしい」）。まとめれば（デフラグ）空くときは、先にそちら。
    """
    if obs.chest_free is None or obs.chest_slots is None or obs.chest_blocks is None:
        return []
    if obs.chest_blocks >= MAX_HOME_CHESTS or (obs.chest_saving or 0) >= CHEST_DEFRAG_ENOUGH:
        return []
    if obs.chest_free > max(CHEST_FREE_MIN, int(obs.chest_slots * CHEST_FREE_RATIO)):
        return []
    return [
        Option(
            GoalSpec(GoalPredicate.PLACED, item="chest", where="home", count=obs.chest_blocks + 1),
            f"家のチェストが埋まってきた（空き {obs.chest_free}/{obs.chest_slots} スロット）: "
            "チェストをもう 1 つ作って家に置く",
        )
    ]


def survival_options(obs: GameObservation, tier: Optional[SupplyTier] = None) -> list[Option]:
    """
    今必要な身を守る小目標（夜を越す、夕方は家に入る、空腹なら食料、松明と食料のストック）。夜の越し方は決めつけない
    （家、近くのベッド、持っているベッド。2026-09-26）。地下にいるなら夜の選択肢は出さない
    （地下は夜も昼も同じ暗さ: そのまま作業を続けてよい）。
    """
    out = []
    # 夜と、家がある夕方は決まった手順（Jev にも聞かない）: 夜は夜を越す、夕方は家に入る。腹ぺこなら
    # 食べる小目標と比べるので決めつけない。目標ごとに聞くと数秒〜数十秒かかり、その間に外が暗くなる
    # （2026-09-30: 時間帯の変わり目の切り替えが 32 回、Jev が迷って Gemini に回るのが多かった）
    routine = not any("hunger" in n for n in obs.needs)
    if obs.time_phase == "night" and not obs.underground:
        out.append(
            Option(
                GoalSpec(GoalPredicate.THROUGH_NIGHT),
                "夜は危ないので安全に夜を越す（家、近くのベッド、持っているベッドで寝る）",
                # 決まった手順は「安全な所へ行く」まで。家の中では決めつけない（家の中でできる作業や、
                # 敵を倒しに出る cleared を選べるように）
                forced=routine and not obs.inside_home,
            )
        )
    elif obs.has_home and obs.time_phase == "dusk" and not obs.underground:
        if obs.inside_home:
            # もう家の中: at_home は済んでいて選べない。外の作業を選ばせず、夜が明けるまで家で過ごす
            # （2026-09-30 18:08: 死んで家で生き返った夕方、at_home が済みで差し戻され続け、3 回目に
            # 外の作業が通って外へ出て、また死んだ）
            out.append(
                Option(
                    GoalSpec(GoalPredicate.THROUGH_NIGHT),
                    "夕方で、もう家の中: 外に出ず、夜が明けるまで家で過ごす（眠れる時間になればベッドで寝る）",
                    forced=routine,
                )
            )
        else:
            out.append(Option(GoalSpec(GoalPredicate.AT_HOME), "暗くなる前に家に入る", forced=routine))
    if obs.time_phase == "dusk" and not obs.underground and not obs.inside_home:
        out.append(
            Option(
                GoalSpec(GoalPredicate.THROUGH_NIGHT),
                "家が遠ければ、その場で夜を越す（近くのベッドか、持っているベッドで寝る）",
            )
        )
    # 夜、家のそばに敵がいて眠れないとき: 武器があれば、倒しに出るのも選択肢（持ち物は先にチェストへ。
    # ブリッジが体力と武器を確かめる。2026-09-27: 夜の行動が制約されすぎ）
    if obs.has_home and obs.time_phase == "night" and _armed(obs) and _hostiles_near(obs):
        out.append(
            Option(
                GoalSpec(GoalPredicate.CLEARED),
                "敵が家のそばにいて眠れないので、持ち物をチェストに預け、武器を持って倒しに出る",
            )
        )
    if any("hunger" in n for n in obs.needs):
        # 地下で空腹、食べ物を 1 個も持っていない: 洞窟には動物がいない（牛や豚は地上）。探しに歩かず、地上に出る
        # （家のチェストに食べ物があればそこで取れる。2026-10-03: 地下で「メロンを探す」を続けて地上に戻れなかった）
        if obs.underground and obs.has_home and _food(obs)[1] == 0:
            out.append(
                Option(
                    GoalSpec(GoalPredicate.AT_HOME),
                    "地下で空腹なのに食べ物がない: 洞窟には動物がいない。地上に出て、家のチェストの食べ物か、地上の動物を探す",
                    forced=True,
                )
            )
        else:
            out.append(hungry_option(obs, tier))
    # 暗くて松明がない・作れる（forced）は、夜の手順より先（暗いと外の行動は止まるので、松明が先）
    torches = torch_options(obs, tier)
    food = food_options(obs, tier)
    return (
        [o for o in torches if o.forced]
        + [o for o in food if o.forced]
        + out
        + [o for o in torches if not o.forced]
        + [o for o in food if not o.forced]
        + chest_options(obs)
    )


def _option(job, left: list[GoalSpec], step: Optional[GoalSpec], objective_id: str) -> Option:
    """中目標の選択肢。束でない have で次の一歩があれば、その一歩が小目標（中目標の理由に、何のための一歩かを添える）。"""
    if step is not None and len(left) == 1 and not step.same_kind(left[0]):
        return Option(step, f"{job.reason}（{left[0].describe()} の一歩）", objective_id)
    return Option(left[0], job.reason, objective_id, tuple(left[1:]), job.approach if left[1:] else "")


def _planned_option(job, step: Optional[Step], objective_id: str) -> Optional[Option]:
    """
    中目標の計画の次の小目標の選択肢（理由は「中目標の名前: 小目標の理由」）。計画の最後の小目標が
    中目標そのものと同じ条件なら None（束や解決器の次の一歩を使う今までの選択肢のほうがよい）。
    """
    if step is None or step.spec.describe() == job.spec.describe():
        return None
    label = job.title or job.reason
    return Option(step.spec, f"{label}: {step.reason}" if step.reason else label, objective_id)


def _armed(obs: GameObservation) -> bool:
    return any(
        name.endswith(("_sword", "_axe")) and n > 0
        for name, n in (obs.state.get("inventory") or {}).items()
    )


def _hostiles_near(obs: GameObservation, radius: float = 16) -> bool:
    return any(
        m.get("hostile") and (m.get("distance_m") or 99) <= radius
        for m in obs.state.get("mobs") or []
    )


class GoalChooser:
    """一番上の大目標の中目標と身を守る小目標から、次の小目標を Jev に選ばせる。"""

    def __init__(
        self,
        judge: IFastJudge,
        bridge: IMinecraftBridge,
        min_confidence: float = 0.4,
        timeout_seconds: float = 10.0,
        recorder: Optional[IWatchRecorder] = None,
        supply_tiers: Sequence[SupplyTier] = (),
    ) -> None:
        """
        Args:
            judge: Jev
            bridge: 中目標が済んだかを判定する（`/check`）
            min_confidence: これより低ければ Gemini に回す
            timeout_seconds: Jev の答えを待つ上限
            recorder: 選択の記録（候補、選んだもの、確信度、Gemini に回した理由）
            supply_tiers: 松明と食べ物のストックの段階（設定 `minecraft.supplies`。空ならストックの小目標は出さない）
        """
        self._judge = judge
        self._bridge = bridge
        self._min_confidence = min_confidence
        self._timeout = timeout_seconds
        self._recorder = recorder
        self._tiers = tuple(supply_tiers)

    async def choose(
        self,
        session: PlaySession,
        obs: GameObservation,
        ended_because: str,
        avoid: Optional[GoalSpec] = None,
    ) -> ChosenGoal | NotChosen:
        """
        次の小目標を選ぶ。選べなければ理由（NotChosen）。

        `avoid` は行き詰まった・進まなかった小目標（docs/design/34 §7）: 同じ種類の中目標は選択肢から
        外し（同じやり方を繰り返すには Gemini の分析と助言が要る）、「Gemini に考え直させる」を足す。
        """
        current = session.plan.current
        if current is None or not current.jobs:
            return self._not("the top objective has no jobs")
        # 一度済みにした中目標は選ばない（あとで材料を使って条件が崩れても。docs/design/50 §4）
        open_jobs = current.open_jobs
        mines = int((obs.state.get("supplies") or {}).get("mines") or 0)
        tier = current_tier(self._tiers, session.plan.playbook_done, mines > 0)
        needed = survival_options(obs, tier)
        flat = [spec for s in open_jobs for spec in s.specs]
        # 計画の小目標（docs/design/50 §18）: まだ済みにしていないものを、同じ条件は 1 つにして一緒に判定する
        planned = {x.key: x.spec for s in open_jobs for x in s.pending_steps()}
        # 身を守る小目標も、もう済んでいれば出さない（夕方にもう家の中、など）。ブリッジが条件として判定
        # できるのは CONDITION_PREDICATES だけ（through_night / at_home / cleared を送ると、判定全体が
        # 断られ、夜と夕方の切り替えがいつも Gemini に回った: 2026-09-29 から 90 回）。それ以外は、
        # 家の中にいる at_home だけが済んでいる
        checkable = [o.spec for o in needed if o.spec.predicate in CONDITION_PREDICATES]
        try:
            statuses = await self._bridge.check(flat + list(planned.values()) + checkable)
        except AILoveShenError as e:
            return self._not(f"the jobs cannot be judged: {e}")
        n_flat, n_planned = len(flat), len(planned)
        step_statuses = statuses[n_flat : n_flat + n_planned]
        judged_survival = dict(zip(checkable, statuses[n_flat + n_planned :]))
        survival_met = [
            judged_survival[o.spec].met
            if o.spec in judged_survival
            else o.spec.predicate == GoalPredicate.AT_HOME and obs.inside_home
            for o in needed
        ]
        statuses = statuses[:n_flat]
        met_steps = {k: st.met for k, st in zip(planned, step_statuses)}
        session.plan.judge_steps(
            current.id,
            {s.id: [k for k in s.step_keys() if met_steps.get(k)] for s in open_jobs},
        )
        # 中目標ごとに、まだ済んでいない条件（束なら、済んだ条件は外して追う）
        unmet: list[list[GoalSpec]] = []
        steps: list[Optional[GoalSpec]] = []  # 中目標ごとの、次の一歩（小目標）
        plan_steps: list[Optional[Step]] = []  # 中目標ごとの、計画の次の小目標
        i = 0
        for s in open_jobs:
            judged = statuses[i : i + len(s.specs)]
            unmet.append([sp for sp, st in zip(s.specs, judged) if not st.met])
            # 束でない have の中目標は、集めに出る材料があれば、その一歩が小目標（docs/design/50 §13）
            steps.append(judged[0].next_step if len(s.specs) == 1 else None)
            # 計画があれば、その次の小目標が先（LLM が前提まで並べた。docs/design/50 §18）
            plan_steps.append(s.next_step(met_steps))
            i += len(s.specs)
        finished = [(s.id, True, s.progress) for s, left in zip(open_jobs, unmet) if not left]
        if finished:
            session.plan.judge_jobs(current.id, finished)  # 満たした中目標は済みのままにする
        survival = [o for o, met in zip(needed, survival_met) if not met]
        options = [
            _planned_option(s, plan_step, current.id) or _option(s, left, step, current.id)
            for s, left, step, plan_step in zip(open_jobs, unmet, steps, plan_steps)
            if left
        ] + survival
        if not options:
            return self._not("all the jobs are done but the objective is not")
        if avoid is not None:
            options = [o for o in options if not o.spec.same_kind(avoid)]
            if not options:
                return self._not(f"no other job than {avoid.describe()} after it failed")
        state = self._state(session, obs, ended_because, current.jobs, open_jobs, unmet)
        # 暗くて松明がない、松明を切らして今作れる: 聞かずに選ぶ（失敗の後は聞く）
        forced = [o for o in survival if o.forced]
        if forced and avoid is None:
            return self._chosen(state, forced, 0, 1.0)
        if len(options) == 1 and avoid is None:
            return self._chosen(state, options, 0, 1.0)
        labels = [chr(ord("A") + i) for i in range(len(options))]
        criteria = {label: f"{o.describe()}: {o.reason}" for label, o in zip(labels, options)}
        instructions = (
            "Which small goal should the streamer work on next? Follow the jobs in order "
            "unless another open job is clearly better now (it is nearer, or the inventory "
            "already has what it needs). Choose a survival goal when the night, danger or "
            "hunger requires it."
        )
        if avoid is not None:
            criteria[ASK_STREAMER] = (
                "none of these: the streamer should look into why the last goal failed and "
                "rethink the plan"
            )
            instructions = (
                f"The last small goal {avoid.describe()} did not work out ({ended_because}). "
                "Should the streamer move on to another open job now, or stop and rethink? "
                + instructions
            )
        question = FastQuestion(
            name="next_goal",
            kind=FastQuestionKind.CHOICE,
            instructions=instructions,
            criteria=criteria,
        )
        try:
            verdict = await asyncio.wait_for(
                self._judge.ask(state, (question,)), timeout=self._timeout
            )
        except (AILoveShenError, TimeoutError) as e:
            return self._not(f"Jev did not answer ({type(e).__name__})", state, options)
        answer = verdict.answers.get("next_goal")
        if answer is not None and answer.value == ASK_STREAMER:
            return self._not("Jev chose to have the streamer rethink after the failure", state, options)
        if answer is None or answer.value not in labels:
            return self._not("Jev gave no usable answer", state, options)
        if answer.confidence < self._min_confidence:
            return self._not(
                f"Jev was unsure ({answer.confidence:.2f} < {self._min_confidence})",
                state,
                options,
            )
        return self._chosen(state, options, labels.index(answer.value), answer.confidence)

    async def close(self) -> None:
        await self._judge.close()

    def _chosen(self, state, options: list[Option], index: int, confidence: float) -> ChosenGoal:
        self._record(state, options, chosen=index, confidence=confidence)
        o = options[index]
        return ChosenGoal(o.spec, o.reason, o.objective_id, confidence, o.together, o.approach)

    def _not(self, why: str, state=None, options=()) -> NotChosen:
        if state is not None:
            self._record(state, options, why=why)
        return NotChosen(why)

    @staticmethod
    def _state(session, obs, ended_because, all_jobs, open_jobs, unmet) -> dict[str, Any]:
        current = session.plan.current
        last = session.goal.describe() if session.goal else None
        left_of = {s.id: left for s, left in zip(open_jobs, unmet)}
        return {
            "objective": current.title if current else None,
            "jobs": [
                f"{s.describe()}: "
                + (
                    "done"
                    if s.state == JobState.DONE or not left_of[s.id]
                    else "not yet"
                    if len(left_of[s.id]) == len(s.specs)
                    else "not yet: " + ", ".join(x.describe() for x in left_of[s.id])
                )
                for s in all_jobs
            ],
            "last_goal": f"{last} ({ended_because})" if last else None,
            "time": obs.time_phase,
            "health": obs.health,
            "food": obs.food,
            "in_home": obs.inside_home,
            "needs": [n for n in obs.needs if n != "none"],
            "inventory": obs.state.get("inventory", {}),
        }

    def _record(self, state, options, chosen=None, confidence=None, why="") -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record(
                {
                    "state": state,
                    "options": [o.describe() for o in options],
                    "chosen": options[chosen].describe() if chosen is not None else None,
                    "confidence": round(confidence, 3) if confidence is not None else None,
                    "to_gemini": why or None,
                }
            )
        except Exception as e:  # noqa: BLE001 - 記録の失敗で選択を止めない
            logger.warning(f"小目標の選択を記録できなかった: {e}")
