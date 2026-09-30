"""
読み上げる前の一言を、今の状態と照らす（docs/design/65 §4）。

実況と一言は、LLM が書いた文がそのまま読まれていた。目標の決定と返事には差し戻しがあるのに、
ここにはなかった（家の外なのに「家の中に入った」、済んでいない小目標を「済んだ」と言う）。
文を Jev に読ませて YES_NO で聞き、答えをブリッジが知っている事実（家の中か、小目標、今の行動）と
比べる。Jev は事実を決めない: 文が何を言っているかだけを答え、食い違うかはコードが決める。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import Activity, FastQuestion, FastQuestionKind

CLAIMS_INSIDE_HOME = FastQuestion(
    name="claims_inside_home",
    kind=FastQuestionKind.YES_NO,
    instructions=(
        "A game streamer says one short line aloud. Does the line say that the streamer is right now "
        "inside the home, has just entered it, or has come back into it? Plans (\"I will go home\") "
        "and events that are over do not count."
    ),
    criteria={
        "yes": "The line says she is inside the home now, or has just got in / back in.",
        "no": "The line does not say that, or only speaks of going home later.",
    },
)
CLAIMS_OUTSIDE_HOME = FastQuestion(
    name="claims_outside_home",
    kind=FastQuestionKind.YES_NO,
    instructions=(
        "A game streamer says one short line aloud. Does the line say that the streamer is right now "
        "outside the home, away from it, or has just gone out of it? Plans and events that are over do "
        "not count."
    ),
    criteria={
        "yes": "The line says she is outside / away from the home now, or has just gone out.",
        "no": "The line does not say that.",
    },
)
CLAIMS_GOAL_DONE = FastQuestion(
    name="claims_goal_done",
    kind=FastQuestionKind.YES_NO,
    instructions=(
        "A game streamer says one short line aloud. `current_goal` is what she is working on now and it "
        "is NOT achieved yet. Does the line say that this goal, or the item or state it asks for, is "
        "already done, finished, made or obtained? Saying she is working on it, will do it, or has "
        "finished something else does not count."
    ),
    criteria={
        "yes": "The line says the current goal (or what it asks for) is already achieved.",
        "no": "The line does not say that.",
    },
)
CONTRADICTS_PLAN = FastQuestion(
    name="contradicts_plan",
    kind=FastQuestionKind.YES_NO,
    instructions=(
        "A game streamer says one short line aloud. `current_goal` is what she is working on now and "
        "`current_action` is what her body is doing (may be empty). Does the line say she is doing, or "
        "is about to do, something clearly different from these, such as a different item, place or "
        "task? Mentioning the reason behind the goal, the bigger objective it serves, or how she feels "
        "is not a contradiction."
    ),
    criteria={
        "yes": "The line announces doing something that does not fit current_goal / current_action.",
        "no": "The line fits them, or does not speak about what she is doing or will do.",
    },
)


@dataclass(frozen=True)
class SpeechFinding:
    """
    文を今の状態と照らした結果。`checked` が偽なら照らせなかった（Jev が答えない、比べる状態がない）。
    `answers` は Jev の答え（質問の名前 → [はい/いいえ, 確信度]）で、記録と測定に使う。
    """

    problems: tuple[str, ...] = ()
    checked: bool = False
    answers: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems


def speech_state(activity: Optional[Activity]) -> dict[str, Any]:
    """文を照らす基準にする、今の状態（Jev に渡すことと読み上げの記録に使う）。"""
    if activity is None:
        return {}
    state: dict[str, Any] = {}
    goal = activity.goal
    obs = activity.observation
    if goal is not None:
        status = obs.goal if obs is not None else None
        # 束の小目標は、まだ済んでいない条件だけ（一部が済んだ直後の「済んだ」は正しい）
        state["current_goal"] = " + ".join(s.describe() for s in goal.unmet(status))
        if goal.reason:
            state["goal_reason"] = goal.reason
        # 全部済んだが、プレイのループがまだ小目標を終わらせていない間は「済んだ」も正しい
        state["goal_met"] = bool(status is not None and status.met)
    if activity.intent:
        state["current_action"] = activity.intent
    state["acting"] = activity.acting
    if obs is not None:
        state["has_home"] = obs.has_home
        if obs.has_home:
            state["inside_home"] = obs.inside_home
            state["home_distance_m"] = obs.home_distance_m
        state["time_phase"] = obs.time_phase
        state["health"] = obs.health
    return state


class SpeechCheck:
    """読み上げる前の文を、今の状態と照らす。"""

    def __init__(
        self,
        judge: IFastJudge,
        min_confidence: float = 0.75,
        timeout_seconds: float = 2.0,
    ) -> None:
        """
        Args:
            judge: Jev
            min_confidence: 文が状態と食い違うと言うのは、はいの確信度がこれ以上のときだけ
            timeout_seconds: Jev の答えを待つ上限（過ぎたら照らせなかったことにする）
        """
        self._judge = judge
        self._min_confidence = min_confidence
        self._timeout = timeout_seconds

    async def check(self, text: str, activity: Optional[Activity]) -> SpeechFinding:
        """文が、今の状態と食い違うことを言っていないか。"""
        state = speech_state(activity)
        has_home = "inside_home" in state
        has_goal = "current_goal" in state
        questions = self.questions(
            has_home=has_home, has_goal=has_goal, goal_met=bool(state.get("goal_met"))
        )
        if not questions:
            return SpeechFinding()
        try:
            verdict = await asyncio.wait_for(
                self._judge.ask({"line": text, **state}, questions), timeout=self._timeout
            )
        except (AILoveShenError, TimeoutError) as e:
            logger.debug(f"発言の照合は Jev なしで（{type(e).__name__}）: 照らせなかった")
            return SpeechFinding()
        answers = {
            name: [answer.value, round(float(answer.confidence), 3)]
            for name, answer in verdict.answers.items()
        }
        return SpeechFinding(
            problems=self.problems_of(answers, state), checked=bool(answers), answers=answers
        )

    @staticmethod
    def questions(
        *, has_home: bool, has_goal: bool, goal_met: bool = False
    ) -> tuple[FastQuestion, ...]:
        """
        状態に照らして意味のある質問（家がなければ場所は、小目標がなければ小目標は聞かない。小目標が
        済んでいれば、「済んだ」は聞かない）。
        """
        out: list[FastQuestion] = []
        if has_home:
            out += [CLAIMS_INSIDE_HOME, CLAIMS_OUTSIDE_HOME]
        if has_goal:
            out += [CONTRADICTS_PLAN] if goal_met else [CLAIMS_GOAL_DONE, CONTRADICTS_PLAN]
        return tuple(out)

    def problems_of(self, answers: dict[str, list[Any]], state: dict[str, Any]) -> tuple[str, ...]:
        """Jev の答えを状態と比べて、食い違いを文にする（測定の道具も同じ比べ方を使う）。"""
        return problems_of(answers, state, self._min_confidence)

    async def close(self) -> None:
        await self._judge.close()


def problems_of(
    answers: dict[str, list[Any]], state: dict[str, Any], min_confidence: float = 0.75
) -> tuple[str, ...]:
    def says(name: str) -> bool:
        answer = answers.get(name)
        return answer is not None and answer[0] is True and float(answer[1]) >= min_confidence

    problems: list[str] = []
    if "inside_home" in state:
        if says("claims_inside_home") and not state["inside_home"]:
            distance = state.get("home_distance_m")
            where = f"家まで {distance}m" if distance is not None else "家の外"
            problems.append(f"家の中にいる・入ったと言っているが、実際は家の外（{where}）")
        if says("claims_outside_home") and state["inside_home"]:
            problems.append("家の外にいる・出たと言っているが、実際は家の中")
    goal = state.get("current_goal")
    if goal is not None:
        if says("claims_goal_done") and not state.get("goal_met"):
            problems.append(f"小目標 {goal} はまだ済んでいないのに、済んだ・手に入れたと言っている")
        if says("contradicts_plan"):
            action = state.get("current_action") or "なし"
            problems.append(
                f"今の小目標は {goal}（今の行動: {action}）なのに、別のことをする・していると言っている"
            )
    return tuple(problems)
