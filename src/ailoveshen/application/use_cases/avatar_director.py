"""
アバターの表情としぐさを、状況と発言から高頻度の判断モデル（Jev）に選ばせる
（docs/design/24_avatar.md §8）。

1 回の呼び出しで 3 つを聞く: 表情（Choice）、強さ（Score）、しぐさ（Choice）。Jev が答え
られないとき（失敗、時間切れ、確信度が低い）は、呼び出し側が渡した規則の答えを使う。
表情はゲームの判断に関わらないので、外れても安全（見た目だけ）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import (
    Activity,
    EmotionState,
    EmotionType,
    FastQuestion,
    FastQuestionKind,
)

EMOTIONS = {
    "neutral": "calm and matter-of-fact",
    "happy": "glad, proud, having fun, or thanking someone",
    "sad": "disappointed, sorry, or something went wrong",
    "angry": "frustrated or annoyed (playfully)",
    "surprised": "startled or shocked by something unexpected",
    "relaxed": "at ease, cozy, or relieved",
}
GESTURES = {
    "none": "no body movement is needed",
    "nod": "agreeing, acknowledging, or starting something",
    "cheer": "celebrating a success with both arms up",
    "flinch": "startled by a hit or a sudden danger",
    "tilt": "puzzled, wondering, or thinking",
    "wave": "greeting or thanking the viewers",
}
INTENSITY_LEVELS = ["barely shown", "mild", "clear", "strong"]

QUESTIONS = (
    FastQuestion(
        name="emotion",
        kind=FastQuestionKind.CHOICE,
        instructions=(
            "Which facial expression fits the streamer at this moment, given what is happening "
            "(moment, situation) and what they say (line)?"
        ),
        criteria=EMOTIONS,
    ),
    FastQuestion(
        name="intensity",
        kind=FastQuestionKind.SCORE,
        instructions="How strongly is that expression shown?",
        criteria=INTENSITY_LEVELS,
    ),
    FastQuestion(
        name="gesture",
        kind=FastQuestionKind.CHOICE,
        instructions="Which body gesture fits this moment?",
        criteria=GESTURES,
    ),
)


@dataclass(frozen=True)
class AvatarReaction:
    """アバターの反応: 表情（None なら変えない）、強さ（0〜1）、しぐさ（None ならなし）。"""

    emotion: str | None = None
    intensity: float = 0.7
    gesture: str | None = None
    source: str = "rule"  # "jev" か "rule"
    confidence: float = 1.0


class AvatarDirector:
    """状況と発言から、アバターの表情としぐさを Jev に選ばせる（だめなら規則の答え）。"""

    def __init__(
        self,
        judge: IFastJudge,
        activity: Callable[[], Activity | None] = lambda: None,
        timeout_seconds: float = 1.5,
        min_confidence: float = 0.35,
        recorder: IWatchRecorder | None = None,
    ) -> None:
        """
        Args:
            judge: 高頻度の判断モデル（Jev）
            activity: 配信者が今していること（ゲームのセッションの activity。なければ None）
            timeout_seconds: これより遅ければ規則の答えを使う
            min_confidence: 表情の確信度がこれより低ければ規則の答えを使う
            recorder: 判断の記録（Jev と規則の答えを並べて残す。見比べるため）
        """
        self._judge = judge
        self._activity = activity
        self._timeout = timeout_seconds
        self._min_confidence = min_confidence
        self._recorder = recorder

    async def react(self, moment: str, line: str, fallback: AvatarReaction) -> AvatarReaction:
        """
        今の出来事（moment）と発言（line、なければ空）への反応を選ぶ。

        Args:
            moment: 何が起きているか（例: "speaking"、"a mid goal was completed: 家を建てる"）
            line: 配信者がこれから話すこと
            fallback: Jev が答えられないときに使う、規則の答え
        """
        state = self.state(moment, line)
        try:
            verdict = await asyncio.wait_for(
                self._judge.ask(state, QUESTIONS), timeout=self._timeout
            )
        except (AILoveShenError, TimeoutError) as e:
            logger.debug(f"アバターの判断は規則で（{type(e).__name__}）")
            self._record(state, None, fallback, fallback)
            return fallback
        answers = verdict.answers
        emotion = answers.get("emotion")
        if emotion is None or emotion.confidence < self._min_confidence:
            self._record(state, verdict.answers, fallback, fallback)
            return fallback
        intensity = answers.get("intensity")
        gesture = answers.get("gesture")
        chosen = AvatarReaction(
            emotion=None if emotion.value == "neutral" else str(emotion.value),
            intensity=_intensity(intensity.value) if intensity else fallback.intensity,
            gesture=None if gesture is None or gesture.value == "none" else str(gesture.value),
            source="jev",
            confidence=emotion.confidence,
        )
        if chosen.emotion not in (None, *EMOTIONS) or chosen.gesture not in (None, *GESTURES):
            chosen = replace(fallback)  # 知らない答えは使わない
        self._record(state, verdict.answers, fallback, chosen, verdict.elapsed_ms)
        return chosen

    async def close(self) -> None:
        """判断モデルのクライアントを閉じる。"""
        await self._judge.close()

    def state(self, moment: str, line: str) -> dict[str, Any]:
        """Jev に渡す状態（要約）。"""
        state: dict[str, Any] = {"moment": moment, "line": line.strip()}
        activity = self._activity()
        if activity is None:
            return state
        if activity.goal is not None:
            state["small_goal"] = activity.goal.spec.describe()
            state["goal_reason"] = activity.goal.reason
        if activity.intent:
            state["trying_to"] = activity.intent
        obs = activity.observation
        if obs is not None:
            mobs = obs.state.get("mobs", [])
            state["situation"] = {
                "time": obs.time_phase,
                "health": obs.health,
                "food": obs.food,
                "in_home": obs.inside_home,
                "needs": [n for n in obs.needs if n != "none"],
                "hostile_mobs_near": [
                    f"{m['name']} {m['distance_m']}m" for m in mobs if m.get("hostile")
                ][:3],
            }
        return state

    def _record(self, state, answers, fallback, chosen, elapsed_ms=None) -> None:
        if self._recorder is None:
            return
        self._recorder.record(
            {
                "state": state,
                "jev": {
                    name: {"value": a.value, "confidence": round(a.confidence, 3)}
                    for name, a in (answers or {}).items()
                },
                "rule": {"emotion": fallback.emotion, "gesture": fallback.gesture},
                "chosen": {
                    "emotion": chosen.emotion,
                    "intensity": round(chosen.intensity, 2),
                    "gesture": chosen.gesture,
                    "source": chosen.source,
                },
                "elapsed_ms": elapsed_ms,
            }
        )


def _intensity(score: float) -> float:
    """Score の期待値（0〜3）を、表情の重み（0.3〜1.0）にする。"""
    return round(min(1.0, max(0.3, 0.3 + 0.7 * float(score) / (len(INTENSITY_LEVELS) - 1))), 2)


# 表情 → 読み上げの感情（docs/design/34 §4）。relaxed と中立は声では中立
VOICE_EMOTIONS = {
    "happy": EmotionType.HAPPY,
    "sad": EmotionType.SAD,
    "angry": EmotionType.ANGRY,
    "surprised": EmotionType.SURPRISED,
}


def voice_emotion(reaction: AvatarReaction) -> EmotionState | None:
    """
    アバターの反応から読み上げの感情を作る（強さはそのまま）。規則の答え（Jev が答えなかった）なら
    None（前と同じく、声は中立のまま）。
    """
    if reaction.source != "jev":
        return None
    kind = VOICE_EMOTIONS.get(reaction.emotion or "", EmotionType.NEUTRAL)
    return EmotionState(primary=kind, intensity=max(0.0, min(1.0, reaction.intensity)))


class LineReactions:
    """
    読み上げる前に選んだ反応を、その文の読み上げが始まるまで覚えておく（アバターが同じ文で
    もう一度 Jev を呼ばないように）。古いものから捨てる。
    """

    def __init__(self, limit: int = 32) -> None:
        self._items: dict[str, AvatarReaction] = {}
        self._limit = limit

    def remember(self, text: str, reaction: AvatarReaction) -> None:
        self._items.pop(text.strip(), None)
        self._items[text.strip()] = reaction
        while len(self._items) > self._limit:
            self._items.pop(next(iter(self._items)))

    def take(self, text: str) -> AvatarReaction | None:
        return self._items.pop(text.strip(), None)
