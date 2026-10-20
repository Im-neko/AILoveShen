"""
同じ理由で失敗し続けているのを見つける（2026-10-03）。

`tool_stuck`（2 回続けて失敗したら、同じ道具・同じ引数を繰り返さないよう言う）は、引数の文字列が違えば
「違う」とみなす。`dig(z=285)` が「575.7m away」で断られ続けたとき、モデルは座標を 54, 55, 56 と変えただけで、
原因（マイナスを落としている）は変わらず、8 回以上続いた。原因の文（数字を除いて同じ）が続くことを数え、
モデルに「数字を変えても同じ」と伝え、続くようなら、その道具をその手番では出さない。
"""
from __future__ import annotations

import re
import time
from collections import deque
from typing import Callable, Sequence

from ailoveshen.application.ports.output.hand_store import IHandStore
from ailoveshen.domain.value_objects import SameCause, ToolCall, ToolOutcome, WithdrawnHand

SAME_CAUSE_MIN = 3  # この回数続けたら、理由を示して別の方法を選ばせる
SAME_CAUSE_WITHHOLD = 4  # この回数続けたら、その道具をこの手番では出さない
# 出さなくならない道具（別の方法そのもの）
NEVER_WITHHELD = frozenset({"do_suggestion", "run_skill"})
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_REASON_CHARS = 200


def reason_key(result: str) -> str:
    """理由の文から数字（座標、距離、id）を除いたもの。同じ原因なら、数字が違っても同じになる。"""
    return _NUMBER.sub("#", result).strip()[:_REASON_CHARS]


def same_cause_run(
    outcomes: Sequence[ToolOutcome], lookup: Callable[[str], bool] = lambda name: False
) -> SameCause | None:
    """
    直近の道具の結果（古い順）の末尾で、失敗が同じ理由で `SAME_CAUSE_MIN` 回以上続いていれば、その状態。
    見張りが止めた失敗は数えない（原因が道具の使い方ではないので）。調べもの（`lookup(道具名)` が真）は
    間にあっても数えも切りもしない（調べては同じ手を繰り返す、が一番多い形）。
    """
    acts = [o for o in outcomes if not lookup(o.call.name)]
    last = acts[-1] if acts else None
    if last is None or last.ok or last.stopped_by:
        return None
    key = reason_key(last.result)
    times = 0
    for outcome in reversed(acts):
        if outcome.ok or outcome.stopped_by or reason_key(outcome.result) != key:
            break
        times += 1
    if times < SAME_CAUSE_MIN:
        return None
    tool = last.call.name
    return SameCause(
        tool=tool,
        reason=last.result[:_REASON_CHARS],
        times=times,
        withheld=times >= SAME_CAUSE_WITHHOLD and tool not in NEVER_WITHHELD,
    )


# ブリッジの提案が通らないとき、その提案を外す（2026-10-17）。`same_cause_run` は道具の名前で数えるので、提案を選ぶ
# do_suggestion は一つの道具で、特定の提案だけを外せない。ここは提案の id（数字を除く）ごとに数える。
# 一度の失敗は普通のこと（試して分かる）なので、同じ理由で WITHDRAW_AFTER 回までは外さない。
# 技（run_skill）も同じ台帳で、技ごとに数える。技は理由が毎回違っても（目的地の外で終わる、途中で行けない、を行き来して）
# 成功しないことがある（descend_stair_raw: 16 回中 14 回失敗、同じ理由の連続は 5 に届かずブリッジの足止めも効かなかった）ので、
# 理由は問わず、成功なしの失敗が続いたら外す。外した技は、write_skill で同じ名前の版を書くと戻る
WITHDRAW_AFTER = 3  # 同じ提案が同じ理由で、この回数失敗したら外す
WITHDRAW_WINDOW_SECONDS = 1800.0  # この間の失敗だけ数える
WITHDRAW_SECONDS = 1800.0  # 外しておく時間。過ぎたら 1 回だけ試せて、同じ理由でまた失敗したらすぐ外す
# 提案の問題ではない失敗（ダメージで中断、見張りが止めた）は数えない
_TRANSIENT = re.compile(r"interrupted|took damage|hostile|reflex", re.IGNORECASE)


SKILL_HAND = "skill "


def hand_of(call: ToolCall) -> str:
    """台帳が数える手の名前: 提案は id、技は "skill 名前"。ほかの道具は空。"""
    if call.name == "do_suggestion":
        return str(call.args.get("id") or "")
    if call.name in ("run_skill", "write_skill") and call.args.get("name"):
        return f"{SKILL_HAND}{call.args['name']}"
    return ""


