"""チャット返答生成のユースケースの実装。"""

from __future__ import annotations

import time
from collections.abc import Callable

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
    RequestHandling,
    parse_proposal,
    reply_schema,
)
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.application.use_cases.notes import NoteKeeper
from ailoveshen.application.use_cases.readings import GUESS, VIEWER, NameReadings, tells_reading
from ailoveshen.domain.entities import Conversation, PlaySession
from ailoveshen.domain.events import ChatResponseGeneratedEvent
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    GenerationContext,
    MessageType,
    MidGoal,
)


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
        if session is None or self._mid_goals is None:
            system_prompt = self._prompt_builder.build_system_prompt(self._character, "reply")
            prompt = self._prompt_builder.build_chat_response_prompt(
                user_name=request.user_name,
                message=request.message,
                context=context,
                name_reading=name_reading,
            )
            text = await self._text_generator.generate(
                prompt=prompt, system_instruction=system_prompt, purpose="reply"
            )
            return text, None

        system_prompt = self._prompt_builder.build_system_prompt(self._character, "reply_requests")
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_chat_response_prompt(
                user_name=request.user_name,
                message=request.message,
                context=context,
                takes_requests=True,
                previous_error=error,
                name_reading=name_reading,
            )
            data = await self._text_generator.generate_json(
                prompt, reply_schema(), system_instruction=system_prompt, purpose="reply"
            )
            self._learn_reading(request, data.get("name_reading"))
            self._learn_lesson(session, request.user_name, data.get("lesson"))
            text = str(data.get("reply", "")).strip()
            if data.get("request") == RequestHandling.WITHDRAW.value:
                await self._withdraw(session, request.user_name)
                return text, None
            if data.get("request") != RequestHandling.ACCEPT.value:
                self._rethink(session, request.user_name, data.get("rethink"))
                return text, None
            try:
                proposal = parse_proposal(data)
                mid_goal = await self._mid_goals.accept(
                    session.plan,
                    proposal,
                    requested_by=request.user_name,
                )
                if proposal.now:
                    # 今の小目標を次のステップで区切り、引き受けた頼みの小目標を決めさせる
                    # （返答で「今やる」と言っている。待っている小目標は夜明けまで切れ目が来ない）
                    was = session.goal.spec.describe() if session.goal else "nothing"
                    session.request_rethink(
                        f'you told {request.user_name} you would do "{mid_goal.title}" now '
                        f"(it is at the top of the mid goals; you were on {was}, go back to it "
                        "after)"
                    )
                else:
                    self._rethink(session, request.user_name, data.get("rethink"))
            except (ValueError, GoalRejectedError) as e:
                # 返答が、プランに入らないものを受けている: それは決して言わせない
                error = str(e)
                logger.warning(f"返答 {attempt} の頼みが使えない: {data!r}: {error}")
                continue
            return text, mid_goal
        raise TextGenerationError(f"no usable reply after {self._max_attempts} attempts: {error}")

    def _learn_reading(self, request: GenerateResponseRequest, reading: object) -> None:
        """返答の name_reading を覚える: 本人が読み方を言ったらその読み、知らない名前なら推測。"""
        if self._readings is None or not isinstance(reading, str) or not reading.strip():
            return
        if tells_reading(request.message):
            self._readings.learn(request.user_name, reading, VIEWER)
        elif self._readings.get(request.user_name) is None:
            self._readings.learn(request.user_name, reading, GUESS)

    def _rethink(self, session: PlaySession, user_name: str, point: object) -> None:
        """
        視聴者のもっともな指摘（近くに木がある、間違い、速いやり方）で、今の小目標を次の切れ目で
        終わらせ、その指摘を理由に決め直させる（小目標に固執しない）。続けては使えない（荒らし対策）。
        """
        if not isinstance(point, str) or not point.strip():
            return
        now = self._clock()
        if (
            self._last_rethink_at is not None
            and now - self._last_rethink_at < self._rethink_interval
        ):
            logger.info(f"{user_name} の指摘で決め直すのは見送った（間が短い）: {point}")
            return
        self._last_rethink_at = now
        was = session.goal.spec.describe() if session.goal else "nothing"
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
                f"{session.goal.spec.describe()} for it)"
            )

    def _learn_lesson(self, session: PlaySession, user_name: str, lesson: object) -> None:
        """納得したアドバイスを教訓としてメモに書く（同じ教訓は寿命を延ばす）。"""
        if self._notes is None or not isinstance(lesson, str) or not lesson.strip():
            return
        obs = session.last_observation
        self._notes.learn_from_viewer(
            session.notebook, lesson, user_name, obs.day if obs is not None else None
        )
