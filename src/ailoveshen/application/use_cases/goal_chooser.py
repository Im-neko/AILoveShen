"""
次の小目標を、Gemini が書いた手順の中から Jev に選ばせる（docs/design/26_steps_and_context.md）。

候補は、一番上の中目標の手順のうちまだ済んでいないもの（ブリッジの `/check` で判定）と、今必要な
身を守る小目標（夜、夕方、空腹）。Jev は 1 回の `Choice` で 1 つ選ぶ。選べないとき（候補がない、
手順が全部済んだのに中目標が済んでいない、確信度が低い、Jev が失敗した）は理由を返し、呼び出し側が
Gemini に回す。済んだかどうかは Jev に判定させない（ブリッジが世界から判定する）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import (
    FastQuestion,
    FastQuestionKind,
    GameObservation,
    GoalPredicate,
    GoalSpec,
)

FOOD_WHEN_HUNGRY = GoalSpec(GoalPredicate.HAVE, item="food", count=4)


@dataclass(frozen=True)
class Option:
    """Jev に示す候補: 小目標、なぜそれか、何のためか（中目標の id。身を守るものは None）。"""

    spec: GoalSpec
    reason: str
    mid_goal_id: Optional[str] = None


@dataclass(frozen=True)
class ChosenGoal:
    """Jev が選んだ次の小目標。`mid_goal_id` は手順なら中目標、身を守るものなら None。"""

    spec: GoalSpec
    reason: str
    mid_goal_id: Optional[str]
    confidence: float


@dataclass(frozen=True)
class NotChosen:
    """Jev では決められなかった理由（Gemini に回す）。"""

    why: str


def survival_options(obs: GameObservation) -> list[Option]:
    """今必要な身を守る小目標（夜は家で過ごす、夕方は家に入る、空腹なら食料）。"""
    out = []
    if obs.has_home and obs.time_phase == "night":
        out.append(Option(GoalSpec(GoalPredicate.THROUGH_NIGHT), "夜は危ないので家で夜を越す"))
    elif obs.has_home and obs.time_phase == "dusk":
        out.append(Option(GoalSpec(GoalPredicate.AT_HOME), "暗くなる前に家に入る"))
    if any("hunger" in n for n in obs.needs):
        out.append(Option(FOOD_WHEN_HUNGRY, "お腹が空いたので食べ物を集める"))
    return out


class GoalChooser:
    """一番上の中目標の手順と身を守る小目標から、次の小目標を Jev に選ばせる。"""

    def __init__(
        self,
        judge: IFastJudge,
        bridge: IMinecraftBridge,
        min_confidence: float = 0.4,
        timeout_seconds: float = 10.0,
        recorder: Optional[IWatchRecorder] = None,
    ) -> None:
        """
        Args:
            judge: Jev
            bridge: 手順が済んだかを判定する（`/check`）
            min_confidence: これより低ければ Gemini に回す
            timeout_seconds: Jev の答えを待つ上限
            recorder: 選択の記録（候補、選んだもの、確信度、Gemini に回した理由）
        """
        self._judge = judge
        self._bridge = bridge
        self._min_confidence = min_confidence
        self._timeout = timeout_seconds
        self._recorder = recorder

    async def choose(
        self, session: PlaySession, obs: GameObservation, ended_because: str
    ) -> ChosenGoal | NotChosen:
        """次の小目標を選ぶ。選べなければ理由（NotChosen）。"""
        current = session.plan.current
        if current is None or not current.plan_steps:
            return self._not("the top mid goal has no steps")
        steps = current.plan_steps
        try:
            statuses = await self._bridge.check([s.spec for s in steps])
        except AILoveShenError as e:
            return self._not(f"the steps cannot be judged: {e}")
        open_steps = [s for s, st in zip(steps, statuses) if not st.met]
        survival = survival_options(obs)
        if not open_steps and not survival:
            return self._not("all the steps are done but the mid goal is not")
        options = [Option(s.spec, s.reason, current.id) for s in open_steps] + survival
        state = self._state(session, obs, ended_because, steps, statuses)
        if len(options) == 1:
            return self._chosen(state, options, 0, 1.0)
        labels = [chr(ord("A") + i) for i in range(len(options))]
        question = FastQuestion(
            name="next_goal",
            kind=FastQuestionKind.CHOICE,
            instructions=(
                "Which small goal should the streamer work on next? Follow the steps in order "
                "unless another open step is clearly better now (it is nearer, or the inventory "
                "already has what it needs). Choose a survival goal when the night, danger or "
                "hunger requires it."
            ),
            criteria={
                label: f"{o.spec.describe()}: {o.reason}" for label, o in zip(labels, options)
            },
        )
        try:
            verdict = await asyncio.wait_for(
                self._judge.ask(state, (question,)), timeout=self._timeout
            )
        except (AILoveShenError, TimeoutError) as e:
            return self._not(f"Jev did not answer ({type(e).__name__})", state, options)
        answer = verdict.answers.get("next_goal")
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
        return ChosenGoal(o.spec, o.reason, o.mid_goal_id, confidence)

    def _not(self, why: str, state=None, options=()) -> NotChosen:
        if state is not None:
            self._record(state, options, why=why)
        return NotChosen(why)

    @staticmethod
    def _state(session, obs, ended_because, steps, statuses) -> dict[str, Any]:
        current = session.plan.current
        last = session.goal.spec.describe() if session.goal else None
        return {
            "mid_goal": current.title if current else None,
            "steps": [
                f"{s.spec.describe()}: {'done' if st.met else 'not yet'}"
                for s, st in zip(steps, statuses)
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
                    "options": [o.spec.describe() for o in options],
                    "chosen": options[chosen].spec.describe() if chosen is not None else None,
                    "confidence": round(confidence, 3) if confidence is not None else None,
                    "to_gemini": why or None,
                }
            )
        except Exception as e:  # noqa: BLE001 - 記録の失敗で選択を止めない
            logger.warning(f"小目標の選択を記録できなかった: {e}")
