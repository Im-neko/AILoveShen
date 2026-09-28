"""チャット返答生成のユースケースの実装。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from ailoveshen.application.dto.llm_dto import (
    GenerateResponseRequest,
    GenerateResponseResponse,
)
from ailoveshen.application.ports.input.generate_response import IGenerateResponse
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.prompt_builder import IPromptBuilder
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.goal_vocabulary import (
    DeclineReason,
    MidGoalProposal,
    PromiseHandling,
    RequestHandling,
    parse_proposal,
    promise_schema,
    reply_schema,
)
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.lessons import LessonBook, clean_keys, situation_of
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.promise_check import PromiseCheck
from ailoveshen.application.use_cases.readings import GUESS, VIEWER, NameReadings, tells_reading
from ailoveshen.application.use_cases.self_facts import SelfFacts
from ailoveshen.domain.entities import Conversation, PlaySession
from ailoveshen.domain.events import ChatResponseGeneratedEvent
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    GenerationContext,
    MessageType,
    MidGoal,
)


# 返事の前の仕分けで、答えを必ず行動か理由にする種類（頼み・助言）
ACTIONABLE = ("request", "advice")
ACTIONABLE_ATTEMPTS = 3  # これらは差し戻しが増えるので、試す回数を増やす（最後は黙らずに話す）


def unanswered(kind: str | None, data: dict[str, object]) -> str:
    """
    頼み・助言への答えが、何も残さずに流しているか（2026-09-28: 「今は○○中だから後で」と
    none / decline にして、中目標にも考え直しにも教訓にもならなかった）。差し戻す理由か空文字列。
    """
    if kind not in ACTIONABLE:
        return ""  # 仕分けがない・Jev が答えなかった: 今までどおり（約束の確かめが後ろで拾う）
    handling = data.get("request")
    if handling == RequestHandling.DECLINE.value:
        reason = data.get("decline_reason")
        allowed = [r.value for r in DeclineReason]
        if reason not in allowed:
            return (
                f"decline には decline_reason（{' / '.join(allowed)}）が要る。今は別のことを"
                "している・今の目標が先、は理由にならない: やる価値があるなら accept の when=next で"
                "中目標の後ろに入れる（返答でもそう言う）。"
            )
        if kind == "advice" and reason == DeclineReason.OFF_MISSION.value:
            return (
                "助言は off_mission で断らない: 後の作業が楽になる・安全になるなら accept（when=next "
                "で後ろに）、当たっていなければ wrong。"
            )
        return ""
    if handling != RequestHandling.NONE.value:
        return ""
    for key in ("rethink", "lesson"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return ""
    what = "助言" if kind == "advice" else "頼み"
    return (
        f"このコメントは視聴者の{what}。none で答えると何も残らない。次のどれかにする: やる価値が"
        "あれば accept（今やるほうがよければ when=now、そうでなければ when=next で後ろに入れ、返答で"
        "いつやるか言う）、今の小目標や計画の問題の指摘に納得したら rethink、これからのやり方の助言なら"
        " lesson、できない・当たっていない・危ないなら decline（decline_reason つき）。"
    )


@dataclass(frozen=True)
class KeptPromise:
    """返答の約束から足した中目標（now: 今の小目標を区切って今やる）。"""

    mid_goal: MidGoal
    now: bool


@dataclass(frozen=True)
class UnkeptPromise:
    """中目標にできない約束: 理由と、約束を外して書き直した返答（空なら返答から作り直す）。"""

    reason: str
    reply: str = ""


class GenerateResponseUseCase(IGenerateResponse):
    """
    視聴者のチャットに返答するユースケース（サブループ / 割り込み）。

    プレイ中の返答は、配信者が今していること（目標の決定と同じもの: 大目標、
    中目標、小目標）を見て、視聴者の頼みを中目標として受けることがある。返答と
    頼みの扱いは 1 回の生成から出るので、受けると言った返答は必ず中目標を足す。
    足した中目標は、今取り組んでいる中目標の後ろに入る。小目標は中断しない。
    プランの上限（1 人 1 つ、など）を破る頼みや判定できない頼みは、理由をつけて
    モデルに戻し、断らせる。

    次のことをまとめる:
    - 視聴者のチャットを会話履歴に記録する
    - アダプターでプロンプトを組み立てる
    - アダプターでテキスト（または返答と頼みの扱い）を生成する
    - 受けた頼みを中目標に足す
    - 返答を記録し、ドメインイベントを発行する
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IPromptBuilder,
        event_publisher: IEventPublisher,
        conversation: Conversation,
        character: CharacterProfile,
        mid_goals: MidGoalKeeper | None = None,
        history_limit: int = 10,
        max_attempts: int = 2,
        readings: NameReadings | None = None,
        notes: NoteKeeper | None = None,
        lessons: LessonBook | None = None,
        promise_check: PromiseCheck | None = None,
        self_facts: SelfFacts | None = None,
        rethink_interval_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        依存を受け取ってユースケースを初期化する（依存性の注入）。

        Args:
            text_generator: LLM のテキスト生成のアダプター
            prompt_builder: プロンプト組み立てのアダプター
            event_publisher: ドメインイベントの発行器
            conversation: 実況と目標の決定と共有する会話履歴
            character: 配信者のキャラクターのプロフィール
            mid_goals: 受けた頼みを中目標に足す（None: 返答は話すだけ）
            history_limit: モデルに渡す最近の会話のメッセージの数
            max_attempts: 受けた頼みが使えないとき、あきらめるまでに生成する回数
            readings: 視聴者の名前の読みの辞書（プロンプトに出し、返答の name_reading で覚える。
                docs/design/30_name_readings.md）
            notes: 自分のメモ（納得した視聴者のアドバイスを教訓として書く。None なら書かない）
            lessons: 教訓帳（あれば、納得したアドバイスはメモではなく、条件つきでこちらに書く:
                似た状況で思い出す。docs/design/35）
            promise_check: 返答が行動を約束しているのに中目標にも考え直しにもならないかを
                確かめる（Jev）。約束していれば Gemini が守り方（今やる / 後でやる）を決めて
                中目標にし、できなければ書き直させる（docs/design/34 §10。None なら確かめない）
            self_facts: 自分について話したこと（プロンプトに出して守らせ、返答の self_facts で
                覚える。docs/design/40。None なら覚えない）
            rethink_interval_seconds: コメントの指摘で小目標を決め直す最短の間（荒らし対策）
            clock: 時計（テストで差し替える）
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._event_publisher = event_publisher
        self._conversation = conversation
        self._character = character
        self._mid_goals = mid_goals
        self._history_limit = history_limit
        self._max_attempts = max_attempts
        self._readings = readings
        self._notes = notes
        self._lessons = lessons
        self._promise_check = promise_check
        self._self_facts = self_facts
        self._rethink_interval = rethink_interval_seconds
        self._clock = clock
        self._last_rethink_at: float | None = None

    async def execute(self, request: GenerateResponseRequest) -> GenerateResponseResponse:
        """
        チャット返答生成のユースケースを実行する。

        流れ:
        1. 視聴者のチャットを記録する
        2. 生成の文脈（セッションの activity を含む）とプロンプトを組み立てる
        3. 返答を生成する。頼みを受けたときは、それを中目標に足す
        4. 返答を記録し、ChatResponseGeneratedEvent を発行する
        """
        try:
            # モデルに渡す履歴には、返答するチャットを含めない。
            # そのチャットはプロンプトが別に示す。
            history = self._conversation.recent_messages(self._history_limit)
            self._conversation.add_viewer_message(
                content=request.message,
                user_name=request.user_name,
                user_id=request.user_id,
            )

            session = request.session
            activity = session.activity() if session is not None else None
            context = GenerationContext(
                emotion_state=request.emotion_state,
                activity=activity,
                recent_messages=history,
            )

            logger.debug(f"返答を生成する: {request.message[:50]}...")
            text, mid_goal = await self._generate(request, context, session)

            if text:
                self._conversation.add_streamer_message(text, MessageType.RESPONSE)
                await self._event_publisher.publish(
                    ChatResponseGeneratedEvent(
                        text=text,
                        original_message=request.message,
                        user_name=request.user_name,
                    )
                )

            return GenerateResponseResponse.ok(
                text=text,
                original_message=request.message,
                user_name=request.user_name,
                mid_goal=mid_goal,
            )

        except Exception as e:
            logger.error(f"返答の生成に失敗した: {e}")
            return GenerateResponseResponse.error_response(
                error=str(e),
                original_message=request.message,
                user_name=request.user_name,
            )

    async def _generate(
        self,
        request: GenerateResponseRequest,
        context: GenerationContext,
        session: PlaySession | None,
    ) -> tuple[str, MidGoal | None]:
        known = self._readings.get(request.user_name) if self._readings else None
        name_reading = known.reading if known else ""
        self_facts = self._self_facts.all() if self._self_facts else ()
        if session is None or self._mid_goals is None:
            system_prompt = self._prompt_builder.build_system_prompt(self._character, "reply")
            prompt = self._prompt_builder.build_chat_response_prompt(
                user_name=request.user_name,
                message=request.message,
                context=context,
                name_reading=name_reading,
                self_facts=self_facts,
            )
            text = await self._text_generator.generate(
                prompt=prompt, system_instruction=system_prompt, purpose="reply"
            )
            return text, None

        system_prompt = self._prompt_builder.build_system_prompt(self._character, "reply_requests")
        error = ""
        kind = request.kind
        advice = kind == "advice"
        attempts = (
            max(self._max_attempts, ACTIONABLE_ATTEMPTS) if kind in ACTIONABLE else self._max_attempts
        )
        for attempt in range(1, attempts + 1):
            prompt = self._prompt_builder.build_chat_response_prompt(
                user_name=request.user_name,
                message=request.message,
                context=context,
                takes_requests=True,
                previous_error=error,
                name_reading=name_reading,
                self_facts=self_facts,
            )
            data = await self._text_generator.generate_json(
                prompt, reply_schema(), system_instruction=system_prompt, purpose="reply"
            )
            text = str(data.get("reply", "")).strip()
            handling = data.get("request")
            wait = self._rethink_wait(session, data.get("rethink"))
            if handling != RequestHandling.WITHDRAW.value and wait:
                # 同意して考え直すと言っても、間引かれて何も起きない: それは言わせない
                error = (
                    f"rethink は今は使えない（{wait:.0f} 秒前に視聴者の指摘で小目標を考え直した"
                    "ばかり）。rethink は書かない。条件で書けるなら accept にして中目標に入れる、"
                    "そうでなければ今の小目標を続けると理由つきで言う（考え直す・変えるとは言わない）。"
                )
                logger.warning(f"返答 {attempt} の rethink は間が短くて使えない: {data!r}")
                continue
            problem = unanswered(kind, data)
            if problem and attempt < attempts:
                error = problem
                logger.warning(f"返答 {attempt} は{kind}を流している: {data!r}")
                continue
            if problem:
                # 最後の試しでも直らない: 黙るよりは話す（流したことは記録に残す）
                logger.warning(f"返答は{kind}を流したまま話す（{attempts} 回直らなかった）: {data!r}")
            if handling == RequestHandling.ACCEPT.value:
                try:
                    proposal = parse_proposal(data)
                    mid_goal = await self._accept(session, request.user_name, proposal, advice)
                except (ValueError, GoalRejectedError) as e:
                    # 返答が、プランに入らないものを受けている: それは決して言わせない
                    error = (
                        f"頼みは受けられなかった: {e}\n直せるなら直し、無理なら decline にして"
                        "返答で理由を言う。"
                    )
                    logger.warning(f"返答 {attempt} の頼みが使えない: {data!r}: {e}")
                    continue
                self._learn(session, request, data)
                if not proposal.now:
                    self._rethink(session, request.user_name, data.get("rethink"))
                return text, mid_goal
            if handling == RequestHandling.WITHDRAW.value:
                self._learn(session, request, data)
                await self._withdraw(session, request.user_name)
                return text, None
            if await self._empty_promise(request.message, text, str(handling), data.get("rethink")):
                # 返答が行動を約束しているのに、中目標にも考え直しにもならない: 約束を中目標に
                # する（今やるか後でやるかは Gemini が決める）。できなければ言わせない
                logger.info(f"返答 {attempt} は約束している。守り方を決める: {data!r}")
                kept = await self._keep_promise(
                    request, context, session, text, str(handling), advice
                )
                if isinstance(kept, UnkeptPromise):
                    if kept.reply:
                        # 同じ判断で書き直した返答を使う（返答を作り直さない: 同じコメントに 2 回
                        # 返答を作っていた。2026-09-27）。約束の教訓は覚えない
                        logger.info(
                            f"返答 {attempt} の約束は守れないので、約束を外して話す: "
                            f"{kept.reason} → {kept.reply}"
                        )
                        self._learn_reading(request, data.get("name_reading"))
                        return kept.reply, None
                    error = (
                        f"返答「{text}」は行動を約束しているが、中目標にできない（{kept.reason}）。"
                        "約束しない返答にする（覚えておく、普段からしている、まだできない）。"
                    )
                    logger.warning(f"返答 {attempt} の約束は守れない: {kept.reason}")
                    continue
                self._learn(session, request, data)
                if kept is None or not kept.now:
                    self._rethink(session, request.user_name, data.get("rethink"))
                return text, kept.mid_goal if kept is not None else None
            self._learn(session, request, data)
            self._rethink(session, request.user_name, data.get("rethink"))
            return text, None
        raise TextGenerationError(f"no usable reply after {attempts} attempts: {error}")

    async def _accept(
        self, session: PlaySession, user_name: str, proposal: MidGoalProposal, advice: bool = False
    ) -> MidGoal:
        """
        頼みを中目標に足す。今やる（now）なら、今の小目標を次のステップで区切り、その中目標の
        小目標を決めさせる（返答で「今やる」と言っている。待っている小目標は夜明けまで切れ目が
        来ない）。`advice`: 仕分けが助言と答えたコメント。配信者自身の中目標にする（提案者を
        残す。視聴者の頼みの上限に数えない）。

        Raises:
            GoalRejectedError: プランに入らないとき
        """
        assert self._mid_goals is not None
        mid_goal = await self._mid_goals.accept(
            session.plan, proposal, requested_by=user_name, advice=advice
        )
        if proposal.now:
            was = session.goal.describe() if session.goal else "nothing"
            session.request_rethink(
                f'you told {user_name} you would do "{mid_goal.title}" now '
                f"(it is at the top of the mid goals; you were on {was}, go back to it after)"
            )
        return mid_goal

    async def _keep_promise(
        self,
        request: GenerateResponseRequest,
        context: GenerationContext,
        session: PlaySession,
        reply: str,
        handling: str,
        advice: bool = False,
    ) -> KeptPromise | None | UnkeptPromise:
        """
        約束している返答の約束を中目標にする（docs/design/34 §10）。Gemini が今やる（小目標を
        区切る）か後でやるかを決める。約束していない（Jev の読み違い）か、リストの中目標がもう
        しているなら何も足さない（None）。中目標にできなければ、その理由と、約束を外して書き直した
        返答（UnkeptPromise）。
        今やるでも、チャットの指摘で小目標を切ったばかりなら後でやる（小目標をばたつかせない）。
        """
        system_prompt = self._prompt_builder.build_system_prompt(self._character, "promise")
        schema = promise_schema(allow_now=handling != RequestHandling.DECLINE.value)
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_promise_prompt(
                user_name=request.user_name,
                message=request.message,
                reply=reply,
                handling=handling,
                context=context,
                previous_error=error,
            )
            try:
                data = await self._text_generator.generate_json(
                    prompt, schema, system_instruction=system_prompt, purpose="promise"
                )
            except TextGenerationError as e:
                return UnkeptPromise(f"the decision failed: {e}")
            decision = data.get("decision")
            reason = str(data.get("reason", "")).strip()
            logger.info(f"約束の守り方 {attempt}: {decision}（{reason}）")
            if decision in (PromiseHandling.NONE.value, PromiseHandling.ALREADY.value):
                return None
            if decision not in (PromiseHandling.NOW.value, PromiseHandling.LATER.value):
                return UnkeptPromise(
                    reason or "it cannot be written as conditions",
                    str(data.get("rewritten_reply", "") or "").strip(),
                )
            if handling == RequestHandling.DECLINE.value:
                decision = PromiseHandling.LATER.value  # 断った返答では今やらない
            now = decision == PromiseHandling.NOW.value
            wait = self._rethink_wait(session, "now") if now else 0.0
            if wait:
                logger.info(f"{wait:.0f} 秒前に小目標を切ったばかりなので、約束は後でやる")
            pending = bool(session.rethink_reason)
            try:
                proposal = parse_proposal({**data, "when": "now" if now and not wait else "next"})
                mid_goal = await self._accept(session, request.user_name, proposal, advice)
            except (ValueError, GoalRejectedError) as e:
                error = f"中目標にできなかった: {e}\n直せるなら直し、無理なら cannot にする。"
                logger.warning(f"約束の中目標が使えない: {data!r}: {e}")
                continue
            if proposal.now and not pending:
                self._last_rethink_at = self._clock()
            return KeptPromise(mid_goal, proposal.now)
        return UnkeptPromise(error or "it could not become a mid goal")

    def _learn(
        self, session: PlaySession, request: GenerateResponseRequest, data: dict[str, object]
    ) -> None:
        """使う返答の名前の読み、教訓、自分の話を覚える（差し戻した返答からは覚えない）。"""
        self._learn_reading(request, data.get("name_reading"))
        if self._self_facts is not None:
            self._self_facts.remember(data.get("self_facts"), request.user_name)
        self._learn_lesson(session, request.user_name, data.get("lesson"), data.get("lesson_when"))

    async def _empty_promise(self, comment: str, text: str, handling: str, rethink: object) -> bool:
        """中目標にも考え直しにもならない返答（none / decline で rethink なし）が行動を約束しているか。"""
        if self._promise_check is None or not text:
            return False
        if isinstance(rethink, str) and rethink.strip():
            return False
        return await self._promise_check.promises(comment, text, handling)

    def _learn_reading(self, request: GenerateResponseRequest, reading: object) -> None:
        """返答の name_reading を覚える: 本人が読み方を言ったらその読み、知らない名前なら推測。"""
        if self._readings is None or not isinstance(reading, str) or not reading.strip():
            return
        if tells_reading(request.message):
            self._readings.learn(request.user_name, reading, VIEWER)
        elif self._readings.get(request.user_name) is None:
            self._readings.learn(request.user_name, reading, GUESS)

    def _rethink_wait(self, session: PlaySession, point: object) -> float:
        """
        返答の rethink が荒らし対策の間で使えないとき、前の指摘からの秒数（使えるなら 0）。
        まだ使われていない考え直しがあれば、それに足すだけで小目標をもう一度切らないので使える。
        """
        if not isinstance(point, str) or not point.strip() or session.rethink_reason:
            return 0.0
        if self._last_rethink_at is None:
            return 0.0
        elapsed = self._clock() - self._last_rethink_at
        return max(elapsed, 1.0) if elapsed < self._rethink_interval else 0.0

    def _rethink(self, session: PlaySession, user_name: str, point: object) -> None:
        """
        視聴者のもっともな指摘（近くに木がある、間違い、速いやり方）で、今の小目標を次の切れ目で
        終わらせ、その指摘を理由に決め直させる（小目標に固執しない）。続けては切らない（荒らし対策:
        間が短い rethink は `_rethink_wait` で書き直させる。まだ使われていない考え直しには足す）。
        """
        if not isinstance(point, str) or not point.strip():
            return
        if not session.rethink_reason:
            self._last_rethink_at = self._clock()
        was = session.goal.describe() if session.goal else "nothing"
        session.request_rethink(
            f"{user_name} pointed out in chat: {point.strip()} (you were on {was})"
        )
        logger.info(f"{user_name} の指摘で小目標を決め直す: {point}")

    async def _withdraw(self, session: PlaySession, user_name: str) -> None:
        """本人の頼みの中目標をやめる。それに向けた小目標なら、次の切れ目で決め直す。"""
        assert self._mid_goals is not None
        dropped = await self._mid_goals.withdraw(session.plan, user_name)
        if dropped is None:
            logger.info(f"{user_name} が取り下げた頼みはリストにない")
            return
        if session.goal is not None and session.goal.mid_goal_id == dropped.id:
            session.request_rethink(
                f'{user_name} withdrew the request "{dropped.title}" (you were on '
                f"{session.goal.describe()} for it)"
            )

    def _learn_lesson(
        self, session: PlaySession, user_name: str, lesson: object, when: object = None
    ) -> None:
        """
        納得したアドバイスを教訓として書く。教訓帳があればそこへ（条件のキーつき: 似た状況で
        思い出す。docs/design/35）、なければメモへ（同じ教訓は寿命を延ばす）。
        """
        if not isinstance(lesson, str) or not lesson.strip():
            return
        obs = session.last_observation
        if self._lessons is not None:
            when = when if isinstance(when, dict) else {}
            keys = clean_keys(when)
            if not keys:
                spec = session.goal.spec if session.goal is not None else None
                keys = situation_of(spec, obs)
            condition = when.get("text")
            self._lessons.learn(
                lesson,
                condition if isinstance(condition, str) else "",
                keys,
                explicit=bool(clean_keys(when)),
                source=f"viewer:{user_name}",
            )
            return
        if self._notes is None:
            return
        self._notes.learn_from_viewer(
            session.notebook, lesson, user_name, obs.day if obs is not None else None
        )
