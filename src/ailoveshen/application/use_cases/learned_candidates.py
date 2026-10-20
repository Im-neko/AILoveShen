"""
覚えた候補: LLM が選んで、うまくいった手を覚え、似た状況で Jev の選択肢（候補）にもう一度出す（docs/design/52）。

道具や種が足りなくなったときのように、ブリッジの候補に無い手を LLM が思いつくことがある（クラフト、
持ち替え、チェストから出す…）。その手は 1 回きりで忘れられ、次に同じ状況になるとまた LLM を呼んで
いた。ここでは、LLM が選んだ手が成功し、そのあと持ち物か小目標の進み具合が変わったときに、状況
（目標の種類・対象・場所）といっしょに覚える。次に同じ状況になったら、その手を Jev の選択肢に
`learned …` として加える。選ぶのは Jev のままで、選ばれた手が効かなければ数えて、効かない手は消す。

覚えるのは世界の座標を含まない道具の呼び出しだけ（座標は場所が変わると意味を失う）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.learned_candidate_store import ILearnedCandidateStore
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.value_objects import Candidate, GameObservation, LearnedCandidate, ToolCall

# 覚えてよい道具: 何を作る・持つ・焼くかだけで決まり、場所を選ばないもの。待つ・寝る・戦う・逃げる・
# 歩く・掘る・置く（場所が要る）や、ソルバーの提案は覚えない。チェストの出し入れ（withdraw / deposit）も
# 覚えない: 数はそのときの不足で決まり、ブリッジが正しい数の候補（take N from the chest）を出している。
# craft も覚えない: レシピと不足はソルバーが候補に出すので覚える意味が薄く、100 件の上限の 9 割を占めて
# ほかの学びを押し出していた（2026-10-20: 91/100 件。失敗は 0 件で、害は見ていない。試しに外している）
LEARNED_TOOLS = frozenset(
    {
        "eat",
        "equip",
        "smelt",
        "use_item",
        "pickup",
        "build_next",
        "level",
        "plant",
        "till",
        "sow",
        "water",
    }
)
COORDINATES = frozenset({"x", "y", "z", "to_x", "to_y", "to_z", "entity", "id"})
# 成功した（失敗として返らなかった）こと自体が「世界が変わった」印になる道具。use_item は、何も変わらなければ
# ブリッジが失敗にする（ドアの開閉などブロックの状態の変わりも変化に数える）。ドアを開けても持ち物も小目標の
# 残りも変わらない（家に入るのは次の手）ので、この印がないと、効いた押し方を覚えられない。craft などは
# 成功しても持ち物が変わったかを見る（足りない材料で作れなかった手を覚えない）
WORLD_CHANGE_TOOLS = frozenset({"use_item"})
# 数の引数は覚えない（そのときの不足で決まる。省略した既定で足りる: craft は 1 回、smelt は持っている分）。
# 数だけ違う同じ手が別々に増えていた
QUANTITIES = frozenset({"count", "times"})
# 状況が同じとみなすキー。offers は、そのとき選択肢に出ていた手の種類（下）
MATCH_KEYS = ("goal", "item", "place", "offers")
# 選択肢の種類に数えない手（体の欲求の手はどの場面にも出る）
COMMON_VERBS = frozenset({"equip", "eat", "pickup"})


@dataclass(frozen=True)
class _Pending:
    """選んだあと、持ち物か進み具合が変わったかを見る手。"""

    situation: tuple[tuple[str, str], ...]
    call: ToolCall
    candidate_id: Optional[str]  # 覚えた候補から選んだ手なら、その id
    goal: str
    inventory: Mapping[str, int]
    remaining: int
    changed: bool = False  # 成功自体が世界の変化の印（WORLD_CHANGE_TOOLS）


def offers_of(candidates: Iterable[Candidate]) -> str:
    """選択肢に出ていた手の種類（どの場面にも出るものを除く。例: dig+explore、待つだけなら wait）。

    LLM が呼ばれるのは、Jev の選択肢に合うものがないとき。同じ種類の選択肢が並ぶ場面でだけ思い出す
    ので、選択肢が豊かな場面で覚えた手が、別の目的の場面に出ることが減る。
    """
    verbs = {c.description.get("verb", "") for c in candidates} - COMMON_VERBS - {""}
    return "+".join(sorted(verbs)) or "none"


def learnable(call: ToolCall) -> bool:
    """この道具の呼び出しは覚えてよいか（座標や対象の id を含まない、場所を選ばない道具）。"""
    return call.name in LEARNED_TOOLS and not (COORDINATES & call.args.keys())


class LearnedCandidates:
    """うまくいった LLM の手を、状況ごとに覚えて思い出す。"""

    def __init__(
        self,
        store: ILearnedCandidateStore,
        shown: int = 3,
        max_learned: int = 100,
        recorder: Optional[IWatchRecorder] = None,
    ) -> None:
        """
        Args:
            store: 保存先（再起動とワールドの作り直しをまたいで残す）
            shown: 1 回に Jev の選択肢へ加える手の数の上限
            max_learned: 覚えた候補の上限（超えたら効きの薄い順に消す）
            recorder: 覚えた・効いた・効かなかった・消したの記録
        """
        self._store = store
        self._shown = shown
        self._max = max_learned
        self._recorder = recorder
        self._learned: list[LearnedCandidate] = []
        self._next_id = 1
        self._pending: Optional[_Pending] = None

    @property
    def learned(self) -> tuple[LearnedCandidate, ...]:
        return tuple(self._learned)

    def load(self) -> None:
        """保存した手を戻す（ワールドが変わっても捨てない: 場所によらない）。"""
        try:
            saved = self._store.load()
        except Exception as e:  # noqa: BLE001 - 読めなくてもプレイは始める
            logger.warning(f"覚えた候補を読めなかった: {e}")
            return
        if saved is None:
            return
        # 覚え方が変わる前の手（今は覚えない道具、照合のキーが足りないもの）は使わない
        keys = tuple(MATCH_KEYS)
        kept = [
            m for m in saved[0] if m.tool in LEARNED_TOOLS and tuple(k for k, _ in m.situation) == keys
        ]
        self._learned, self._next_id = kept, saved[1]
        if len(kept) != len(saved[0]):
            logger.info(f"覚えた候補: 今の覚え方に合わない {len(saved[0]) - len(kept)} 件を捨てた")
            self._save()
        logger.info(f"覚えた候補: {len(self._learned)} 件")

    def recall(self, situation: Mapping[str, str]) -> list[LearnedCandidate]:
        """今の状況に合う手（効いたものが先）。合うのは目標の種類・対象・場所が同じ手。"""
        key = _key(situation)
        found = [m for m in self._learned if m.situation == key]
        found.sort(key=lambda m: (m.worked - 2 * m.failed, m.last_used_at), reverse=True)
        return found[: self._shown]

    def begin(
        self,
        situation: Mapping[str, str],
        call: ToolCall,
        obs: GameObservation,
        goal: str,
        ok: bool,
        candidate: Optional[LearnedCandidate] = None,
    ) -> None:
        """1 手を選んで実行した（obs は実行する前の観測、goal は小目標の文）。実行が成功していれば、次の観測で効いたか見る。

        `candidate` は覚えた候補から Jev が選んだ手。LLM が選んだ手は、覚えてよい道具のときだけ見る。
        """
        self._pending = None
        if candidate is None and not learnable(call):
            return
        if not ok:
            if candidate is not None:
                self._effect(candidate.id, worked=False)
            return
        self._pending = _Pending(
            situation=_key(situation),
            call=replace(call, args={k: v for k, v in call.args.items() if k not in QUANTITIES}),
            candidate_id=candidate.id if candidate else None,
            goal=goal,
            inventory=dict(obs.state.get("inventory") or {}),
            remaining=obs.goal.remaining if obs.goal else 0,
            changed=call.name in WORLD_CHANGE_TOOLS,
        )

    def settle(self, obs: GameObservation, goal: str) -> None:
        """前の手のあとの観測と今の小目標。持ち物か（同じ小目標の）進み具合が変わっていれば効いた。"""
        pending, self._pending = self._pending, None
        if pending is None:
            return
        inventory = dict(obs.state.get("inventory") or {})
        moved = obs.goal is not None and goal == pending.goal and (
            obs.goal.met or obs.goal.remaining < pending.remaining
        )
        worked = pending.changed or inventory != dict(pending.inventory) or moved
        if pending.candidate_id is not None:
            self._effect(pending.candidate_id, worked)
        elif worked:
            self._remember(pending)

    def _remember(self, pending: _Pending) -> None:
        now = _now()
        for i, m in enumerate(self._learned):
            if m.situation == pending.situation and m.tool == pending.call.name and m.args == pending.call.args:
                self._learned[i] = replace(m, worked=m.worked + 1, last_used_at=now)
                self._save()
                return
        candidate = LearnedCandidate(
            id=f"l{self._next_id}",
            situation=pending.situation,
            tool=pending.call.name,
            args=dict(pending.call.args),
            intent=pending.call.intent,
            learned_at=now,
            last_used_at=now,
        )
        self._next_id += 1
        self._learned.append(candidate)
        logger.info(f"候補を覚えた: {candidate.describe()}（{_describe(candidate)}）")
        self._log("remember", candidate)
        if len(self._learned) > self._max:
            weakest = min(self._learned, key=lambda m: (m.worked - 2 * m.failed, m.last_used_at))
            self._learned.remove(weakest)
            self._log("forget", weakest)
        self._save()

    def _effect(self, candidate_id: str, worked: bool) -> None:
        """覚えた候補から選んだ手が効いたか。効かない手は消す。"""
        for i, m in enumerate(self._learned):
            if m.id != candidate_id:
                continue
            m = replace(
                m,
                worked=m.worked + worked,
                failed=m.failed + (not worked),
                last_used_at=_now(),
            )
            if m.failed > m.worked:
                del self._learned[i]
                logger.info(f"効かない覚えた候補を消した: {m.describe()}（{_describe(m)}）")
                self._log("drop", m)
            else:
                self._learned[i] = m
                self._log("worked" if worked else "failed", m)
            self._save()
            return

    def _log(self, event: str, candidate: LearnedCandidate) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record(
                {
                    "event": event,
                    "candidate": candidate.id,
                    "call": candidate.describe(),
                    "situation": dict(candidate.situation),
                    "worked": candidate.worked,
                    "failed": candidate.failed,
                }
            )
        except Exception as e:  # noqa: BLE001 - 記録の失敗で選択を止めない
            logger.warning(f"覚えた候補の記録を書けなかった: {e}")

    def _save(self) -> None:
        try:
            self._store.save(self._learned, self._next_id)
        except Exception as e:  # noqa: BLE001 - 保存の失敗でプレイを止めない
            logger.warning(f"覚えた候補を保存できなかった: {e}")


def _key(situation: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple((k, situation.get(k, "")) for k in MATCH_KEYS)


def _describe(candidate: LearnedCandidate) -> str:
    return ", ".join(f"{k}={v}" for k, v in candidate.situation if v) or "any"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
