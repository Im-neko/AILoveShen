"""応援へのお礼の言い方のテスト。"""

import random

import pytest

from ailoveshen.application.use_cases.support_thanks import names_of, thanks_for
from ailoveshen.domain.value_objects import SupportEvent, SupportKind


def E(kind, name="ねこ", **kw):
    return SupportEvent(kind, name, name.lower(), **kw)


RNG = random.Random(1)


def test_names_of_three_then_the_rest():
    events = [E(SupportKind.FOLLOW, n) for n in "あいうえお"]
    assert names_of(events[:1]) == "あさん"
    assert names_of(events[:2]) == "あさんといさん"
    assert names_of(events[:3]) == "あさん、いさん、うさん"
    assert names_of(events) == "あさん、いさん、うさんほか2人"


@pytest.mark.parametrize(
    ("event", "words"),
    [
        (E(SupportKind.FOLLOW), ("ねこさん", "フォロー")),
        (E(SupportKind.SUBSCRIBE), ("ねこさん", "サブスク")),
        (E(SupportKind.SUBSCRIBE, tier=3), ("Tier 3",)),
        (E(SupportKind.RESUBSCRIBE, months=6), ("6か月",)),
        (E(SupportKind.GIFT, amount=5), ("5人",)),
        (E(SupportKind.GIFT, amount=1, recipient="まめ"), ("まめ",)),
        (E(SupportKind.CHEER, amount=500), ("500",)),
        (E(SupportKind.CHEER, amount=5), ("ビッツ",)),
        (E(SupportKind.RAID, amount=42), ("42人",)),
        (E(SupportKind.RAID, amount=1), ("レイド",)),
    ],
)
def test_every_kind_thanks_by_name_with_what_it_brought(event, words):
    text = thanks_for([event], rng=RNG)
    assert all(w in text for w in words), text
    assert "{" not in text


def test_a_different_line_from_the_last_one():
    event = E(SupportKind.FOLLOW)
    first = thanks_for([event], rng=random.Random(0))
    for seed in range(20):
        assert thanks_for([event], last=first, rng=random.Random(seed)) != first


def test_nothing_to_thank_for_is_an_error():
    with pytest.raises(ValueError):
        thanks_for([])
