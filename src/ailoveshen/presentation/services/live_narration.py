"""
行動の途中の一言: 黙っている時間が続いたら、今やろうとしていることや考えていることを話す
（docs/design/43_live_narration.md）。話すのは行動を実行している間だけ（`Activity.acting`）:
次の行動や目標を選んでいる間は、選ばれていない行動を言わない。

小目標の切れ目の実況（Narrator）だけでは、時間で止めない長い行動（歩く、漕ぐ、焼くのを待つ）の
間、何分も黙っていた。読み上げが終わってから `interval_seconds` 黙っていて、しかも前の一言から
状況が変わっていたら（持ち物、位置、体力、時間帯、家の出入り）1 行話す。変わっていなければ黙る
（2026-09-29: 頻度を下げる）。続けて失敗して行き詰まっている間も黙る（同じような一言を繰り返さず、
考え直して別の手を選ぶほうが先）:
- 道具に添えた日本語の intent が新しければ、それをそのまま読む（LLM を呼ばない）
- そうでなければ、前の一言から変わったこと（行動の結果、持ち物の増減、動いた距離）と、
  直前に話したことを渡して、違う内容の 1 文を作らせる（purpose=live。会話の履歴には残さない）

状況が何も変わらないまま `idle_seconds` 黙っていたら（夜の穴の中で朝を待つ、長い歩き）、状況を
変えずに雑談を 1 行話す（今の様子への気づき、感じたこと、視聴者への一言。やることは新しく約束しない。
人間の配信者は待ち時間を黙らない）。

次の行動や目標を LLM が決めている間、続けて失敗して行き詰まっている間は、行動の一言は話さない
（選ばれていない行動を言わない）。その代わり `thinking_seconds` 黙っていたら、考え中の独り言を
1 行話す（今の状態から、迷っていること・気づいたこと。やることを新しく約束しない・したと言わない）。
目標の決定や `tool_stuck` の待ちで 2〜6 分黙っていた（2026-09-30: 30 分のうち発話の空白は最大 270 秒）。

同じ内容を繰り返さない: 実況の読み上げはすべて `SpokenLines.repeats` を通し、直前の数行と
似ていれば捨てる（Narrator と一言が同時に作っても、2 行目は読まない）。作っている間に別の
発話が始まったら、その行は捨てる（古くなる）。捨てても、次はもう 1 間隔待つ。

今の状態と食い違うことを言わない（docs/design/65 §4）: 作った文（道具の intent はそのまま読むので除く）は
読む直前に `SpeechGuard` が今の状態と照らし、食い違えば 1 回だけ作り直し、それでも食い違えば言わない。
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
from ailoveshen.presentation.services.speech_guard import SpeechGuard

SIMILAR = 0.5  # 文字の 2-gram の Jaccard がこれ以上なら同じ内容とみなす
SEMANTIC_SIMILARITY = 0.8  # Jev が「意味的に 8 割以上同じ」と判定したら同じ
REMEMBERED_LINES = 8
SHOWN_LINES = 4  # 一言のプロンプトに見せる、直前に話したこと
RECENT_ACTIONS = 3
STUCK_FAILURES = 2  # 続けてこれだけ失敗している間は話さない
MOVED_M = 10  # 前の一言からこれだけ動いたら「状況が変わった」
HEALTH_DROP = 3  # 前の一言から体力がこれだけ減ったら「状況が変わった」
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


def _skill_signature(note: str) -> str:
    """技の経過のうち、秒数を除いたもの（位置やログが変わったときだけ話す材料にする）。"""
    return note.partition("、")[2]


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
        idle_seconds: float = 0.0,
        thinking_seconds: float = 0.0,
        guard: SpeechGuard | None = None,
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
            idle_seconds: 状況が変わらないまま黙っていたら雑談するまでの秒数（0 なら話さない）
            thinking_seconds: 次の手や目標を決めている間・行き詰まっている間に黙っていたら、考え中の
                独り言を話すまでの秒数（0 なら話さない）
            guard: 作った文を、読む直前に今の状態と照らす（None なら照らさない）
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
        self._idle = idle_seconds
        self._thinking = thinking_seconds
        self._guard = guard
        self._quiet_from = clock()
        self._waiting_since: float | None = None  # 次の手を決めている・行き詰まっている状態が続いている起点
        self._last_intent = ""
        self._actions: deque[str] = deque(maxlen=RECENT_ACTIONS)
        self._seen: dict[str, Any] = {}  # 前の一言のときの持ち物・位置・体力・時間帯・家の出入り

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
        self._track_waiting(now)
        if not self._is_idle():
            self._quiet_from = now
            return
        quiet_from = max(self._quiet_from, self._spoken.last_at or self._quiet_from)
        if now - quiet_from < self._interval:
            return
        activity = self._activity()
        if activity is None:
            return
        # 行動を実行している間だけ「している」ことを話す。次の行動や目標を選んでいる間は、選ばれていない
        # 行動を言わない（2026-09-28: 目標の決定を 4 分待つ間、終わった行動の結果から「パンにするね」を
        # 何度も作って話した）。続けて失敗して行き詰まっている間も同じ。その間は考え中の独り言だけ
        if self._waiting(activity):
            await self._think_aloud(activity, now, max(quiet_from, now if self._waiting_since is None else self._waiting_since))
            return
        changes = self._changes(activity)
        if changes is None:  # 比べる前の状態がまだない: 今を基準にする
            self._mark(activity)
            return
        idle = not changes
        if idle and (self._idle <= 0 or now - quiet_from < self._idle):
            return  # 状況が変わるまで黙る（雑談の間隔に届くまで）
        line = None if idle else self._new_intent(activity)
        source = "intent"
        events: list[str] = []
        if line is None:
            events = self._idle_events(activity) if idle else self._events(activity, changes)
            line = await self._live_line(events, activity)
            source = "idle" if idle else "live"
        # 話しても話さなくても、次はもう 1 間隔待つ（捨てたときにすぐ作り直さない）
        self._quiet_from = self._clock()
        self._mark(activity)
        if not line:
            return
        if not self._is_idle():
            logger.info(f"[live] 作っている間にほかの発話が始まったので捨てた: {line}")
            return
        previous = await self._duplicate_of_recent(line)
        if previous is not None:
            logger.info(f"[live] Jev/類似度で前回と同じ判定したので捨てた: {line}（前: {previous}）")
            return
        if source != "intent":
            verified = await self._verified(line, events)
            if verified is None:
                return
            line = verified
            if not self._is_idle():
                logger.info(f"[live] 照らしている間にほかの発話が始まったので捨てた: {line}")
                return
        logger.debug(f"[live] {source}: {line}")
        await self._say(line)

    async def _live_line(self, events: list[str], activity: Activity) -> str:
        return await self._llm.generate_commentary(
            recent_events=events, activity=activity, purpose="live", record=False
        )

    async def _verified(self, line: str, events: list[str]) -> str | None:
        """作った文を今の状態と照らす（食い違えば作り直して、それでも食い違えば None）。"""
        if self._guard is None:
            return line

        async def regenerate(extra: list[str]) -> str:
            activity = self._activity()
            if activity is None:
                return ""
            again = await self._live_line(events + extra, activity)
            if again and await self._duplicate_of_recent(again) is not None:
                return ""  # 言い直しが直前と同じ内容なら言わない
            return again

        return await self._guard.verify(line, regenerate)

    @staticmethod
    def _waiting(activity: Activity) -> bool:
        """次の手や目標を決めている間か、続けて失敗して行き詰まっている間（体が動いていない）。"""
        return (
            activity.goal is None
            or not activity.acting
            or activity.failing >= STUCK_FAILURES
            or activity.stagnant_seconds > 0
        )

    def _track_waiting(self, now: float) -> None:
        """待ちが続いている起点を覚える。数秒の選び直しではなく、続いて待っているときだけ独り言にする。"""
        activity = self._activity()
        if activity is None or not self._waiting(activity):
            self._waiting_since = None
        elif self._waiting_since is None:
            self._waiting_since = now

    async def _think_aloud(self, activity: Activity, now: float, quiet_from: float) -> None:
        """次の手を決めている間・行き詰まっている間の、考え中の独り言（状態の話だけ。行動は言わない）。"""
        if self._thinking <= 0 or now - quiet_from < self._thinking:
            return
        events = self._thinking_events(activity)
        line = await self._live_line(events, activity)
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
        verified = await self._verified(line, events)
        if verified is None:
            return
        line = verified
        if not self._is_idle():
            logger.info(f"[live] 照らしている間にほかの発話が始まったので捨てた: {line}")
            return
        logger.debug(f"[live] thinking: {line}")
        await self._say(line)

    def _thinking_events(self, activity: Activity) -> list[str]:
        """独り言に渡すこと: 考え中であること、今の状態、直前に話したことと違う内容に。"""
        goal = activity.goal
        said = "」「".join(self._spoken.recent())
        return [
            f"今の小目標: {goal.describe()}（{goal.reason}）" if goal is not None else "今の小目標: 次を決めている最中",
            "（考え中の独り言）今は次にどうするかを考えている最中で、体は止まっている。今の状態（今いる場所、"
            "時間帯、体力・満腹度、持ち物、見えているもの、うまくいかなかったこと）から、迷っていること・気づいた"
            "ことを、短い 1 文（40 字くらいまで）で話す。何かを始めた・やっていると言わない。やることを"
            "新しく約束しない。今の状態にないことは言わない。"
            + (f"直前に話したこと「{said}」と同じ内容・言い換えは言わない。" if said else ""),
        ]

    def _new_intent(self, activity: Activity) -> str | None:
        """道具に添えた日本語の intent が前と違えば、それ（Jev の手の英語の id は読まない）。"""
        intent = activity.intent.strip()
        if not intent or intent == self._last_intent or not _JAPANESE.search(intent):
            return None
        self._last_intent = intent
        return intent

    def _events(self, activity: Activity, changes: list[str]) -> list[str]:
        """一言に渡すこと: 今の行動、前の一言から変わったこと、直前に話したことと違う内容に。"""
        goal = activity.goal
        events = [f"今の小目標: {goal.describe()}（{goal.reason}）" if goal is not None else "今の小目標: なし"]
        events.append(f"今の行動: {activity.intent or 'なし'}")
        events += [f"行動の結果: {a}" for a in self._actions]
        events += changes
        said = "」「".join(self._spoken.recent())
        events.append(
            "（行動の途中の一言）今やっていること・考えていること・気づいたこと・次の段取りを"
            "短い 1 文（40 字くらいまで）で話す。次の段取りは今の小目標と「今していること」の中目標から"
            "（そこにない予定を作らない）。"
            + (
                f"直前に話したこと「{said}」と同じ内容・言い換えは言わない。正しいのは今の"
                "状態（今いる場所、持ち物、行動の結果）で、直前に話したことがそれと違えば、状態に"
                "合わせて言い直す（例: 家の中に入ったと言ったが、今いる場所が家の外なら、"
                "まだ外だと言う）。"
                if said
                else ""
            )
            + "「変わったこと」に触れる"
        )
        return events

    def _idle_events(self, activity: Activity) -> list[str]:
        """雑談に渡すこと: 今の小目標と、状況が変わっていないこと、直前に話したことと違う内容に。"""
        goal = activity.goal
        said = "」「".join(self._spoken.recent())
        return [
            f"今の小目標: {goal.describe()}（{goal.reason}）" if goal is not None else "今の小目標: なし",
            f"今の行動: {activity.intent or 'なし'}",
            "（待ち時間の雑談）状況は変わっていない。今の状態（今いる場所、時間帯、体力・満腹度、持ち物、"
            "見えているもの）から、気づいたこと・感じたこと・視聴者への一言を、短い 1 文（40 字くらいまで）で"
            "話す。やることを新しく約束しない。今の状態にないことは言わない。"
            + (f"直前に話したこと「{said}」と同じ内容・言い換えは言わない。" if said else "")
        ]

    @staticmethod
    def _snapshot(activity: Activity) -> dict[str, Any] | None:
        """状況の比べる項目（観測がなければ None）。"""
        obs = activity.observation
        if obs is None:
            return None
        return {
            "inventory": dict(obs.state.get("inventory") or {}),
            "position": (obs.state.get("self") or {}).get("position") or {},
            "health": obs.health,
            "phase": obs.time_phase,
            "inside_home": obs.inside_home,
            "skill": _skill_signature(activity.skill_note),
        }

    def _mark(self, activity: Activity) -> None:
        """今の状況を、次に「変わったか」を比べる基準にする。"""
        snap = self._snapshot(activity)
        if snap is not None:
            self._seen = snap

    def _changes(self, activity: Activity) -> list[str] | None:
        """前の一言のときからの、状況の変わったこと（なければ []。比べる基準がなければ None）。"""
        now = self._snapshot(activity)
        before = self._seen
        if now is None or not before:
            return None
        out = []
        diff = {
            k: now["inventory"].get(k, 0) - before["inventory"].get(k, 0)
            for k in set(now["inventory"]) | set(before["inventory"])
            if now["inventory"].get(k, 0) != before["inventory"].get(k, 0)
        }
        if diff:
            out.append("持ち物の増減: " + "、".join(f"{k} {v:+d}" for k, v in sorted(diff.items())[:6]))
        if before["position"] and now["position"]:
            moved = (
                sum((float(now["position"].get(k, 0)) - float(before["position"].get(k, 0))) ** 2 for k in "xz")
                ** 0.5
            )
            if moved >= MOVED_M:
                out.append(f"前の一言から {moved:.0f}m 動いた")
        if before["health"] - now["health"] >= HEALTH_DROP:
            out.append(f"体力が {before['health']:.0f} から {now['health']:.0f} に減った")
        if now["phase"] != before["phase"]:
            out.append(f"時間帯が {before['phase']} から {now['phase']} に変わった")
        if now["inside_home"] != before["inside_home"]:
            out.append("家の中に入った" if now["inside_home"] else "家の外に出た")
        if now["skill"] and now["skill"] != before.get("skill"):
            # 技が動いている間は観測が更新されない: 技の経過（位置は観測より新しい）が、今起きていること
            out.append(f"技の途中の様子（最新。上の観測より新しい）: {activity.skill_note}")
        return out
