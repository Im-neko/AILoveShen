"""
道具モードの 1 手を、まず Jev に選ばせる（docs/design/34 §3）。

選択肢はブリッジの候補（`do_suggestion` として実行）、覚えた技のうち成功していて引数の要らないもの
（`run_skill`）、「どれでもない」（`ask_gemini`）。Jev が選べないとき（どれでもない、確信度が低い、
時間切れ、失敗、選択肢がない、直前の道具が失敗した、Jev の手が続けて失敗した、Jev が続けて選び
すぎた）は理由を返し、呼び出し側が Gemini に回す。Jev に「済んだか」は判定させない。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from loguru import logger

from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import Candidate, SkillInfo, ToolCall

ASK_GEMINI = "ask_gemini"
SKILL_PREFIX = "run_skill "
SKILLS_OFFERED = 5
CANDIDATES_OFFERED = 16
ASK_GEMINI_TEXT = (
    "none of these fits the goal now: let the streamer think it over (look things up, "
    "write a new skill, or do something that is not listed)"
)


@dataclass(frozen=True)
class PickedStep:
    """Jev が選んだ 1 手。`skill` は技を選んだとき（`call` は run_skill）。"""

    call: ToolCall
    option_id: str
    confidence: float
    skill: Optional[SkillInfo] = None


@dataclass(frozen=True)
class StepToGemini:
    """Jev では決めなかった理由（Gemini に回す）。"""

    why: str


class StepPicker:
    """道具モードの 1 手を、ブリッジの候補と覚えた技から Jev に選ばせる。"""

    def __init__(
        self,
        selector: IActionSelector,
        min_confidence: float = 0.5,
        timeout_seconds: float = 3.0,
        max_failures: int = 2,
        gemini_every: int = 8,
        recorder: Optional[IWatchRecorder] = None,
    ) -> None:
        """
        Args:
            selector: Jev（候補から 1 つ選ぶ）
            min_confidence: これより低ければ Gemini に回す
            timeout_seconds: Jev の答えを待つ上限
            max_failures: Jev の選んだ手がこれだけ続けて失敗したら、次は Gemini
            gemini_every: Jev がこれだけ続けて選んだら、次は Gemini（調べる・技を書く機会）
            recorder: 選択の記録（選択肢、選んだもの、確信度、Gemini に回した理由）
        """
        self._selector = selector
        self._min_confidence = min_confidence
        self._timeout = timeout_seconds
        self._max_failures = max_failures
        self._gemini_every = gemini_every
        self._recorder = recorder
        self._failures = 0  # Jev の手が続けて失敗した数
        self._streak = 0  # Jev が続けて選んだ数

    def note(self, by_jev: bool, ok: bool) -> None:
        """1 手の結果を知らせる（誰が選んだか、うまくいったか）。"""
        if by_jev:
            self._streak += 1
            self._failures = 0 if ok else self._failures + 1
        else:
            self._streak = 0
            self._failures = 0

    def options(
        self, candidates: Sequence[Candidate], skills: Sequence[SkillInfo]
    ) -> list[Candidate]:
        """Jev に見せる選択肢（候補、使える技、どれでもない）。順位はつけない。"""
        out = list(candidates[:CANDIDATES_OFFERED])
        usable = [s for s in skills if s.verified and not s.params][:SKILLS_OFFERED]
        for s in usable:
            out.append(
                Candidate(
                    f"{SKILL_PREFIX}{s.name}",
                    {
                        "verb": "run_skill",
                        "what": s.description,
                        "worked": s.successes,
                        "failed": s.failures,
                    },
                )
            )
        return out

    async def pick(
        self,
        state: dict[str, Any],
        instructions: str,
        candidates: Sequence[Candidate],
        skills: Sequence[SkillInfo],
        last_ok: Optional[bool],
    ) -> PickedStep | StepToGemini:
        """次の 1 手を選ぶ。選べなければ理由（StepToGemini）。"""
        if last_ok is False:
            return self._not("the last tool failed: the streamer looks at it")
        if self._failures >= self._max_failures:
            return self._not(f"Jev's last {self._failures} steps failed")
        if self._streak >= self._gemini_every:
            return self._not(f"Jev chose {self._streak} steps in a row: the streamer checks in")
        options = self.options(candidates, skills)
        if not options:
            return self._not("there are no candidates or usable skills")
        offered = options + [Candidate(ASK_GEMINI, {"what": ASK_GEMINI_TEXT})]
        try:
            decision = await asyncio.wait_for(
                self._selector.select(state, offered, instructions), timeout=self._timeout
            )
        except (AILoveShenError, TimeoutError) as e:
            return self._not(f"Jev did not answer ({type(e).__name__})", state, offered)
        chosen = next((o for o in options if o.action_id == decision.action_id), None)
        if decision.action_id == ASK_GEMINI:
            return self._not("Jev chose to ask the streamer", state, offered, decision.confidence)
        if chosen is None:
            return self._not(f"Jev gave no usable answer ({decision.action_id!r})", state, offered)
        if decision.confidence < self._min_confidence:
            return self._not(
                f"Jev was unsure ({decision.confidence:.2f} < {self._min_confidence})",
                state,
                offered,
                decision.confidence,
            )
        self._record(state, offered, chosen.action_id, decision.confidence)
        if chosen.action_id.startswith(SKILL_PREFIX):
            name = chosen.action_id[len(SKILL_PREFIX) :]
            skill = next(s for s in skills if s.name == name)
            call = ToolCall("run_skill", {"name": name}, intent=skill.description)
            return PickedStep(call, chosen.action_id, decision.confidence, skill)
        call = ToolCall(
            "do_suggestion", {"id": chosen.action_id}, intent=_intent(chosen)
        )
        return PickedStep(call, chosen.action_id, decision.confidence)

    def _not(
        self,
        why: str,
        state: Optional[dict[str, Any]] = None,
        offered: Sequence[Candidate] = (),
        confidence: Optional[float] = None,
    ) -> StepToGemini:
        if state is not None:
            self._record(state, offered, None, confidence, why)
        return StepToGemini(why)

    def _record(
        self,
        state: dict[str, Any],
        offered: Sequence[Candidate],
        chosen: Optional[str],
        confidence: Optional[float],
        why: str = "",
    ) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record(
                {
                    "goal": state.get("goal"),
                    "options": [o.action_id for o in offered],
                    "chosen": chosen,
                    "confidence": round(confidence, 3) if confidence is not None else None,
                    "to_gemini": why or None,
                }
            )
        except Exception as e:  # noqa: BLE001 - 記録の失敗で選択を止めない
            logger.warning(f"1 手の選択を記録できなかった: {e}")


def _intent(candidate: Candidate) -> str:
    """目標ボードに出す、今やろうとしていること（候補の id がそのまま読める文）。"""
    return candidate.action_id
