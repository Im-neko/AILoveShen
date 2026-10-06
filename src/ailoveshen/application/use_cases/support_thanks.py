"""配信への応援（フォロー、サブスク、ギフト、ビッツ、レイド）へのお礼の一言（docs/design/71_support_thanks.md）。

LLM は呼ばない: 応援は待たせず、決まった言い方の中から選ぶ（同じ言い方が続かないよう、直前と違うものを選ぶ）。
名前の読みは、読み上げの前に別で当てる（NameReadings）ので、ここでは表示名のまま入れる。
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from ailoveshen.domain.value_objects import SupportEvent, SupportKind

# フォローをまとめて言うとき、名前を呼ぶのはこの人数まで（あとは「ほか N 人」）
NAMED_FOLLOWERS = 3

FOLLOW = (
    "{names}、フォローありがとう！これからよろしくね",
    "わあ、{names}、フォローしてくれたんだ。ありがとう、嬉しい！",
    "{names}、フォローありがとう。来てくれて嬉しいな、ゆっくりしていってね",
)
FOLLOW_MANY = (
    "{names}、フォローありがとう！たくさん来てくれて嬉しいな",
    "わ、{names}、いっぺんにフォローありがとう！",
)
SUBSCRIBE = (
    "{name}さん、サブスクありがとう！めちゃくちゃ嬉しい！",
    "{name}さん、サブスクしてくれたの？ありがとう、がんばるね！",
)
SUBSCRIBE_BIG = (
    "{name}さん、Tier {tier} のサブスクありがとう！すごく嬉しい、大事にするね！",
)
RESUBSCRIBE = (
    "{name}さん、{months}か月目のサブスクありがとう！ずっと応援してくれて嬉しいよ",
    "{name}さん、{months}か月も続けてくれてありがとう！これからもよろしくね",
)
GIFT = (
    "{name}さん、サブスクを{amount}人分もプレゼントしてくれてありがとう！みんなも{name}さんにお礼言ってね",
    "わあ、{name}さんが{amount}人にサブスクをプレゼントしてくれた！ありがとう！",
)
GIFT_ONE = (
    "{name}さん、{recipient}さんにサブスクをプレゼントしてくれてありがとう！",
    "{recipient}さん、{name}さんからサブスクのプレゼントだよ！{name}さんもありがとう！",
)
CHEER = (
    "{name}さん、{amount}ビッツありがとう！",
    "{name}さん、ビッツを{amount}も投げてくれてありがとう、嬉しい！",
)
CHEER_SMALL = ("{name}さん、ビッツありがとう！",)
RAID = (
    "{name}さんが{amount}人でレイドに来てくれた！みんな、いらっしゃい！ありがとう、{name}さん！",
    "わあ、{name}さんのレイドだ！{amount}人も来てくれてありがとう、ゆっくりしていってね！",
)
RAID_SMALL = ("{name}さんがレイドに来てくれた！いらっしゃい、ありがとう！",)

BIG_CHEER = 100
BIG_RAID = 2


def names_of(events: Sequence[SupportEvent]) -> str:
    """「A さんと B さん」「A さん、B さん、C さんほか 2 人」。"""
    names = [e.user_name for e in events]
    shown = names[:NAMED_FOLLOWERS]
    rest = len(names) - len(shown)
    text = "、".join(f"{n}さん" for n in shown)
    if len(shown) == 2 and rest == 0:
        text = f"{shown[0]}さんと{shown[1]}さん"
    return f"{text}ほか{rest}人" if rest else text


def thanks_for(
    events: Sequence[SupportEvent],
    last: str = "",
    rng: random.Random | None = None,
) -> str:
    """
    お礼の一言。events は同じ種類のフォローをまとめたもの、それ以外は 1 件。

    Args:
        last: 直前に言ったお礼（あれば、違う言い方を選ぶ）
        rng: 選び方（テストで固定する）

    Raises:
        ValueError: events が空のとき。
    """
    if not events:
        raise ValueError("there is nothing to thank for")
    rng = rng or random
    first = events[0]
    if first.kind == SupportKind.FOLLOW:
        templates = FOLLOW if len(events) == 1 else FOLLOW_MANY
        return _pick(templates, {"names": names_of(events)}, last, rng)
    fields = {
        "name": first.user_name,
        "amount": first.amount,
        "months": first.months,
        "tier": first.tier,
        "recipient": first.recipient,
    }
    if first.kind == SupportKind.SUBSCRIBE:
        templates = SUBSCRIBE_BIG if first.tier >= 2 else SUBSCRIBE
    elif first.kind == SupportKind.RESUBSCRIBE:
        templates = RESUBSCRIBE
    elif first.kind == SupportKind.GIFT:
        templates = GIFT if first.amount > 1 or not first.recipient else GIFT_ONE
    elif first.kind == SupportKind.CHEER:
        templates = CHEER if first.amount >= BIG_CHEER else CHEER_SMALL
    else:
        templates = RAID if first.amount >= BIG_RAID else RAID_SMALL
    return _pick(templates, fields, last, rng)


def _pick(templates: Sequence[str], fields: dict[str, object], last: str, rng: random.Random) -> str:
    """言い方を選ぶ（直前に言ったものと同じ文は避ける）。"""
    lines = [t.format(**fields) for t in templates]
    return rng.choice([line for line in lines if line != last] or lines)
