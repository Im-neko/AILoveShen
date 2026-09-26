"""
教訓帳: 視聴者の助言と自分の失敗から学んだことを、似た状況で思い出す（docs/design/35）。

教訓には、教わったときの状況のキー（決まった語彙: コードが観測から決める）か、Gemini が選んだ
条件のキーを付ける。思い出すときは、コードがキーの重なりで絞り、Jev が 1 回の呼び出しで教訓ごとに
「今当てはまるか」を答える（答えがなければコードの上位）。小目標の始まりに思い出した教訓は、その
小目標が済んだか行き詰まったかで実績を数え、効かない教訓は消える。
"""

from __future__ import annotations

import asyncio
import difflib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.lesson_store import ILessonStore
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import (
    MAX_LESSON_CHARS,
    MAX_LESSON_CONDITION_CHARS,
    SITUATION_KEYS,
    SITUATION_VALUES,
    FastQuestion,
    FastQuestionKind,
    GameObservation,
    GoalSpec,
    Lesson,
)

JUDGES = ("jev", "rules", "off")
FAILED_ENDINGS = ("stuck", "stalled", "died")
MIN_SCORE = 2
FALLBACK_SHOWN = 3  # Jev が答えないときに出す、コードの上位の数
SAME_TEXT_RATIO = 0.85
WEAK_AFTER_USES = 4  # これだけ思い出しても一度も効かなければ消す
HUNGRY_FOOD = 6
HURT_HEALTH = 8
MID_STALLED = "has made no progress"  # play.py の中目標が進まない印と同じ


def ended_kind(reason: str) -> str:
    """小目標の終わり方（goal_end_reason の文から）。理由がなければ ""。"""
    if not reason:
        return ""
    if " died and " in reason:
        return "died"
    if " is stuck" in reason:
        return "stuck"
    if " stalled " in reason or MID_STALLED in reason:
        return "stalled"
    if reason.endswith(" is met"):
        return "met"
    return "other"


def situation_of(
    spec: Optional[GoalSpec], obs: Optional[GameObservation], ended: str = ""
) -> dict[str, str]:
    """小目標と観測から、状況のキー（docs/design/35 §2.1）。"""
    keys: dict[str, str] = {}
    if spec is not None:
        keys["goal"] = spec.predicate.value
        thing = spec.item or spec.name
        if thing:
            keys["item"] = thing
    if obs is not None:
        if obs.time_phase in SITUATION_VALUES["time"]:
            keys["time"] = obs.time_phase
        keys["place"] = (
            "home" if obs.inside_home else "underground" if obs.underground else "outside"
        )
        keys["body"] = (
            "hungry" if obs.food <= HUNGRY_FOOD else "hurt" if obs.health <= HURT_HEALTH else "ok"
        )
        inventory = obs.state.get("inventory") or {}
        keys["pickaxe"] = "yes" if any(str(k).endswith("_pickaxe") for k in inventory) else "no"
    if ended:
        keys["ended"] = ended
    return keys


def clean_keys(data: Mapping[str, Any]) -> dict[str, str]:
    """Gemini が書いた条件のキーのうち、語彙にあるものだけ。"""
    keys: dict[str, str] = {}
    for key in SITUATION_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if key in SITUATION_VALUES and value not in SITUATION_VALUES[key]:
            continue
        keys[key] = value
    return keys


def _family(item: str) -> str:
    """物の種類（oak_log → log、iron_pickaxe → pickaxe）。"""
    return item.rsplit("_", 1)[-1]


