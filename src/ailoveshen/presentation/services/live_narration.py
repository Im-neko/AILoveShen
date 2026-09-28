"""
行動の途中の一言: 黙っている時間が続いたら、今やろうとしていることや考えていることを話す
（docs/design/43_live_narration.md）。

小目標の切れ目の実況（Narrator）だけでは、時間で止めない長い行動（歩く、漕ぐ、焼くのを待つ）の
間、何分も黙っていた。読み上げが終わってから `interval_seconds` 黙っていたら 1 行話す:
- 道具に添えた日本語の intent が新しければ、それをそのまま読む（LLM を呼ばない）
- そうでなければ、前の一言から変わったこと（行動の結果、持ち物の増減、動いた距離）と、
  直前に話したことを渡して、違う内容の 1 文を作らせる（purpose=live。会話の履歴には残さない）

同じ内容を繰り返さない: 実況の読み上げはすべて `SpokenLines.repeats` を通し、直前の数行と
似ていれば捨てる（Narrator と一言が同時に作っても、2 行目は読まない）。作っている間に別の
発話が始まったら、その行は捨てる（古くなる）。捨てても、次はもう 1 間隔待つ。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventSubscriber
from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.domain.events import GameActionExecutedEvent
from ailoveshen.domain.value_objects import Activity, FastQuestion, FastQuestionKind
from ailoveshen.presentation.services.llm_service import LLMService

SIMILAR = 0.5  # 文字の 2-gram の Jaccard がこれ以上なら同じ内容とみなす
SEMANTIC_SIMILARITY = 0.8  # Jev が「意味的に 8 割以上同じ」と判定したら同じ
REMEMBERED_LINES = 8
SHOWN_LINES = 4  # 一言のプロンプトに見せる、直前に話したこと
RECENT_ACTIONS = 3
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
_PUNCT = re.compile(r"[\s、。！？!?…「」『』（）()〜ー・,.]+")
SAME_MEANING_QUESTION = FastQuestion(
    name="same_meaning",
    kind=FastQuestionKind.YES_NO,
    instructions=(
        "Compare two short Japanese lines from a live game stream narration. "
        "Return true only when the second line is effectively the same meaning as the first, "
        "even if the wording is different. "
        "Do not treat a new problem, new action, change in place, or new inventory state as the same."
    ),
    criteria={
        "yes": "The second line says the same thing in substance as the first.",
        "no": "The second line adds a new fact, new state, or a different action.",
    },
)


def _grams(text: str) -> set[str]:
    t = _PUNCT.sub("", text)
    return {t[i : i + 2] for i in range(len(t) - 1)} or {t}


def similarity(a: str, b: str) -> float:
    """2 つの文の近さ（文字の 2-gram の Jaccard、0〜1）。"""
    x, y = _grams(a), _grams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


class SpokenLines:
    """直前に話した行（実況も返事も）。実況が同じ内容を繰り返さないために見る。"""

    def __init__(
        self, size: int = REMEMBERED_LINES, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._lines: deque[str] = deque(maxlen=size)
        self._clock = clock
        self.last_at: float | None = None

    def repeats(self, text: str) -> str | None:
        """直前の行と同じ内容なら、その行（なければ None）。"""
        return next((line for line in self._lines if similarity(text, line) >= SIMILAR), None)

    def remember(self, text: str) -> None:
        self._lines.append(text)
        self.last_at = self._clock()

    def recent(self, n: int = SHOWN_LINES) -> list[str]:
        return list(self._lines)[-n:]


class LiveNarration:
    """黙っている時間が続いたら、行動の途中の一言を話す。"""

    def __init__(
        self,
        llm: LLMService,
        activity: Callable[[], Activity | None],
        say: Callable[[str], Awaitable[None]],
        spoken: SpokenLines,
        is_idle: Callable[[], bool] = lambda: True,
        interval_seconds: float = 10.0,
        tick_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        judge: IFastJudge | None = None,
        semantic_similarity_threshold: float = 0.8,
    ) -> None:
        """
        Args:
            llm: 一言を作る
            activity: 配信者が今していること
            say: 実況の出し先（同じ内容なら捨てる。factories/stream.py）
            spoken: 直前に話した行
            is_idle: 読み上げが空いているか（TTS がなければいつも真: 話した時刻から測る）
            interval_seconds: 黙っていたら話すまでの秒数（読み上げが終わってから）
            tick_seconds: 見る間隔
            clock: 時計（テストで差し替える）
        """
        self._llm = llm
        self._activity = activity
        self._say = say
        self._spoken = spoken
        self._is_idle = is_idle
        self._interval = interval_seconds
        self._tick = tick_seconds
        self._clock = clock
        self._judge = judge
        self._semantic_similarity_threshold = semantic_similarity_threshold
        self._quiet_from = clock()
        self._last_intent = ""
        self._actions: deque[str] = deque(maxlen=RECENT_ACTIONS)
        self._seen: dict[str, Any] = {}  # 前の一言のときの持ち物と位置

    def subscribe(self, bus: IEventSubscriber) -> None:
        """行動の結果を購読する（一言に渡す、前から変わったこと）。"""
        bus.subscribe(GameActionExecutedEvent, self.on_action)

    async def on_action(self, event: GameActionExecutedEvent) -> None:
        result = "成功" if event.ok else "失敗"
        self._actions.append(f"{event.action_id} → {result}: {event.result[:120]}")

    async def run(self) -> None:
        """止められるまで見続ける。"""
        while True:
            await asyncio.sleep(self._tick)
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001 - 一言の失敗で配信を止めない
                logger.warning(f"行動の途中の一言を話せなかった: {e}")

    async def _same_meaning(self, candidate: str, previous: str) -> bool:
        """Jev に、前回の出力と意味的に近いかを聞く。8 割以上なら同じ判定。"""
        if not candidate or not previous:
            return False
        if similarity(candidate, previous) >= SIMILAR:
            return True
        if self._judge is None:
            return False
        try:
            verdict = await self._judge.ask(
                {"previous_line": previous, "candidate_line": candidate},
                (SAME_MEANING_QUESTION,),
            )
        except Exception as e:  # noqa: BLE001 - 判定失敗時は話させる
            logger.debug(f"Jev の意味判定に失敗したので通す: {e}")
            return False
        answer = verdict.answers.get(SAME_MEANING_QUESTION.name)
        if answer is None:
            return False
        return bool(answer.value) and float(answer.confidence) >= self._semantic_similarity_threshold

    async def _duplicate_of_recent(self, text: str) -> str | None:
        """直前の発話のいずれかと意味が近ければ、その行を返す。"""
        for line in reversed(self._spoken.recent()):
            if similarity(text, line) >= SIMILAR:
                return line
            if await self._same_meaning(text, line):
                return line
        return None

    async def tick(self) -> None:
        """黙っている時間が間隔を超えていれば 1 行話す。"""
        now = self._clock()
        if not self._is_idle():
            self._quiet_from = now
            return
        quiet_from = max(self._quiet_from, self._spoken.last_at or self._quiet_from)
        if now - quiet_from < self._interval:
            return
        activity = self._activity()
        if activity is None or activity.goal is None:
            return
        line = self._new_intent(activity)
        source = "intent"
        if line is None:
            line = await self._llm.generate_commentary(
                recent_events=self._events(activity),
                activity=activity,
                purpose="live",
                record=False,
            )
            source = "live"
        # 話しても話さなくても、次はもう 1 間隔待つ（捨てたときにすぐ作り直さない）
        self._quiet_from = self._clock()
        if not line:
            return
        if not self._is_idle():
            logger.info(f"[live] 作っている間にほかの発話が始まったので捨てた: {line}")
            return
        previous = await self._duplicate_of_recent(line)
        if previous is not None:
            logger.info(f"[live] Jev/類似度で前回と同じ判定したので捨てた: {line}（前: {previous}）")
            return
        logger.debug(f"[live] {source}: {line}")
        await self._say(line)

    def _new_intent(self, activity: Activity) -> str | None:
        """道具に添えた日本語の intent が前と違えば、それ（Jev の手の英語の id は読まない）。"""
        intent = activity.intent.strip()
        if not intent or intent == self._last_intent or not _JAPANESE.search(intent):
            return None
        self._last_intent = intent
        return intent

    def _events(self, activity: Activity) -> list[str]:
        """一言に渡すこと: 今の行動、前の一言から変わったこと、直前に話したことと違う内容に。"""
        goal = activity.goal
        events = [f"今の小目標: {goal.describe()}（{goal.reason}）" if goal is not None else "今の小目標: なし"]
        events.append(f"今の行動: {activity.intent or 'なし'}")
        events += [f"行動の結果: {a}" for a in self._actions]
        events += self._changes(activity)
        said = "」「".join(self._spoken.recent())
        events.append(
            "（行動の途中の一言）今やっていること・考えていること・気づいたこと・次の段取りを"
            "短い 1 文（40 字くらいまで）で話す。次の段取りは今の小目標と「今していること」の手順から"
            "（そこにない予定を作らない）。"
            + (
                f"直前に話したこと「{said}」と同じ内容・言い換えは言わない。予定を理由なく"
                "変えない（やると言ったことをやめる・明日に延ばす、など）。ただし正しいのは今の"
                "状態（今いる場所、持ち物、行動の結果）で、直前に話したことがそれと違えば、状態に"
                "合わせて言い直す（例: 家の中に入ったと言ったが、今いる場所が家の外なら、"
                "まだ外だと言う）。"
                if said
                else ""
            )
            + "変わったことがなければ、今の様子や気持ちなど別の話題にする"
        )
        return events

    def _changes(self, activity: Activity) -> list[str]:
        """前の一言のときからの、持ち物の増減と動いた距離。"""
        obs = activity.observation
        if obs is None:
            return []
        inventory = dict(obs.state.get("inventory") or {})
        position = (obs.state.get("self") or {}).get("position") or {}
        before_inv = self._seen.get("inventory")
        before_pos = self._seen.get("position")
        self._seen = {"inventory": inventory, "position": position}
        out = []
        if before_inv is not None:
            diff = {
                k: inventory.get(k, 0) - before_inv.get(k, 0)
                for k in set(inventory) | set(before_inv)
                if inventory.get(k, 0) != before_inv.get(k, 0)
            }
            if diff:
                out.append(
                    "持ち物の増減: " + "、".join(f"{k} {v:+d}" for k, v in sorted(diff.items())[:6])
                )
        if before_pos and position:
            moved = (
                sum((float(position.get(k, 0)) - float(before_pos.get(k, 0))) ** 2 for k in "xz")
                ** 0.5
            )
            if moved >= 3:
                out.append(f"前の一言から {moved:.0f}m 動いた")
        return out
