"""Narrator: 配信者の行動が変わる理由を声に出して言う。"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventSubscriber
from ailoveshen.domain.events import (
    GameActionExecutedEvent,
    GoalDecidingEvent,
    GoalEndedEvent,
    GoalMemberMetEvent,
    GoalSetEvent,
    HouseCompletedEvent,
    JobDoneEvent,
    ObjectiveAddedEvent,
    ObjectiveCompletedEvent,
    ObjectiveDroppedEvent,
    SkillLearnedEvent,
    SkillRevisedEvent,
    TownCompletedEvent,
    TownDefinedEvent,
    TownMapDrawnEvent,
    TownSiteChosenEvent,
)
from ailoveshen.domain.value_objects import Activity
from ailoveshen.presentation.services.llm_service import LLMService
from ailoveshen.presentation.services.speech_guard import SpeechGuard

PENDING_KEPT = 8  # 話さずに取っておく出来事の数（古いものから落とす）
THINKING_NOTICE_INTERVAL_SECONDS = 90.0  # 「考えるね」を続けて言わない間隔（計画を書いてから決めるときの 2 回目など）
# 考え始めたときの定型の一言（LLM を呼ばない。決まるまで何十秒〜何分も無音になるので）。順に使う
THINKING_NOTICES = (
    "今からちょっと次どうするか考えるね",
    "うーん、次どうしようかな。ちょっと考えるね",
    "ちょっと待ってね、次にやることを考えるよ",
)
RETHINKING_NOTICES = (
    "うまくいかなかったから、ちょっとやり方を考え直すね",
    "うーん、別のやり方を考えてみるね",
)


@dataclass(frozen=True)
class _Forecast:
    """決めたが、まだ行動が 1 つも成功していない小目標（話していない）。"""

    goal: str
    serves: str
    reason: str


class Narrator:
    """
    目標の変化を実況にして、配信者がなぜ別のことを始めたのかを視聴者に伝える。

    小目標の切れ目で起きたことは、次の目標と一緒に（1回の発話で）話す:
    小目標の終わり、完了した大目標とやめた大目標（視聴者の頼みをやめたときは
    必ず話す）、配信者が自分で加えた大目標、家の完成、街（選んだ場所とその理由、
    決めたときの中身、完成）。受けた視聴者の頼みは改めて話さない（いつやるかは返答で言っている）。

    次の小目標（予告）は、その小目標で行動が 1 つ成功してから話す（docs/design/65 §3）。話した予告を
    未達成でやめたら、次の実況で必ず言う。決めてすぐ終わった小目標は、話さなかったことにする
    （2026-09-30 のログ: 話した小目標の 58% が未達成で終わる）。

    activity はイベントが起きたときに取る。そのため実況は、生成中に決まった
    目標ではなく、そのときボットが取り組んでいた目標について話す。
    実況はバックグラウンドで生成し、プレイのループはそれを待たない。
    """

    def __init__(
        self,
        llm: LLMService,
        activity: Callable[[], Activity | None],
        say: Callable[[str], Awaitable[None]],
        gate: Callable[[list[str], float], Awaitable[bool]] | None = None,
        max_silence_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        notice: Callable[[str], Awaitable[None]] | None = None,
        guard: SpeechGuard | None = None,
        remember: Callable[[str], None] | None = None,
    ) -> None:
        """
        Narrator を初期化する。

        Args:
            llm: 実況を生成する
            activity: 配信者が今していること（例: ゲームのセッションの activity）
            say: 実況の出し先（TTS かログ）
            gate: 小目標の切れ目で、今話す価値があるか（Jev。docs/design/34 §6）。None なら毎回話す
            max_silence_seconds: 最後に話してからこれだけたっていれば、gate に聞かずに話す
            clock: 時計（テストで差し替える）
            notice: 考え始めたときの定型の一言の出し先（say と違い、直前と同じ文でも捨てない）。None なら言わない
            guard: 作った実況を、読む直前に今の状態と照らす（docs/design/65 §4）。None なら照らさない
            remember: 照らして通った実況を会話の履歴に入れる（guard があるとき、生成では入れない。
                言わなかった文を「言った」ことにしないため）
        """
        self._llm = llm
        self._activity = activity
        self._say = say
        self._gate = gate
        self._max_silence = max_silence_seconds
        self._clock = clock
        self._notice = notice
        self._guard = guard
        self._remember = remember
        self._notices = {
            False: itertools.cycle(THINKING_NOTICES),
            True: itertools.cycle(RETHINKING_NOTICES),
        }
        self._last_notice: float | None = None
        self._last_spoke: float | None = None
        self._must_speak = False  # 見どころがあった（gate に聞かずに話す）
        self._pending: list[str] = []  # 起きたこと。次の目標と一緒に話す
        self._awaiting: _Forecast | None = None  # 決めたが、行動がまだ成功していない小目標
        self._queued: dict[str, str] = {}  # 話す出来事に入れた予告（小目標 → 予告の文）
        self._told: dict[str, str] = {}  # 視聴者に話した予告（小目標 → 予告の文）
        self._said: dict[str, list[str]] = {}  # 話した文 → その文で話した予告の小目標（聞こえなかったとき取り消す）
        self._tasks: set[asyncio.Task[None]] = set()

    def subscribe(self, bus: IEventSubscriber) -> None:
        """目標のイベントを購読する。"""
        bus.subscribe(GoalEndedEvent, self.on_goal_ended)
        bus.subscribe(GoalSetEvent, self.on_goal_set)
        bus.subscribe(GameActionExecutedEvent, self.on_action)
        bus.subscribe(GoalDecidingEvent, self.on_goal_deciding)
        bus.subscribe(GoalMemberMetEvent, self.on_member_met)
        bus.subscribe(ObjectiveAddedEvent, self.on_objective_added)
        bus.subscribe(ObjectiveCompletedEvent, self.on_objective_completed)
        bus.subscribe(ObjectiveDroppedEvent, self.on_objective_dropped)
        bus.subscribe(JobDoneEvent, self.on_job_done)
        bus.subscribe(HouseCompletedEvent, self.on_house_completed)
        bus.subscribe(TownSiteChosenEvent, self.on_town_site_chosen)
        bus.subscribe(TownDefinedEvent, self.on_town_defined)
        bus.subscribe(TownMapDrawnEvent, self.on_town_map_drawn)
        bus.subscribe(TownCompletedEvent, self.on_town_completed)
        bus.subscribe(SkillLearnedEvent, self.on_skill_learned)
        bus.subscribe(SkillRevisedEvent, self.on_skill_revised)

    async def on_goal_ended(self, event: GoalEndedEvent) -> None:
        """小目標の終わりを取っておき、次の目標と一緒に話す。話した予告をやめたときは、やめたと言う。"""
        goal = event.goal
        if self._awaiting is not None and self._awaiting.goal == goal:
            # 行動が成功する前に終わった: 予告していないので、話さない
            self._awaiting = None
            logger.info(f"[narrator] 予告しないまま小目標が終わった: {goal}（{event.ended_because}）")
            if self._must_speak and self._pending:
                self._boundary()  # 待たせていた見どころは、予告なしで話す
            return
        line = self._queued.pop(goal, None)
        if line is not None and line in self._pending:
            # 予告を出来事に入れたが、まだ話していない: 予告ごと、なかったことにする
            self._pending.remove(line)
            return
        told = self._told.pop(goal, None)
        if told is not None and not event.met:
            self._pending.append(
                f"さっき次にやると話した小目標 {goal} を、やり遂げずにやめた（{event.ended_because}）。"
                "話したことと違うので、やめた理由を言う"
            )
            self._must_speak = True
            return
        result = "達成" if event.met else "未達成でやめた"
        self._pending.append(f"小目標 {goal} が{result}（{event.ended_because}）")
        self._trim()  # 予告を話す切れ目が来ない間（行動が成功しない小目標が続く間）も溜め込まない

    async def on_goal_deciding(self, event: GoalDecidingEvent) -> None:
        """
        LLM が次の目標を考え始めた。決まるまで行動も実況もなく黙るので、定型の一言で知らせる
        （バックグラウンドで。イベントの発行はハンドラーを待つので、決定の開始を遅らせない）。
        """
        if self._notice is None:
            return
        now = self._clock()
        if (
            self._last_notice is not None
            and now - self._last_notice < THINKING_NOTICE_INTERVAL_SECONDS
        ):
            return
        self._last_notice = now
        self._spawn(self._say_notice(next(self._notices[event.after_failure])))

    async def _say_notice(self, text: str) -> None:
        assert self._notice is not None
        try:
            await self._notice(text)
        except Exception as e:  # noqa: BLE001 - 一言が言えなくても決定は続ける
            logger.error(f"考え始めた一言に失敗した: {e}")

    async def on_goal_set(self, event: GoalSetEvent) -> None:
        """
        新しい小目標を取っておく（話すのは、その小目標で行動が 1 つ成功してから: `on_action`）。
        決めた直後に話すと、動く前に終わる小目標を予告してしまう。
        """
        serves = f"「{event.objective}」のため" if event.objective else "身を守るため"
        self._awaiting = _Forecast(event.goal, serves, event.reason)

    async def on_action(self, event: GameActionExecutedEvent) -> None:
        """
        新しい小目標の最初の行動が成功した。起きたことと次の目標を 1回の発話で話す。gate に聞くときは、
        聞くところからバックグラウンドで（イベントの発行はハンドラーを待つので、プレイのループを止めない）。
        「今はいい」なら取っておく。
        """
        if self._awaiting is None or not event.ok:
            return
        self._announce()
        self._boundary()

    def _announce(self) -> None:
        """動き出した小目標の予告を、話す出来事に足す。"""
        forecast = self._awaiting
        assert forecast is not None
        self._awaiting = None
        # これからやること（まだ済んでいない）と、今いる場所を添える: 前は
        # 「新しい小目標: at_home()」だけで、家から 390m 離れているのに「無事に家の中に入った」
        # と話した（2026-09-27）
        line = (
            f"新しい小目標（これからやること。まだ済んでいない）: {forecast.goal}（{forecast.serves}。"
            f"{forecast.reason}）{_place(self._activity())}"
        )
        self._pending.append(line)
        self._queued[forecast.goal] = line

    async def on_member_met(self, event: GoalMemberMetEvent) -> None:
        """束の条件が 1 つ済んだ（docs/design/44）。小目標の切れ目と同じく話す。"""
        if self._awaiting is not None and self._awaiting.goal == event.goal:
            self._announce()  # 束の一部が先に済んだ: 束の予告を先に言わないと、済んだことだけが聞こえる
        left = f"残りは {', '.join(event.left)}" if event.left else ""
        self._pending.append(
            f"一緒に追っている {event.goal} のうち {event.member} が済んだ。{left}"
        )
        self._boundary()

    def _boundary(self) -> None:
        """切れ目: 取っておいたことを話す（gate があれば聞いてから）。"""
        quiet = (self._clock() - self._last_spoke) if self._last_spoke is not None else None
        if self._gate is None or self._must_speak or quiet is None or quiet >= self._max_silence:
            events, forecasts = self._take()
            self._must_speak = False
            self._spawn(self._generate(events, self._activity(), forecasts))
            return
        self._spawn(self._ask_then_comment(list(self._pending), quiet))

    def _trim(self) -> None:
        """取っておく出来事を古いものから落とす（落とした予告は、話した予告として数えない）。"""
        if self._must_speak:
            return  # 見どころが待っている間は落とさない（次に話す）
        self._pending = self._pending[-PENDING_KEPT:]
        self._queued = {g: line for g, line in self._queued.items() if line in self._pending}

    def _take(self) -> tuple[list[str], dict[str, str]]:
        """話す出来事と、その中の予告を取り出す。"""
        events, self._pending = self._pending, []
        forecasts, self._queued = self._queued, {}
        return events, forecasts

    async def _ask_then_comment(self, events: list[str], quiet: float) -> None:
        assert self._gate is not None
        try:
            speak = await self._gate(events, quiet)
        except Exception as e:  # noqa: BLE001 - 聞けなければ話す
            logger.warning(f"実況の間合いを聞けなかった: {e}")
            speak = True
        if not speak:
            self._trim()  # 話さなかったことは捨てずに、次に話すとき一緒に（古いものから落とす）
            return
        events, forecasts = self._take()
        if not events:
            return  # ほかの実況が先に話した
        self._must_speak = False
        await self._generate(events, self._activity(), forecasts)

    async def on_objective_added(self, event: ObjectiveAddedEvent) -> None:
        """配信者が自分で加えた大目標は次の目標と一緒に話す。視聴者のもの・採った助言は返答で話している。"""
        if event.requested_by or event.suggested_by:
            return
        self._pending.append(
            f"大目標「{event.title}」をリストの {event.position} 番目に足した（{event.reason}）"
        )

    async def on_objective_completed(self, event: ObjectiveCompletedEvent) -> None:
        """完了した大目標を取っておき、次の目標と一緒に話す。"""
        self._pending.append(
            f"大目標「{event.title}」{_requested(event.requested_by, event.suggested_by)}が完了した"
        )
        self._must_speak = True

    async def on_job_done(self, event: JobDoneEvent) -> None:
        """
        済んだ中目標を取っておき、次の小目標の切れ目に話す（間合いは gate が決める）。頼みや助言の
        中目標は、誰のものかも添える。中目標が済むたびに話さなければならないわけではない。
        """
        self._pending.append(
            f"大目標「{event.objective}」の中目標 {event.job}"
            f"{_requested(event.requested_by, event.suggested_by)}が済んだ（{event.reason}）"
        )

    async def on_objective_dropped(self, event: ObjectiveDroppedEvent) -> None:
        """やめた大目標を取っておき、次の目標と一緒に話す（黙ってやめない）。"""
        self._pending.append(
            f"大目標「{event.title}」{_requested(event.requested_by, event.suggested_by)}"
            f"をやめた（{event.reason}）"
        )
        self._must_speak = True

    async def on_house_completed(self, event: HouseCompletedEvent) -> None:
        """家の完成。次の目標と一緒に話す（建築の小目標も同時に終わる）。"""
        self._pending.append(f"家「{event.name}」が完成した")
        self._must_speak = True

    async def on_town_site_chosen(self, event: TownSiteChosenEvent) -> None:
        """候補地から選んだ街の場所と、その理由。次の目標と一緒に話す。"""
        move = "そこに家を建てて引っ越す" if event.moving else "最初の家の場所のまま"
        self._pending.append(f"街「{event.name}」の場所を決めた（{move}）: {event.reason}")
        self._must_speak = True

    async def on_town_defined(self, event: TownDefinedEvent) -> None:
        """配信者が決めた街は、次の目標と一緒に話す。"""
        self._pending.append(
            f"最終目標の街をこう決めた: {event.text}（段階: {' → '.join(event.stages)}）"
        )

    async def on_town_map_drawn(self, event: TownMapDrawnEvent) -> None:
        """街の理想図は、次の目標と一緒に話す。"""
        what = "描き直した" if event.revised else "描いた"
        self._pending.append(f"街の理想図を{what}（{event.reason}）: {event.concept}")

    async def on_town_completed(self, event: TownCompletedEvent) -> None:
        """街の完成。次の目標と一緒に話す。"""
        self._pending.append(f"街が完成した（{event.text}）")
        self._must_speak = True

    async def on_skill_learned(self, event: SkillLearnedEvent) -> None:
        """技を覚えた（初めて成功した）。見どころなので、次の目標を待たずにすぐ話す。"""
        self._comment(
            [f"新しい技を覚えた: {event.description}（{event.name}。初めてうまくいった）"]
        )

    async def on_skill_revised(self, event: SkillRevisedEvent) -> None:
        """技を書いた・直した。次の目標と一緒に話す。"""
        if event.version <= 1:
            self._pending.append(f"技「{event.description}」を書いて試した")
        else:
            why = f"（前の失敗: {event.reason[:120]}）" if event.reason else ""
            self._pending.append(f"技「{event.description}」を直した（v{event.version}）{why}")

    async def drain(self) -> None:
        """生成中の実況を待つ。"""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _comment(self, events: list[str]) -> None:
        self._spawn(self._generate(events, self._activity(), {}))

    def _spawn(self, work) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _generate(
        self, events: list[str], activity: Activity | None, forecasts: dict[str, str]
    ) -> None:
        try:
            if self._guard is None:
                text = await self._llm.generate_commentary(recent_events=events, activity=activity)
            else:
                text = await self._verified_commentary(events, activity)
            if text:
                self._last_spoke = self._clock()
                self._told_now(text, forecasts)
                await self._say(text)
        except Exception as e:
            logger.error(f"目標の変化の実況に失敗した: {e}")


    async def _verified_commentary(self, events: list[str], activity: Activity | None) -> str:
        """実況を作って、読む直前の状態と照らす（言わないなら空）。通った文だけ会話の履歴に入れる。"""
        assert self._guard is not None
        text = await self._llm.generate_commentary(
            recent_events=events, activity=activity, record=False
        )
        if not text:
            return ""

        async def regenerate(extra: list[str]) -> str:
            return await self._llm.generate_commentary(
                recent_events=events + extra, activity=self._activity(), record=False
            )

        verified = await self._guard.verify(text, regenerate) or ""
        if verified and self._remember is not None:
            self._remember(verified)
        return verified

    def _told_now(self, text: str, forecasts: dict[str, str]) -> None:
        """話した予告のうち、今も追っている小目標のものを覚える（やめたら、やめたと言うため）。"""
        current = self._activity()
        goal = current.goal.describe() if current is not None and current.goal is not None else None
        told = [name for name in forecasts if name == goal]
        for name in told:
            self._told[name] = forecasts[name]
        if told:
            self._said[text.strip()] = told
            while len(self._said) > PENDING_KEPT:
                del self._said[next(iter(self._said))]

    def unheard(self, text: str) -> None:
        """
        話した文が視聴者に聞こえなかった（読み上げが古くなって捨てた、直前と同じで言わなかった、
        失敗した）。その文で話した予告は、話していないことにする（聞こえていない予告のやめたことは言わない）。
        """
        for name in self._said.pop(text.strip(), []):
            self._told.pop(name, None)


def _place(activity: Activity | None) -> str:
    """今いる場所（家があるときだけ）: 家の中か、家の外と家までの距離。"""
    obs = activity.observation if activity is not None else None
    if obs is None or not obs.has_home:
        return ""
    if obs.inside_home:
        return "。今いる場所: 家の中"
    distance = obs.home_distance_m
    if distance is None:
        return "。今いる場所: 家の外"
    return f"。今いる場所: 家の外（家まで {distance}m）"


def _requested(user_name: str, suggested_by: str = "") -> str:
    if user_name:
        return f"（{user_name}さんの頼み）"
    return f"（{suggested_by}さんの提案）" if suggested_by else ""