def score(
    lesson: Lesson, now: Mapping[str, str], upcoming: Sequence[Mapping[str, str]] = ()
) -> Optional[int]:
    """今の状況との重なりの点（docs/design/35 §4）。当てはまらないはっきりした条件があれば None。"""
    keys = lesson.keys
    targets = [now, *upcoming]
    if lesson.explicit:
        for key in ("time", "place", "body", "pickaxe"):
            if key in keys and key in now and keys[key] != now[key]:
                return None
        for key in ("goal", "item"):
            if key in keys and not any(t.get(key) == keys[key] for t in targets):
                return None
    anchor = 0
    for t in targets:
        points = 0
        if "goal" in keys and t.get("goal") == keys["goal"]:
            points += 2
        if "item" in keys and "item" in t:
            if t["item"] == keys["item"]:
                points += 3
            elif _family(t["item"]) == _family(keys["item"]):
                points += 1
        anchor = max(anchor, points)
    total = anchor

    def same(key: str) -> bool:
        return key in keys and now.get(key) == keys[key]

    if same("time") and keys["time"] != "day":
        total += 1
    if same("place") and keys["place"] != "outside":
        total += 1
    if same("body") and keys["body"] != "ok":
        total += 2
    if same("pickaxe") and keys["pickaxe"] == "no":
        total += 1
    if same("ended") and keys["ended"] in FAILED_ENDINGS:
        total += 2
    if lesson.explicit:
        total += 1  # 書いてある条件がすべて今に合う（「夜は…」だけの教訓も思い出せる）
    return total


