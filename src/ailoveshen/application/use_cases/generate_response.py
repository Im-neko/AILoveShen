"""チャット返答生成のユースケースの実装。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

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
    AdviceRelevance,
    DeclineReason,
    ObjectiveProposal,
    PromiseHandling,
    RequestHandling,
    parse_proposal,
    promise_schema,
    reply_schema,
)
from ailoveshen.application.use_cases.objectives import ObjectiveKeeper
from ailoveshen.application.use_cases.lessons import LessonBook, clean_keys, situation_of
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.foreign_speech import is_foreign_comment, mostly_japanese
from ailoveshen.application.use_cases.promise_check import PromiseCheck
from ailoveshen.application.use_cases.reply_outcome import ALREADY, LATER, MET, NOW, ReplyOutcome
from ailoveshen.application.use_cases.reply_facts import ReplyFactCheck
from ailoveshen.application.use_cases.readings import GUESS, VIEWER, NameReadings, tells_reading
from ailoveshen.application.use_cases.self_facts import SelfFacts
from ailoveshen.domain.entities import Conversation, PlaySession, PromisedNow
from ailoveshen.domain.events import ChatResponseGeneratedEvent
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    GenerationContext,
    GoalPredicate,
    MessageType,
    Objective,
)


# 返事の前の仕分けで、答えを必ず行動か理由にする種類（頼み・助言）
ACTIONABLE = ("request", "advice")
ACTIONABLE_ATTEMPTS = 3  # これらは差し戻しが増えるので、試す回数を増やす（最後は黙らずに話す）
# 今の小目標の行動がこの回数続けて失敗しているときに「後で」と採った助言は、失敗の理由の指摘ではないか
# 1 度だけ聞き直す（2026-09-28: 家に入れない理由の「家の前の穴を埋めて」を 3 番目の大目標にし、
# 家に入る失敗を続けた）
FAILING_ACTIONS = 2
FINAL_ATTEMPTS = 2  # 結果に合わせた返答の書き直し（合わなければ 1 回だけ直させる）


def unanswered(kind: str | None, data: dict[str, object]) -> str:
    """
    頼み・助言への答えが、何も残さずに流しているか（2026-09-28: 「今は○○中だから後で」と
    none / decline にして、大目標にも考え直しにも教訓にもならなかった）。差し戻す理由か空文字列。
    """
    handling = data.get("request")
    if kind == "withdraw":
        if handling == RequestHandling.WITHDRAW.value:
            return ""
        return (
            "このコメントは、視聴者が自分の頼みや助言を取り下げたもの。request は withdraw にする"
            "（その人のものだけが大目標・中目標から外れる。ないときは何も起きない）。返答で"
            "「要らない」と言うなら、リストからも外す。"
        )
    if kind not in ACTIONABLE:
        return ""  # 仕分けがない・Jev が答えなかった: 今までどおり（約束の確かめが後ろで拾う）
    if handling == RequestHandling.DECLINE.value:
        reason = data.get("decline_reason")
        allowed = [r.value for r in DeclineReason]
        if reason not in allowed:
            return (
                f"decline には decline_reason（{' / '.join(allowed)}）が要る。今は別のことを"
                "している・今の目標が先、は理由にならない: やる価値があるなら accept の when=next で"
                "大目標の後ろに入れる（返答でもそう言う）。"
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


def night_doable(proposal: ObjectiveProposal) -> bool:
    """
    夜の家の中で、待たずに今できる頼みか（持っているベッドを家に置く: ブリッジが through_night の手に
    place_bed を出す）。これだけは、夜の間は小目標を切り替えない決まり（`defers_rethink`）の例外にする。
    """
    return bool(proposal.conditions) and all(
        c.predicate == GoalPredicate.PLACED
        and c.where == "home"
        and (c.item == "bed" or str(c.item).endswith("_bed"))
        for c in proposal.conditions
    )


def unjudged(kind: str | None, data: dict[str, object], can_rethink: bool) -> str:
    """
    助言・提案への答えが、今の目標との関わりを見ていないか、関わると認めたのに今の小目標を決め直さ
    ないか（助言が来ても、教訓や後ろの大目標になるだけで、今の小目標が見直されなかった）。差し戻す
    理由か空文字列。`can_rethink`: 荒らし対策の間が空いていて rethink が使える。
    """
    handling = data.get("request")
    if handling not in (RequestHandling.ACCEPT.value, RequestHandling.NONE.value):
        return ""  # 断りには理由がある。取り下げは助言ではない
    if kind != "advice" and not (handling == RequestHandling.ACCEPT.value and data.get("suggestion") is True):
        return ""
    relevance = data.get("relevance")
    allowed = [r.value for r in AdviceRelevance]
    if relevance not in allowed:
        return (
            f"助言には relevance（{' / '.join(allowed)}）が要る。「今していること」の今の小目標・"
            "中目標・大目標と比べて、どれだけ関わるかを relevance_reason に 1 文で書く。"
        )
    if relevance != AdviceRelevance.RELATED.value:
        return ""
    if data.get("when") == "now":
        return ""  # 小目標はその大目標のために区切られる
    point = data.get("rethink")
    if isinstance(point, str) and point.strip():
        return ""
    if not can_rethink:
        if handling != RequestHandling.ACCEPT.value:
            return ""
        # rethink は間引かれて使えない。引き受けて「今の作業の後」に回すと、関わる（今の作業に効く）と
        # 言いながら後で作ると言う食い違いになる（「斧があれば楽」「材料集めが終わったら斧を作る」）
        return (
            "この助言は今の小目標か中目標に関わる（related）と見ているのに、今の作業の後（when=next）に"
            "回すと、その効果は今の作業に届かず、返答も食い違う（効くと言いながら後で作ると言う）。"
            "今やるほうがよければ when=now にして、返答で「今やるね」と理由を言う。後でよいなら、"
            "relevance を partly にして、なぜ後でよいか（今の作業を途中でやめると無駄が大きい、など）を"
            "返答で言う。"
        )
    return (
        "この助言は今の小目標か中目標に関わる（related）と見ていて、納得して受けるなら、今の小目標を"
        "そのまま続けない: rethink に何を考え直すかを英語 1 文で書く（大目標にするなら when=now でもよい）。"
        "今の小目標が実は続けるべきなら、relevance を partly か unrelated にして、返答で理由を言う。"
        "当たっていなければ decline（wrong）にする。"
    )


@dataclass(frozen=True)
class KeptPromise:
    """返答の約束から足した大目標（now: 今の小目標を区切って今やる）。"""

    objective: Objective | None  # None: 建物の設計を裏でしていて、まだ足されていない
    now: bool
    outcome: ReplyOutcome


@dataclass(frozen=True)
class UnkeptPromise:
    """大目標にできない約束: 理由と、約束を外して書き直した返答（空なら返答から作り直す）。"""

    reason: str
    reply: str = ""


class GenerateResponseUseCase(IGenerateResponse):
    """
    視聴者のチャットに返答するユースケース（サブループ / 割り込み）。

    プレイ中の返答は、配信者が今していること（目標の決定と同じもの: 最終目標、
    大目標、小目標）を見て、視聴者の頼みを大目標として受けることがある。返答と
    頼みの扱いは 1 回の生成から出るので、受けると言った返答は必ず大目標を足す。
    足した大目標は、今取り組んでいる大目標の後ろに入る。小目標は中断しない。
    プランの上限（1 人 1 つ、など）を破る頼みや判定できない頼みは、理由をつけて
    モデルに戻し、断らせる。

    次のことをまとめる:
    - 視聴者のチャットを会話履歴に記録する
    - アダプターでプロンプトを組み立てる
    - アダプターでテキスト（または返答と頼みの扱い）を生成する
    - 受けた頼みを大目標に足す
    - 返答を記録し、ドメインイベントを発行する
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IPromptBuilder,
        event_publisher: IEventPublisher,
        conversation: Conversation,
        character: CharacterProfile,
        objectives: ObjectiveKeeper | None = None,
        history_limit: int = 10,
        commentary_limit: int = 3,
        max_attempts: int = 2,
        readings: NameReadings | None = None,
        notes: NoteKeeper | None = None,
        lessons: LessonBook | None = None,
        promise_check: PromiseCheck | None = None,
        fact_check: ReplyFactCheck | None = None,
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
            objectives: 受けた頼みを大目標に足す（None: 返答は話すだけ）
            history_limit: モデルに渡す、視聴者のコメントと自分の返事の最近の数
            commentary_limit: それに加えて渡す、自分の実況（独り言）の最近の数
            max_attempts: 受けた頼みが使えないとき、あきらめるまでに生成する回数
            readings: 視聴者の名前の読みの辞書（プロンプトに出し、返答の name_reading で覚える。
                docs/design/30_name_readings.md）
            notes: 自分のメモ（納得した視聴者のアドバイスを教訓として書く。None なら書かない）
            lessons: 教訓帳（あれば、納得したアドバイスはメモではなく、条件つきでこちらに書く:
                似た状況で思い出す。docs/design/35）
            promise_check: 返答が行動を約束しているのに大目標にも考え直しにもならないかを
                確かめる（Jev）。約束していれば Gemini が守り方（今やる / 後でやる）を決めて
                大目標にし、できなければ書き直させる（docs/design/34 §10。None なら確かめない）
            fact_check: 返答が、持っている物を「ない・見つからない」と言っていないかを確かめる（Jev）。
                言っていれば事実を添えて書き直させる（None なら確かめない）
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
        self._objectives = objectives
        self._history_limit = history_limit
        self._commentary_limit = commentary_limit
        self._max_attempts = max_attempts
        self._readings = readings
        self._notes = notes
        self._lessons = lessons
        self._promise_check = promise_check
        self._fact_check = fact_check
        self._self_facts = self_facts
        self._rethink_interval = rethink_interval_seconds
        self._clock = clock
        self._last_rethink_at: float | None = None

    async def execute(self, request: GenerateResponseRequest) -> GenerateResponseResponse:
        """
        チャット返答生成のユースケースを実行する。

        流れ:
        1. 生成の文脈（セッションの activity を含む）とプロンプトを組み立てる
        2. 返答を生成する。頼みを受けたときは、それを大目標に足す
        3. 視聴者のチャットと返答を記録し、ChatResponseGeneratedEvent を発行する
        """
        try:
            # モデルに渡す履歴には、返答するチャットを含めない。
            # そのチャットはプロンプトが別に示す。
            # 視聴者との往復を、実況に押し出されないように取り、時刻の順に並べる（設計書 80 の下の
            # 設計書 81: コメントとの会話の流れと時系列）
            history = self._conversation.recent_chat_flow(
                self._history_limit, self._commentary_limit
            )

            session = request.session
            activity = session.activity() if session is not None else None
            context = GenerationContext(
                emotion_state=request.emotion_state,
                activity=activity,
                recent_messages=history,
                now=datetime.now(timezone.utc),
                comment_received_at=request.received_at,
                waiting_comments=request.waiting,
            )

            logger.debug(f"返答を生成する: {request.message[:50]}...")
            try:
                text, objective = await self._generate(request, context, session)
            finally:
                # 返事を書いている間は会話に入れない: 入れると、その間に書く実況が、返事のない
                # コメントを見て先に答える（2026-09-30: 実況が「ごめんね、あとで」と言った 7 秒後に
                # 返事が「その通り、変更するね」と逆を言った）
                self._conversation.add_viewer_message(
                    content=request.message,
                    user_name=request.user_name,
                    user_id=request.user_id,
                    received_at=request.received_at,
                )

            if text:
                self._conversation.add_streamer_message(
                    text, MessageType.RESPONSE, addressed_to=request.user_name
                )
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
                objective=objective,
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
    ) -> tuple[str, Objective | None]:
        known = self._readings.get(request.user_name) if self._readings else None
        name_reading = known.reading if known else ""
        self_facts = self._self_facts.all() if self._self_facts else ()
        if session is None or self._objectives is None:
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
        asked_now = False
        kind = request.kind or ("request" if request.producer else None)
        advice = kind == "advice"
        attempts = (
            max(self._max_attempts, ACTIONABLE_ATTEMPTS)
            if kind in (*ACTIONABLE, "withdraw")
            else self._max_attempts
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
                producer=request.producer,
            )
            data = await self._text_generator.generate_json(
                prompt, reply_schema(), system_instruction=system_prompt, purpose="reply"
            )
            text = str(data.get("reply", "")).strip()
            handling = data.get("request")
            if is_foreign_comment(request.message) and mostly_japanese(text) and attempt < attempts:
                # 日本語以外のコメントに日本語で返した（2026-10-10: 英語に日本語で返した）: その言語で書き直させる
                error = (
                    f"The viewer's comment is not in Japanese, but the reply \"{text}\" is. Write the reply in the "
                    "same language as the comment (keep it short; the viewer's name may stay in Japanese)."
                )
                logger.warning(f"返答 {attempt} は日本語以外のコメントに日本語で返している: {data!r}")
                continue
            # 運営の指示は、視聴者の指摘のように間引かない
            wait = 0.0 if request.producer else self._rethink_wait(session, data.get("rethink"))
            if handling != RequestHandling.WITHDRAW.value and wait:
                # 同意して考え直すと言っても、間引かれて何も起きない: それは言わせない
                error = (
                    f"rethink は今は使えない（{wait:.0f} 秒前に視聴者の指摘で小目標を考え直した"
                    "ばかり）。rethink は書かない。条件で書けるなら accept にして大目標に入れる、"
                    "そうでなければ今の小目標を続けると理由つきで言う（考え直す・変えるとは言わない）。"
                )
                logger.warning(f"返答 {attempt} の rethink は間が短くて使えない: {data!r}")
                continue
            problem = unanswered(kind, data) or unjudged(
                kind, data, not self._rethink_wait(session, "check")
            )
            if problem and attempt < attempts:
                error = problem
                logger.warning(f"返答 {attempt} は{kind}を流している: {data!r}")
                continue
            if problem:
                # 最後の試しでも直らない: 黙るよりは話す（流したことは記録に残す）
                logger.warning(f"返答は{kind}を流したまま話す（{attempts} 回直らなかった）: {data!r}")
            if data.get("relevance"):
                logger.info(
                    f"{request.user_name} の助言は今の目標と {data.get('relevance')}: "
                    f"{data.get('relevance_reason', '')}"
                )
            if handling == RequestHandling.ACCEPT.value:
                try:
                    proposal = parse_proposal(data)
                    # 助言か: 仕分けの答えか、返答自身の見立て（Jev の仕分けは頼み・質問・助言の
                    # 境目で迷う: 「畑広げない？」が頼み、「畑拡張は？」が質問だった。2026-09-28）
                    # 運営の指示は、自分の大目標にする（視聴者の頼みの上限に数えない）
                    suggestion = advice or request.producer or data.get("suggestion") is True
                    failing = session.consecutive_failures if session.goal else 0
                    if (
                        suggestion
                        and not proposal.now
                        and not asked_now
                        and failing >= FAILING_ACTIONS
                        and attempt < attempts
                    ):
                        asked_now = True
                        assert session.goal is not None
                        error = (
                            f"今の小目標 {session.goal.describe()} は、行動が {failing} 回続けて失敗して"
                            "いる。この助言がその失敗の理由や解き方の指摘なら、後回しにしない: when=now に"
                            "して rethink も書く（返答でも今やると言う）。関係のない助言なら、同じ答えで"
                            "よい。"
                        )
                        logger.warning(f"返答 {attempt} は失敗中に助言を後回しにした: {data!r}")
                        continue
                    timing, when_note = self._timing(session, proposal)
                    objective = None
                    if await self._objectives.all_met(proposal.conditions):
                        # 頼まれたことはもうできている: 大目標にも考え直しにもしない（2026-10-10 22:11:
                        # ベッドは家にあるのに「ベッドで寝て」を大目標にして「今やるね」と言い、46 秒後に達成した）
                        timing, when_note = MET, self._met_note(proposal)
                    else:
                        objective = await self._accept(
                            session, request.user_name, proposal, suggestion
                        )
                except (ValueError, GoalRejectedError) as e:
                    # 返答が、プランに入らないものを受けている: それは決して言わせない
                    error = (
                        f"頼みは受けられなかった: {e}\n直せるなら直し、無理なら decline にして"
                        "返答で理由を言う。"
                    )
                    logger.warning(f"返答 {attempt} の頼みが使えない: {data!r}: {e}")
                    if attempt >= attempts and kind == "advice":
                        # 最後の試しでも助言の形が直らない: 黙って返事を落とさない。教訓は覚え、大目標にはせず、
                        # 覚えておくと言う返事を話す（2026-10-10 19:39: 「苗木の上に土を置かないで」に、条件の
                        # 形が 3 回合わず、返事が出なかった）
                        self._learn(session, request, data)
                        return text, None
                    continue
                self._learn(session, request, data)
                if not proposal.now:
                    self._rethink(session, request.user_name, data.get("rethink"))
                outcome = self._accepted(proposal, timing, when_note)
                return await self._final(request, context, outcome, text), objective
            if handling == RequestHandling.WITHDRAW.value:
                self._learn(session, request, data)
                await self._withdraw(session, request.user_name)
                return await self._final(request, context, ReplyOutcome("withdrawn"), text), None
            if await self._empty_promise(request.message, text, str(handling), data.get("rethink")):
                # 返答が行動を約束しているのに、大目標にも考え直しにもならない: 約束を大目標に
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
                        f"返答「{text}」は行動を約束しているが、大目標にできない（{kept.reason}）。"
                        "約束しない返答にする（覚えておく、普段からしている、まだできない）。"
                    )
                    logger.warning(f"返答 {attempt} の約束は守れない: {kept.reason}")
                    continue
                self._learn(session, request, data)
                if kept is None or not kept.now:
                    self._rethink(session, request.user_name, data.get("rethink"))
                if kept is None:
                    return text, None  # もう大目標にある: 時期は言えないので、返答はそのまま
                return await self._final(request, context, kept.outcome, text), kept.objective
            # 持っている物を「ない・見つからない」と言っている: 事実を添えて 1 回だけ書き直させる
            # （最後の試しでも直らなければ話す）
            if self._fact_check is not None and attempt < attempts:
                wrong = await self._fact_check.problem(text, context.activity)
                if wrong:
                    error = wrong
                    logger.warning(f"返答 {attempt} は持ち物の事実と食い違う: {data!r}")
                    continue
            self._learn(session, request, data)
            self._rethink(session, request.user_name, data.get("rethink"))
            outcome = self._plain_outcome(kind, data)
            if outcome is None:
                return text, None
            return await self._final(request, context, outcome, text), None
        raise TextGenerationError(f"no usable reply after {attempts} attempts: {error}")

    @staticmethod
    def _timing(session: PlaySession, proposal: ObjectiveProposal) -> tuple[str, str]:
        """
        受ける頼みが実際にいつやられるか（コードが決める。返答の文ではなく、これが事実）。
        今やる（when=now）でも、夜の待ちは小目標を切り替えない決まり（`defers_rethink`）で夜明けまで
        動かない。例外は、夜の家の中で今できる頼み（`night_doable`）。
        """
        current = session.plan.current
        first = current.open_jobs[0] if current is not None and current.open_jobs else None
        if (
            proposal.job
            and first is not None
            and {c.describe() for c in first.specs} == {c.describe() for c in proposal.conditions}
        ):
            return ALREADY, ""
        if proposal.now:
            if session.defers_rethink() and not night_doable(proposal):
                return LATER, "夜の間は今の小目標（夜を越す）を切り替えない決まりなので、夜が明けてからやる。"
            return NOW, ""
        if proposal.job and first is not None:
            return LATER, f"今の中目標「{first.title or first.spec.describe()}」のあとにやる。"
        if current is not None:
            return LATER, f"今の大目標「{current.title}」のあとにやる（リストの順番どおり）。"
        return LATER, "リストの順番どおり、今のことのあとにやる。"

    @staticmethod
    def _met_note(proposal: ObjectiveProposal) -> str:
        """もうできている頼みに添える、本当のこと。ベッドなら、寝るのは夜になってから。"""
        if any(str(c.item).endswith("bed") or c.item == "bed" for c in proposal.conditions):
            return "ベッドは家にある。寝られるのは夜になってからで（昼は寝られない）、夜になれば家のベッドで寝る。"
        return ""

    @staticmethod
    def _accepted(proposal: ObjectiveProposal, timing: str, when_note: str) -> ReplyOutcome:
        return ReplyOutcome(
            "accepted",
            title=proposal.title or proposal.conditions[0].describe(),
            what=", ".join(c.describe() for c in proposal.conditions),
            level="job" if proposal.job else "objective",
            timing=timing,
            when_note=when_note,
        )

    @staticmethod
    def _plain_outcome(kind: str | None, data: dict[str, object]) -> ReplyOutcome | None:
        """大目標にしなかった返答（断った・教訓や考え直し・何もしない）の結果。話すだけの雑談は None。"""
        handling = data.get("request")
        lesson = str(data.get("lesson") or "").strip()
        rethink = str(data.get("rethink") or "").strip()
        if handling == RequestHandling.DECLINE.value:
            detail = str(data.get("reason") or data.get("relevance_reason") or "").strip()
            return ReplyOutcome(
                "declined", decline_reason=str(data.get("decline_reason") or ""), detail=detail
            )
        if lesson or rethink:
            return ReplyOutcome("noted", lesson=lesson, rethink=rethink)
        if kind in ACTIONABLE:
            return ReplyOutcome("none")
        return None

    async def _final(
        self,
        request: GenerateResponseRequest,
        context: GenerationContext,
        outcome: ReplyOutcome,
        draft: str,
    ) -> str:
        """
        決まった行動に合わせて返答を書き直す（docs/design/159）。下書きは行動と同じ生成で書かれていて、
        行動を後から直されると食い違う。書き直した文が結果と合わなければ 1 回だけ直させ、それでも
        合わなければ、結果だけから作った一言を話す（食い違った文は話さない）。
        """
        system_prompt = self._prompt_builder.build_system_prompt(self._character, "reply")
        error = ""
        for attempt in range(1, FINAL_ATTEMPTS + 1):
            prompt = self._prompt_builder.build_final_reply_prompt(
                user_name=request.user_name,
                message=request.message,
                context=context,
                facts=outcome.facts(),
                draft=draft,
                previous_error=error,
            )
            try:
                text = (
                    await self._text_generator.generate(
                        prompt=prompt, system_instruction=system_prompt, purpose="reply_final"
                    )
                ).strip()
            except TextGenerationError as e:
                logger.warning(f"返答の書き直しに失敗した: {e}")
                return draft if not outcome.mismatch(draft) else outcome.fallback(request.user_name)
            problem = await self._final_problem(request, context, outcome, text)
            if not problem:
                return text
            error = problem
            logger.warning(f"書き直した返答 {attempt} が結果と合わない: {text!r}: {problem}")
        logger.warning(f"返答が結果と合わないので、結果だけから作った一言を話す: {outcome}")
        return outcome.fallback(request.user_name)

    async def _final_problem(
        self,
        request: GenerateResponseRequest,
        context: GenerationContext,
        outcome: ReplyOutcome,
        text: str,
    ) -> str:
        """書き直した返答が、結果・言語・持ち物の事実と合わない理由（合っていれば空文字列）。"""
        if not text:
            return "The reply is empty."
        problem = outcome.mismatch(text)
        if problem:
            return problem
        if is_foreign_comment(request.message) and mostly_japanese(text):
            return (
                "The viewer's comment is not in Japanese, but the reply is. Write it in the same "
                "language as the comment."
            )
        if outcome.handling in ("declined", "none") and await self._empty_promise(
            request.message, text, "decline" if outcome.handling == "declined" else "none", None
        ):
            return "The reply promises an action, but nothing was registered to do (see the facts)."
        if self._fact_check is not None:
            return await self._fact_check.problem(text, context.activity) or ""
        return ""

    async def _accept(
        self,
        session: PlaySession,
        user_name: str,
        proposal: ObjectiveProposal,
        advice: bool = False,
    ) -> Objective | None:
        """
        頼みを大目標に足す。今やる（now）なら、今の小目標を次のステップで区切り、その大目標の
        小目標を決めさせる（返答で「今やる」と言っている。待っている小目標は夜明けまで切れ目が
        来ない）。`advice`: 仕分けが助言と答えたコメント。配信者自身の大目標にする（提案者を
        残す。視聴者の頼みの上限に数えない）。

        足すのが裏の設計を待つとき（まだない建物）は None（足せたら今やる考え直しをする。断られたら配信が話す）。

        Raises:
            GoalRejectedError: プランに入らないとき
        """
        assert self._objectives is not None
        current = session.plan.current
        first = current.open_jobs[0] if current is not None and current.open_jobs else None
        already_current = (
            proposal.job
            and first is not None
            and {c.describe() for c in first.specs} == {c.describe() for c in proposal.conditions}
        )

        urgent = night_doable(proposal)

        def rethink(objective: Objective) -> None:
            if not proposal.now:
                return
            was = session.goal.describe() if session.goal else "nothing"
            if not proposal.job:
                session.promise_now(
                    PromisedNow(user_name, objective.title, proposal.conditions, job=False)
                )
                session.request_rethink(
                    f'you told {user_name} you would do "{objective.title}" now '
                    f"(it is at the top of the objectives; you were on {was}, go back to it after)",
                    urgent=urgent,
                )
                return
            # 中目標として採ったとき、約束したのはその中目標で、大目標の名前ではない。もう今の中目標なら、
            # 小目標を区切らない（2026-10-10: 今の工房の中目標を採ったのに、大目標の「自分の家を作る」を
            # 約束したことにして、家の仕上げに切り替え、工房を止めた）
            if already_current:
                return
            what = proposal.title or proposal.conditions[0].describe()
            session.promise_now(PromisedNow(user_name, what, proposal.conditions, job=True))
            session.request_rethink(
                f'you told {user_name} you would do "{what}" now (a job of the objective '
                f'"{objective.title}"; you were on {was}, go back to it after)',
                urgent=urgent,
            )

        # 建物の設計（Gemini で 1 回 40〜200 秒）は返事のあとに裏でする: 返事が待つと、あとのコメントごと遅れる
        # （2026-10-07: 8 分）。そのときは大目標がまだ足されていないので、今やるなら考え直しは足せてから
        objective = await self._objectives.accept(
            session.plan, proposal, requested_by=user_name, advice=advice, on_added=rethink
        )
        if objective is not None:
            rethink(objective)
        return objective

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
        約束している返答の約束を大目標にする（docs/design/34 §10）。Gemini が今やる（小目標を
        区切る）か後でやるかを決める。約束していない（Jev の読み違い）か、リストの大目標がもう
        しているなら何も足さない（None）。大目標にできなければ、その理由と、約束を外して書き直した
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
            if data.get("level") not in ("objective", "job"):
                # 層を先に決めさせる（層のない約束は、必ず大目標になっていた: 「ツルハシを作る」が大目標に入った）
                error = (
                    "level is missing: decide first whether this is an objective of its own or a job "
                    "of the objective worked on now, then write the title and conditions"
                )
                logger.warning(f"約束の層がない: {data!r}")
                continue
            if handling == RequestHandling.DECLINE.value:
                decision = PromiseHandling.LATER.value  # 断った返答では今やらない
            now = decision == PromiseHandling.NOW.value
            wait = self._rethink_wait(session, "now") if now else 0.0
            if wait:
                logger.info(f"{wait:.0f} 秒前に小目標を切ったばかりなので、約束は後でやる")
            pending = bool(session.rethink_reason)
            try:
                proposal = parse_proposal({**data, "when": "now" if now and not wait else "next"})
                timing, when_note = self._timing(session, proposal)
                objective = await self._accept(session, request.user_name, proposal, advice)
            except (ValueError, GoalRejectedError) as e:
                error = f"大目標にできなかった: {e}\n直せるなら直し、無理なら cannot にする。"
                logger.warning(f"約束の大目標が使えない: {data!r}: {e}")
                continue
            if proposal.now and not pending:
                self._last_rethink_at = self._clock()
            return KeptPromise(objective, proposal.now, self._accepted(proposal, timing, when_note))
        return UnkeptPromise(error or "it could not become an objective")

    def _learn(
        self, session: PlaySession, request: GenerateResponseRequest, data: dict[str, object]
    ) -> None:
        """使う返答の名前の読み、教訓、自分の話を覚える（差し戻した返答からは覚えない）。"""
        self._learn_reading(request, data.get("name_reading"))
        if self._self_facts is not None:
            self._self_facts.remember(data.get("self_facts"), request.user_name)
        self._learn_lesson(session, request.user_name, data.get("lesson"), data.get("lesson_when"))

    async def _empty_promise(self, comment: str, text: str, handling: str, rethink: object) -> bool:
        """大目標にも考え直しにもならない返答（none / decline で rethink なし）が行動を約束しているか。"""
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
        """本人の頼みの大目標をやめる。それに向けた小目標なら、次の切れ目で決め直す。"""
        assert self._objectives is not None
        dropped = await self._objectives.withdraw(session.plan, user_name)
        if dropped is None:
            logger.info(f"{user_name} が取り下げた頼みはリストにない")
            return
        if session.goal is not None and session.goal.objective_id == dropped.id:
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