class HandLedger:
    """
    ブリッジの提案ごとの、同じ理由での失敗の台帳。`WITHDRAW_AFTER` 回続いたら、その提案を一定の間外す。
    外した手の代わりに、LLM は別の道具か、自分で書く技（write_skill）で目的に近づく。
    """

    def __init__(self, clock: Callable[[], float] = time.time, store: IHandStore | None = None) -> None:
        """
        Args:
            clock: 時計（秒）。保存した時刻を再起動のあとにも使うので、壁時計（time.time）。テストで差し替える
            store: 台帳を再起動をまたいで残す先（なければ残さない）
        """
        self._clock = clock
        self._store = store
        self._fails: dict[str, deque[tuple[float, str]]] = {}
        self._until: dict[str, float] = {}
        self._shown: dict[str, tuple[str, str, int]] = {}  # 外した手の id、理由、回数
        if store is not None and (saved := store.load()):
            self._restore(saved)

    def snapshot(self) -> dict:
        """保存する形。期限の過ぎたものと、古い失敗は含めない。"""
        now = self._clock()
        return {
            "fails": {
                k: [[at, why] for at, why in v if now - at <= WITHDRAW_WINDOW_SECONDS]
                for k, v in self._fails.items()
            },
            "until": {k: t for k, t in self._until.items() if t > now},
            "shown": {k: list(v) for k, v in self._shown.items() if self._until.get(k, 0) > now},
        }

    def _restore(self, saved: dict) -> None:
        try:
            for k, v in (saved.get("fails") or {}).items():
                self._fails[k] = deque(((float(at), str(why)) for at, why in v), maxlen=WITHDRAW_AFTER)
            self._until = {k: float(t) for k, t in (saved.get("until") or {}).items()}
            self._shown = {k: (str(h), str(r), int(n)) for k, (h, r, n) in (saved.get("shown") or {}).items()}
        except (TypeError, ValueError):
            self._fails, self._until, self._shown = {}, {}, {}  # 読めない保存は捨てる

    def _save(self) -> None:
        if self._store is not None:
            self._store.save(self.snapshot())

    def note(self, outcome: ToolOutcome) -> None:
        """do_suggestion と run_skill / write_skill の結果を 1 つ記録する。ほかの道具は数えない。"""
        hand = hand_of(outcome.call)
        if not hand:
            return
        key = reason_key(hand)
        rewritten = outcome.call.name == "write_skill" and not outcome.refused
        if outcome.ok or rewritten:
            # 成功、または技を書き直した（新しい版は、前の版の失敗で外さない）
            changed = [self._fails.pop(key, None), self._until.pop(key, None), self._shown.pop(key, None)]
            if any(c is not None for c in changed):
                self._save()
            return
        if outcome.call.name == "write_skill":
            return  # 書けなかった（断られた）のは、技の失敗ではない
        if outcome.stopped_by or _TRANSIENT.search(outcome.result):
            return
        now = self._clock()
        fails = self._fails.setdefault(key, deque(maxlen=WITHDRAW_AFTER))
        fails.append((now, reason_key(outcome.result)))
        recent = [why for at, why in fails if now - at <= WITHDRAW_WINDOW_SECONDS]
        same = hand.startswith(SKILL_HAND) or len(set(recent)) == 1
        if len(recent) >= WITHDRAW_AFTER and same:
            self._until[key] = now + WITHDRAW_SECONDS
            self._shown[key] = (hand, outcome.result[:_REASON_CHARS], len(recent))
        self._save()

    def withdrawn(self, hand: str) -> bool:
        """この提案（id）は今外してあるか。期限が過ぎたら、あと 1 回の失敗で外す数に戻して、出す。"""
        key = reason_key(hand)
        until = self._until.get(key)
        if until is None:
            return False
        if self._clock() >= until:
            del self._until[key]
            self._shown.pop(key, None)
            fails = self._fails.get(key)
            if fails:
                # あと 1 回同じ理由で失敗したら、また外す（残りの失敗は今のものとして数える）
                now = self._clock()
                self._fails[key] = deque(((now, why) for _, why in list(fails)[1:]), maxlen=WITHDRAW_AFTER)
            self._save()
            return False
        return True

    def current(self) -> tuple[WithdrawnHand, ...]:
        """今外してある手（プロンプトに見せる）。"""
        now = self._clock()
        out = []
        for key in list(self._until):
            if not self.withdrawn(key):
                continue
            hand, reason, times = self._shown[key]
            out.append(WithdrawnHand(hand, reason, times, max(1, round((self._until[key] - now) / 60))))
        return tuple(out)
