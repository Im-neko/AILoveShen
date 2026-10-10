"""
道具モードの 1 手を、まず Jev に選ばせる（docs/design/34 §3）。

選択肢はブリッジの候補（`do_suggestion` として実行）、覚えた技のうち成功していて引数の要らないもの
（`run_skill`）、「どれでもない」（`ask_gemini`）。Jev が選べないとき（どれでもない、確信度が低い、
時間切れ、失敗、選択肢がない、直前の道具が失敗した、Jev の手が続けて失敗した、Jev が続けて選び
すぎた）は理由を返し、呼び出し側が Gemini に回す。Jev に「済んだか」は判定させない。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from loguru import logger

from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import (
    ActionDecision,
    Candidate,
    LearnedCandidate,
    SkillInfo,
    ToolCall,
    ToolOutcome,
)

ASK_GEMINI = "ask_gemini"
RECOVER_VERB = "recover_items"
PLUG_VERB = "plug_hole"
LEAVE_BURROW_VERB = "leave_burrow"
CLIMB_OUT_VERB = "climb_out"
DIG_OUT_VERB = "dig_out"
CLIMB_DOWN_VERB = "climb_down"
STEP_OFF_VERB = "step_off"
TORCH_VERB = "place_torch"
TOSS_VERB = "toss"
TIDY_VERB = "tidy"
STASH_VERB = "stash"
WINDFALL_NOW = "now"  # ブリッジが見つけた、目標外の近くの鉱石・素材（`windfall`）
# 聞かずに採る寄り道は、小目標の手のうち「目標の手」と同じ数まで（最初の WINDFALL_FREE 手は数えない）。
# 8 分間 30 手のうち 24 手が寄り道で、目標（鉄 3）の手は一度も選ばれなかった（2026-10-01）
WINDFALL_FREE = 2
EAT_VERB = "eat"
DIG_IN_VERB = "dig_in"
WALL_IN_VERB = "wall_in"
REACH_LAND_VERB = "reach_land"
SKILL_PREFIX = "run_skill "
LEARNED_PREFIX = "learned "
SKILLS_OFFERED = 5
CANDIDATES_OFFERED = 16
PROBABILITIES_KEPT = 5
RESULT_CHARS = 160
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
    learned: Optional[LearnedCandidate] = None  # 覚えた候補から選んだとき（docs/design/52）


@dataclass(frozen=True)
class StepToGemini:
    """Jev では決めなかった理由（Gemini に回す）。"""

    why: str


@dataclass(frozen=True)
class _Pending:
    """結果を待っている 1 手（記録の `step` の番号、見せた選択肢、Jev が一番と見た選択肢）。"""

    step: int
    offered: tuple[str, ...]
    top: Optional[str]


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
            recorder: 選択の記録（選択肢、選んだもの、確信度、確率、Gemini に回した理由、実行の結果）。
                1 手につき `event: pick`（選択）と `event: result`（結果）の 2 行で、`step` の番号で結ぶ
        """
        self._selector = selector
        self._min_confidence = min_confidence
        self._timeout = timeout_seconds
        self._max_failures = max_failures
        self._gemini_every = gemini_every
        self._recorder = recorder
        self._failures = 0  # Jev の手が続けて失敗した数
        self._streak = 0  # Jev が続けて選んだ数
        self._step = 0
        self._pending: Optional[_Pending] = None
        self._windfall_goal: Optional[str] = None  # 数えている小目標
        self._windfall_steps = 0  # その小目標の手のうち、寄り道
        self._goal_steps = 0  # それ以外の手

    def note(self, by_jev: bool, outcome: ToolOutcome) -> None:
        """1 手の結果を知らせる（誰が選んだか、実行の結果）。数え直し、結果を記録する。"""
        ok = outcome.ok or outcome.refused if by_jev else outcome.ok
        if by_jev:
            self._streak += 1
            self._failures = 0 if ok else self._failures + 1
        else:
            self._streak = 0
            self._failures = 0
            self._goal_steps += 1  # Gemini が選んだ手は目標の手
        self._record_result(by_jev, outcome)

    def skip(self, goal: str, why: str) -> None:
        """Jev に聞かずに LLM が選ぶ手（動きがない、同じ手の繰り返し）を記録する。"""
        self._not(why, {"goal": goal}, ())

    def options(
        self,
        candidates: Sequence[Candidate],
        skills: Sequence[SkillInfo],
        learned: Sequence[LearnedCandidate] = (),
    ) -> list[Candidate]:
        """Jev に見せる選択肢（候補、使える技、覚えている手）。順位はつけない。"""
        out = list(candidates[:CANDIDATES_OFFERED])
        usable = [s for s in skills if s.verified and not s.needs_arguments][:SKILLS_OFFERED]
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
        for m in learned:
            out.append(
                Candidate(
                    f"{LEARNED_PREFIX}{m.describe()}",
                    {
                        "verb": "learned",
                        "what": m.intent or m.describe(),
                        "worked": m.worked,
                        "failed": m.failed,
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
        learned: Sequence[LearnedCandidate] = (),
    ) -> PickedStep | StepToGemini:
        """次の 1 手を選ぶ。選べなければ理由（StepToGemini）。"""
        options = self.options(candidates, skills, learned)
        offered = options + [Candidate(ASK_GEMINI, {"what": ASK_GEMINI_TEXT})]
        # 死んで落とした持ち物は 5 分で消える。ブリッジは死んで 4 分半までしか出さず、行く回数も 2 回に
        # 限る（何度も失敗して止まらないことはない）ので、直前の手の失敗や Jev の続けた数に関わらず、
        # 何より先に取りに戻る（直前の攻撃が体力切れで止まった＝死んだ直後に、Gemini に回って取りに
        # 戻らなかった）
        # 掘った跡に水が流れ込んでいるのも同じ: 畑の水が流れ出て畑が枯れる。ブリッジは 10 分までで、
        # 1 か所 2 回までしか出さない
        recover = self._forced(state, options, offered, (RECOVER_VERB, PLUG_VERB))
        if recover is not None:
            return recover
        if last_ok is False:
            return self._not("the last tool failed: the streamer looks at it", state, offered)
        # 朝になっても塞いだ穴の中にいる（ブリッジは昼に穴の中にいるときだけ出す）: 何か掘りに行かず、先に出る。
        # 失敗したあとは上の行のとおり LLM が見る（土がなくて出られない、など）
        leave = self._forced(state, options, offered, (LEAVE_BURROW_VERB,))
        if leave is not None:
            return leave
        # 飢えそう（ブリッジが `urgent` を付けた食べる手。食べる物がある）: 何を選ぶより先に食べる。
        # 満腹度 0 で肉を持ったまま go_home を選び続け、11 分歩いて体力を半分失った（2026-10-01）。
        # 失敗したあとは上の行のとおり LLM が見る
        starving = next(
            (
                o
                for o in options
                if o.description.get("verb") == EAT_VERB and o.description.get("urgent")
            ),
            None,
        )
        if starving is not None:
            self._record(state, offered, starving.action_id)
            return PickedStep(
                ToolCall("do_suggestion", {"id": starving.action_id}, intent=_intent(starving)),
                starving.action_id,
                1.0,
            )
        # 体力が危険で家が遠い（ブリッジが `urgent` を付けた穴掘り）: 遠い家へ歩かず、その場で穴に入って塞ぐ。
        # 3 回の死亡はどれも、体力 1〜5 で家への長い道を歩いている最中だった（2026-10-01）
        shelter = next(
            (
                o
                for o in options
                if o.description.get("verb") in (DIG_IN_VERB, WALL_IN_VERB) and o.description.get("urgent")
            ),
            None,
        )
        if shelter is not None:
            self._record(state, offered, shelter.action_id)
            return PickedStep(
                ToolCall("do_suggestion", {"id": shelter.action_id}, intent=_intent(shelter)),
                shelter.action_id,
                1.0,
            )
        # 水の中（ブリッジが `urgent` を付けた reach_land: 読み込まれた範囲に陸がある）: 経路探索器は水の中から動かないので、
        # 何を選んでも進まない。先に陸へ泳ぐ（2026-10-06: 海の上で夜を迎え、wait を繰り返した）。
        # 閉じた所に閉じ込められている（ブリッジが `urgent` を付けた climb_out: 昼で、積む土か丸石がある）: 歩いて
        # 出られないので、何を選んでも進まない。先に積んで出る。夜は安全な地下なので urgent なしで、LLM が選ぶ
        # （階段の床が抜けた採掘場の底で、go_home の失敗を約 50 分繰り返した: 2026-10-04）
        shut_in = next(
            (
                o
                for o in options
                if o.description.get("verb") in (CLIMB_OUT_VERB, DIG_OUT_VERB, CLIMB_DOWN_VERB, STEP_OFF_VERB, REACH_LAND_VERB)
                and o.description.get("urgent")
            ),
            None,
        )
        if shut_in is not None:
            self._record(state, offered, shut_in.action_id)
            return PickedStep(
                ToolCall("do_suggestion", {"id": shut_in.action_id}, intent=_intent(shut_in)),
                shut_in.action_id,
                1.0,
            )
        # 持ち物がほぼいっぱいで、余った物がある（ブリッジが出す）: 拾えない・掘っても入らない状態は
        # 何を選んでも進まないので、先に捨てて空ける。失敗したあとは上の行のとおり LLM が見る
        # （toss の道具もある。捨てられずに同じ手を繰り返すことはない）
        toss = self._forced(state, options, offered, (TOSS_VERB,))
        if toss is not None:
            return toss
        # 回復の手段がなく（体力が危険、満腹度が足りない、食べ物がない）家にいる（ブリッジが `urgent` を付けた
        # 預け入れ）: 家にいても治らない。死んで失う物を先に無くして、外で動けるようにする（docs/design/76）。
        # 夜に戦いに出る前の預け入れ（`urgent` なし）は目標の手順なので、ここでは選ばない
        stash = next(
            (
                o
                for o in options
                if o.description.get("verb") == STASH_VERB and o.description.get("urgent")
            ),
            None,
        )
        if stash is not None:
            self._record(state, offered, stash.action_id)
            return PickedStep(
                ToolCall("do_suggestion", {"id": stash.action_id}, intent=_intent(stash)),
                stash.action_id,
                1.0,
            )
        # 家の中で、今使わない物を持っている（ブリッジが出す。目標が使う物は残す）: 持ち物は死ぬと全部落ちて
        # 5 分で消えるので、先にチェストへ戻す。失敗したあとは上の行のとおり LLM が見る（いっぱいで入らない
        # ときは、ブリッジがしばらく出さない）
        tidy = self._forced(state, options, offered, (TIDY_VERB,))
        if tidy is not None:
            return tidy
        # 近くで見つけた目標外の鉱石・素材（ブリッジが、採れる道具があり、安全なときだけ `windfall: now` で出す。
        # docs/design/79）: 聞かずに採る。失敗したあとは上の行のとおり LLM が見る
        found = next(
            (o for o in options if o.description.get("windfall") == WINDFALL_NOW), None
        )
        if found is not None and self._detour_allowed(state):
            self._record(state, offered, found.action_id)
            return PickedStep(
                ToolCall("do_suggestion", {"id": found.action_id}, intent=_intent(found)),
                found.action_id,
                1.0,
            )
        if self._failures >= self._max_failures:
            return self._not(f"Jev's last {self._failures} steps failed", state, offered)
        if self._streak >= self._gemini_every:
            return self._not(
                f"Jev chose {self._streak} steps in a row: the streamer checks in", state, offered
            )
        if not options:
            return self._not("there are no candidates or usable skills", state, offered)
        # 聞かずに選ぶ手: 暗くて松明を持っている（何も見えない。ブリッジが暗いときだけ出す）
        torch = self._forced(state, options, offered, (TORCH_VERB,))
        if torch is not None:
            return torch
        started = time.monotonic()
        try:
            decision = await asyncio.wait_for(
                self._selector.select(state, offered, instructions), timeout=self._timeout
            )
        except (AILoveShenError, TimeoutError) as e:
            return self._not(
                f"Jev did not answer ({type(e).__name__})",
                state,
                offered,
                seconds=time.monotonic() - started,
            )
        seconds = time.monotonic() - started
        chosen = next((o for o in options if o.action_id == decision.action_id), None)
        if decision.action_id == ASK_GEMINI:
            return self._not(
                "Jev chose to ask the streamer", state, offered, decision, seconds
            )
        if chosen is None:
            return self._not(
                f"Jev gave no usable answer ({decision.action_id!r})",
                state,
                offered,
                decision,
                seconds,
            )
        if decision.confidence < self._min_confidence:
            return self._not(
                f"Jev was unsure ({decision.confidence:.2f} < {self._min_confidence})",
                state,
                offered,
                decision,
                seconds,
            )
        self._record(state, offered, chosen.action_id, decision=decision, seconds=seconds)
        if chosen.action_id.startswith(SKILL_PREFIX):
            name = chosen.action_id[len(SKILL_PREFIX) :]
            skill = next(s for s in skills if s.name == name)
            call = ToolCall("run_skill", {"name": name}, intent=skill.description)
            return PickedStep(call, chosen.action_id, decision.confidence, skill)
        if chosen.action_id.startswith(LEARNED_PREFIX):
            found = next(m for m in learned if f"{LEARNED_PREFIX}{m.describe()}" == chosen.action_id)
            call = ToolCall(found.tool, dict(found.args), intent=found.intent or found.describe())
            return PickedStep(call, chosen.action_id, decision.confidence, learned=found)
        call = ToolCall(
            "do_suggestion", {"id": chosen.action_id}, intent=_intent(chosen)
        )
        return PickedStep(call, chosen.action_id, decision.confidence)

    def recovery(self, state: dict[str, Any], candidates: Sequence[Candidate]) -> Optional[PickedStep]:
        """死んで落とした持ち物を取りに戻る手があれば、聞かずに選んだ手として返す（なければ None）。"""
        options = self.options(candidates, [])
        return self._forced(state, options, options, (RECOVER_VERB,))

    def _forced(
        self,
        state: dict[str, Any],
        options: Sequence[Candidate],
        offered: Sequence[Candidate],
        verbs: Sequence[str],
    ) -> Optional[PickedStep]:
        """Jev に聞かずに選ぶ手（`verbs` の順に、最初に見つかった候補）。"""
        forced = next(
            (o for verb in verbs for o in options if o.description.get("verb") == verb), None
        )
        if forced is None:
            return None
        self._record(state, offered, forced.action_id)
        return PickedStep(
            ToolCall("do_suggestion", {"id": forced.action_id}, intent=_intent(forced)),
            forced.action_id,
            1.0,
        )

    def _detour_allowed(self, state: dict[str, Any]) -> bool:
        """聞かずに採る寄り道をしてよいか（この小目標で、目標の手より寄り道が多くなりすぎていないとき）。"""
        self._count_for(state)
        return self._windfall_steps < WINDFALL_FREE + self._goal_steps

    def _count_for(self, state: dict[str, Any]) -> None:
        """小目標が変わったら、寄り道と目標の手の数を数え直す。"""
        goal = state.get("goal")
        if goal != self._windfall_goal:
            self._windfall_goal = goal
            self._windfall_steps = 0
            self._goal_steps = 0

    def _count_step(
        self, state: dict[str, Any], offered: Sequence[Candidate], chosen: Optional[str]
    ) -> None:
        """選んだ手が寄り道（`windfall`）か、それ以外かを数える（Gemini に回す手は選んでいないので数えない）。"""
        if chosen is None:
            return
        self._count_for(state)
        picked = next((o for o in offered if o.action_id == chosen), None)
        if picked is not None and picked.description.get("windfall"):
            self._windfall_steps += 1
        else:
            self._goal_steps += 1

    def _not(
        self,
        why: str,
        state: dict[str, Any],
        offered: Sequence[Candidate],
        decision: Optional[ActionDecision] = None,
        seconds: Optional[float] = None,
    ) -> StepToGemini:
        self._record(state, offered, None, why, decision, seconds)
        return StepToGemini(why)

    def _record(
        self,
        state: dict[str, Any],
        offered: Sequence[Candidate],
        chosen: Optional[str],
        why: str = "",
        decision: Optional[ActionDecision] = None,
        seconds: Optional[float] = None,
    ) -> None:
        """1 手の選択を記録し、結果（`note`）を待つ手にする。

        Jev に聞いたなら Jev の答え（一番と見た選択肢とその確率、確率の上位）を残す: 確信度が低いとき
        Jev が何を選ぼうとしていたか、Gemini が選んだ手と同じだったかを後で見られるように。
        """
        ids = tuple(o.action_id for o in offered)
        top = _top_option(decision, ids)
        self._count_step(state, offered, chosen)
        self._step += 1
        self._pending = _Pending(self._step, ids, top[0] if top else None)
        if self._recorder is None:
            return
        entry: dict[str, Any] = {
            "event": "pick",
            "step": self._step,
            "goal": state.get("goal"),
            "options": list(ids),
            "asked_jev": decision is not None or seconds is not None,
            "chosen": chosen,
            "confidence": round(decision.confidence, 3) if decision is not None else None,
            "to_gemini": why or None,
            "streak": self._streak,
            "failures": self._failures,
        }
        if seconds is not None:
            entry["jev_seconds"] = round(seconds, 2)
        if decision is not None:
            entry["jev_answer"] = decision.action_id
            entry["top"] = top[0] if top else None
            entry["top_p"] = round(top[1], 3) if top else None
            ranked = sorted(decision.probabilities.items(), key=lambda kv: kv[1], reverse=True)
            entry["probabilities"] = {k: round(v, 3) for k, v in ranked[:PROBABILITIES_KEPT]}
        self._write(entry)

    def _record_result(self, by_jev: bool, outcome: ToolOutcome) -> None:
        """選んだ手の結果を、`pick` と同じ `step` で残す。Gemini が選んだ手は Jev の選択肢との一致も。"""
        pending, self._pending = self._pending, None
        if self._recorder is None:
            return
        call = outcome.call
        option = _option_of(call)
        entry: dict[str, Any] = {
            "event": "result",
            "step": pending.step if pending else None,
            "by": "jev" if by_jev else "gemini",
            "tool": call.name,
            "action": call.describe()[:RESULT_CHARS],
            "option": option,
            "ok": outcome.ok,
            "refused": outcome.refused,
            "seconds": round(outcome.seconds, 2),
            "result": outcome.result[:RESULT_CHARS],
        }
        if not by_jev and pending is not None:
            entry["in_options"] = option in pending.offered if option else False
            entry["is_top"] = option is not None and option == pending.top
        self._write(entry)

    def _write(self, entry: dict[str, Any]) -> None:
        assert self._recorder is not None
        try:
            self._recorder.record(entry)
        except Exception as e:  # noqa: BLE001 - 記録の失敗で選択を止めない
            logger.warning(f"1 手の選択を記録できなかった: {e}")


def _top_option(
    decision: Optional[ActionDecision], offered: Sequence[str]
) -> Optional[tuple[str, float]]:
    """Jev が「どれでもない」を除いて一番と見た選択肢とその確率（確率がなければ答えと確信度）。"""
    if decision is None:
        return None
    real = {k: v for k, v in decision.probabilities.items() if k in offered and k != ASK_GEMINI}
    if real:
        best = max(real, key=lambda k: real[k])
        return best, real[best]
    if decision.action_id in offered and decision.action_id != ASK_GEMINI:
        return decision.action_id, decision.confidence
    return None


def _option_of(call: ToolCall) -> Optional[str]:
    """道具の呼び出しが Jev の選択肢のどれに当たるか（候補の実行、技の実行）。"""
    if call.name == "do_suggestion":
        return call.args.get("id")
    if call.name == "run_skill" and call.args.get("name"):
        return f"{SKILL_PREFIX}{call.args['name']}"
    return None


def _intent(candidate: Candidate) -> str:
    """目標ボードに出す、今やろうとしていること（候補の id がそのまま読める文）。"""
    return candidate.action_id