def _normalized(text: str) -> str:
    return "".join(text.split()).replace("。", "").replace("、", "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LessonBook:
    """教訓帳を変える・読む唯一の経路（docs/design/35）。"""

    def __init__(
        self,
        store: ILessonStore,
        judge: Optional[IFastJudge] = None,
        mode: str = "jev",
        shown: int = 4,
        candidates: int = 6,
        min_confidence: float = 0.6,
        timeout_seconds: float = 2.0,
        max_lessons: int = 200,
        recorder: Optional[IWatchRecorder] = None,
        clock: Callable[[], str] = _now_iso,
    ) -> None:
        """
        Args:
            store: 教訓を再起動とワールドの作り直しをまたいで保つ
            judge: Jev（思い出した教訓が今当てはまるかを答える）
            mode: "jev"（コードで絞って Jev が確かめる）か "rules"（コードの上位だけ）
            shown: 一度に出す教訓の数
            candidates: Jev に見せる候補の数
            min_confidence: Jev の「当てはまる」がこれ以上のときだけ出す
            timeout_seconds: Jev の答えを待つ上限（過ぎたらコードの上位）
            max_lessons: 教訓帳の上限
            recorder: 思い出した記録
            clock: 実時間（ISO 8601）
        """
        if mode not in ("jev", "rules"):
            raise ValueError(f"mode must be jev or rules, got {mode!r}")
        self._store = store
        self._judge = judge
        self._mode = mode
        self._shown = max(1, shown)
        self._candidates = max(1, candidates)
        self._min_confidence = min_confidence
        self._timeout = timeout_seconds
        self._max = max(1, max_lessons)
        self._recorder = recorder
        self._clock = clock
        self._lessons: list[Lesson] = []
        self._next_id = 1

    @property
    def lessons(self) -> tuple[Lesson, ...]:
        return tuple(self._lessons)

    def load(self) -> None:
        """保存した教訓を戻す（ワールドが変わっても捨てない: 場所によらない）。"""
        try:
            saved = self._store.load()
        except Exception as e:  # noqa: BLE001 - 読めなくてもプレイは始める
            logger.warning(f"教訓帳を読めなかった: {e}")
            return
        if saved is None:
            return
        self._lessons, self._next_id = list(saved[0]), saved[1]
        logger.info(f"教訓帳: {len(self._lessons)} 件")

    def learn(
        self,
        text: str,
        condition: str,
        keys: Mapping[str, str],
        explicit: bool,
        source: str,
    ) -> Optional[Lesson]:
        """
        教訓を書く。同じ教訓（ほぼ同じ文で同じ目標の種類）があれば、教わった回数を増やすだけ。
        書いた（強めた）教訓、書けなければ None。
        """
        text = text.strip()[:MAX_LESSON_CHARS]
        condition = condition.strip()[:MAX_LESSON_CONDITION_CHARS]
        if not text:
            return None
        situation = tuple((k, v) for k, v in keys.items() if k in SITUATION_KEYS)
        for i, old in enumerate(self._lessons):
            if self._same(old, text, dict(situation)):
                merged = replace(
                    old,
                    taught=old.taught + 1,
                    condition=old.condition or condition,
                    # 視聴者に教わったら、失敗から学んだものより強い出どころにする
                    source=source if source.startswith("viewer:") else old.source,
                )
                self._lessons[i] = merged
                self._save()
                logger.info(f"教訓をまた学んだ（{merged.taught} 回目）: {merged.id} {merged.text}")
                return merged
        try:
            lesson = Lesson(
                id=f"L{self._next_id}",
                text=text,
                condition=condition,
                situation=situation,
                explicit=explicit and bool(situation),
                source=source,
                learned_at=self._clock(),
            )
        except ValueError as e:
            logger.warning(f"教訓を書けなかった: {text}: {e}")
            return None
        self._next_id += 1
        self._lessons.append(lesson)
        self._prune()
        self._save()
        where = "、".join(f"{k}={v}" for k, v in situation) or "いつでも"
        logger.info(f"教訓を書いた: {lesson.id} {lesson.text}（{condition or where}; {source}）")
        return lesson

    async def recall(
        self,
        now: Mapping[str, str],
        upcoming: Sequence[Mapping[str, str]] = (),
        context: Optional[Mapping[str, Any]] = None,
        moment: str = "start",
    ) -> tuple[Lesson, ...]:
        """
        今の状況で思い出す教訓（最大 shown 件）。コードで絞り、Jev が当てはまるかを確かめる。

        Args:
            now: 今の状況のキー
            upcoming: これからやること（一番上の中目標のまだの手順）の状況のキー
            context: Jev に見せる今の様子（目標の文など）
            moment: 記録用（"decision": 目標の決定の前、"start": 小目標の始まり）
        """
        ranked = self._ranked(now, upcoming)
        if not ranked:
            return ()
        pool = ranked[: self._candidates]
        entry: dict[str, Any] = {
            "moment": moment,
            "situation": dict(now),
            "candidates": [lesson.id for lesson, _ in pool],
        }
        if self._mode != "jev" or self._judge is None:
            return self._record(entry, [lesson for lesson, _ in pool][:FALLBACK_SHOWN], "rules")
        questions = tuple(
            FastQuestion(
                name=f"lesson_{i}",
                kind=FastQuestionKind.YES_NO,
                instructions=(
                    f"A lesson the streamer learned before: \"{lesson.text}\" "
                    f"(it applies when: {lesson.condition or _describe(lesson.keys)}). "
                    "Does it apply to the situation now?"
                ),
            )
            for i, (lesson, _) in enumerate(pool)
        )
        state = {"situation": dict(now), **(dict(context) if context else {})}
        if upcoming:
            state["coming_next"] = [dict(u) for u in upcoming]
        try:
            verdict = await asyncio.wait_for(self._judge.ask(state, questions), timeout=self._timeout)
        except (AILoveShenError, TimeoutError) as e:
            fallback = [lesson for lesson, _ in pool][:FALLBACK_SHOWN]
            return self._record(entry, fallback, f"jev did not answer ({type(e).__name__})")
        kept: list[Lesson] = []
        answers: dict[str, Any] = {}
        for i, (lesson, _) in enumerate(pool):
            answer = verdict.answers.get(f"lesson_{i}")
            if answer is None:
                continue
            answers[lesson.id] = [answer.value, round(answer.confidence, 3)]
            if answer.value is True and answer.confidence >= self._min_confidence:
                kept.append(lesson)
        entry["answers"] = answers
        return self._record(entry, kept, "")

    def mark_used(self, lessons: Sequence[Lesson]) -> tuple[Lesson, ...]:
        """小目標の始まりに思い出した（実績の対象になる）。数え直した教訓を返す。"""
        if not lessons:
            return ()
        ids = {lesson.id for lesson in lessons}
        at = self._clock()
        self._lessons = [
            replace(n, used=n.used + 1, last_used_at=at) if n.id in ids else n
            for n in self._lessons
        ]
        self._save()
        by_id = {n.id: n for n in self._lessons}
        return tuple(by_id[n.id] for n in lessons if n.id in by_id)  # 思い出した順のまま

    def settle(self, lessons: Sequence[Lesson], ended: str) -> None:
        """
        思い出して始めた小目標の終わり方で実績を数える: 済んだ → helped、行き詰まった・進まない・
        死んだ → failed、それ以外（時間帯が変わった、など）は数えない。弱い教訓を消す。
        """
        if not lessons or ended not in ("met", *FAILED_ENDINGS):
            return
        ids = {lesson.id for lesson in lessons}
        helped = ended == "met"
        self._lessons = [
            replace(n, helped=n.helped + 1) if n.id in ids and helped
            else replace(n, failed=n.failed + 1) if n.id in ids
            else n
            for n in self._lessons
        ]
        self._prune()
        self._save()

    def _ranked(
        self, now: Mapping[str, str], upcoming: Sequence[Mapping[str, str]]
    ) -> list[tuple[Lesson, int]]:
        scored = []
        for lesson in self._lessons:
            points = score(lesson, now, upcoming)
            if points is not None and points >= MIN_SCORE:
                scored.append((lesson, points))
        scored.sort(key=lambda p: (p[1], p[0].helped - p[0].failed, p[0].taught), reverse=True)
        return scored

    def _record(self, entry: dict[str, Any], lessons: Sequence[Lesson], why: str) -> tuple[Lesson, ...]:
        shown = tuple(lessons[: self._shown])
        if self._recorder is not None:
            try:
                self._recorder.record(
                    {**entry, "shown": [n.id for n in shown], "fallback": why or None}
                )
            except Exception as e:  # noqa: BLE001 - 記録の失敗でプレイを止めない
                logger.warning(f"教訓を思い出した記録を残せなかった: {e}")
        if shown:
            logger.info(
                f"思い出した教訓（{entry['moment']}）: " + " / ".join(n.text for n in shown)
            )
        return shown

    @staticmethod
    def _same(old: Lesson, text: str, keys: Mapping[str, str]) -> bool:
        a, b = _normalized(old.text), _normalized(text)
        if a == b:
            return True
        return (
            old.keys.get("goal") == keys.get("goal")
            and difflib.SequenceMatcher(None, a, b).ratio() >= SAME_TEXT_RATIO
        )

    def _prune(self) -> None:
        """弱い教訓（何度も思い出して一度も効かない）と、上限を超えた分を消す。確かな教訓は残す。"""
        weak = [
            n for n in self._lessons if not n.proven and n.used >= WEAK_AFTER_USES and n.helped == 0
        ]
        for n in weak:
            logger.info(f"効かない教訓を消した: {n.id} {n.text}（{n.used} 回思い出して効かなかった）")
        self._lessons = [n for n in self._lessons if n not in weak]
        while len(self._lessons) > self._max:
            candidates = [n for n in self._lessons if not n.proven] or self._lessons
            worst = min(
                candidates,
                key=lambda n: (n.helped - n.failed, n.taught, n.last_used_at or n.learned_at),
            )
            self._lessons.remove(worst)
            logger.info(f"教訓帳がいっぱいなので消した: {worst.id} {worst.text}")

    def _save(self) -> None:
        try:
            self._store.save(self._lessons, self._next_id)
        except Exception as e:  # noqa: BLE001 - 保存の失敗でプレイを止めない
            logger.warning(f"教訓帳を保存できなかった: {e}")


def _describe(keys: Mapping[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in keys.items()) or "any time"
