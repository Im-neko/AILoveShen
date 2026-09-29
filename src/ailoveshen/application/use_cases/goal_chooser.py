"""
次の小目標を、Gemini が書いた中目標の中から Jev に選ばせる（docs/design/26_steps_and_context.md）。

候補は、一番上の大目標の中目標のうちまだ済んでいないもの（ブリッジの `/check` で判定）と、今必要な
身を守る小目標（夜、夕方、空腹）。Jev は 1 回の `Choice` で 1 つ選ぶ。選べないとき（候補がない、
中目標が全部済んだのに大目標が済んでいない、確信度が低い、Jev が失敗した）は理由を返し、呼び出し側が
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
    JobState,
)

FOOD_WHEN_HUNGRY = GoalSpec(GoalPredicate.HAVE, item="food", count=4)
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


def survival_options(obs: GameObservation) -> list[Option]:
    """
    今必要な身を守る小目標（夜を越す、夕方は家に入る、空腹なら食料）。夜の越し方は決めつけない
    （家、近くのベッド、持っているベッド。2026-09-26）。地下にいるなら夜の選択肢は出さない
    （地下は夜も昼も同じ暗さ: そのまま作業を続けてよい）。
    """
    out = []
    if obs.time_phase == "night" and not obs.underground:
        out.append(
            Option(
                GoalSpec(GoalPredicate.THROUGH_NIGHT),
                "夜は危ないので安全に夜を越す（家、近くのベッド、持っているベッドで寝る）",
            )
        )
    elif obs.has_home and obs.time_phase == "dusk" and not obs.underground:
        out.append(Option(GoalSpec(GoalPredicate.AT_HOME), "暗くなる前に家に入る"))
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
        out.append(Option(FOOD_WHEN_HUNGRY, "お腹が空いたので食べ物を集める"))
    return out


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
    ) -> None:
        """
        Args:
            judge: Jev
            bridge: 中目標が済んだかを判定する（`/check`）
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
        needed = survival_options(obs)
        flat = [spec for s in open_jobs for spec in s.specs]
        try:
            # 身を守る小目標も、もう済んでいれば出さない（夕方にもう家の中、など）
            statuses = await self._bridge.check(flat + [o.spec for o in needed])
        except AILoveShenError as e:
            return self._not(f"the jobs cannot be judged: {e}")
        statuses, survival_statuses = statuses[: len(flat)], statuses[len(flat) :]
        # 中目標ごとに、まだ済んでいない条件（束なら、済んだ条件は外して追う）
        unmet: list[list[GoalSpec]] = []
        i = 0
        for s in open_jobs:
            judged = statuses[i : i + len(s.specs)]
            unmet.append([sp for sp, st in zip(s.specs, judged) if not st.met])
            i += len(s.specs)
        finished = [(s.id, True, s.progress) for s, left in zip(open_jobs, unmet) if not left]
        if finished:
            session.plan.judge_jobs(current.id, finished)  # 満たした中目標は済みのままにする
        survival = [o for o, st in zip(needed, survival_statuses) if not st.met]
        options = [
            Option(left[0], s.reason, current.id, tuple(left[1:]), s.approach if left[1:] else "")
            for s, left in zip(open_jobs, unmet)
            if left
        ] + survival
        if not options:
            return self._not("all the jobs are done but the objective is not")
        if avoid is not None:
            options = [o for o in options if not o.spec.same_kind(avoid)]
            if not options:
                return self._not(f"no other job than {avoid.describe()} after it failed")
        state = self._state(session, obs, ended_because, current.jobs, open_jobs, unmet)
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
