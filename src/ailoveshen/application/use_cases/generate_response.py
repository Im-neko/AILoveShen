"""チャット返答生成のユースケースの実装。"""

from __future__ import annotations

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
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._event_publisher = event_publisher
        self._conversation = conversation
        self._character = character
        self._mid_goals = mid_goals
        self._history_limit = history_limit
        self._max_attempts = max_attempts

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
        system_prompt = self._prompt_builder.build_system_prompt(self._character)
        if session is None or self._mid_goals is None:
            prompt = self._prompt_builder.build_chat_response_prompt(
                user_name=request.user_name, message=request.message, context=context
            )
            text = await self._text_generator.generate(
                prompt=prompt, system_instruction=system_prompt
            )
            return text, None

        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_chat_response_prompt(
                user_name=request.user_name,
                message=request.message,
                context=context,
                takes_requests=True,
                previous_error=error,
            )
            data = await self._text_generator.generate_json(
                prompt, reply_schema(), system_instruction=system_prompt
            )
            text = str(data.get("reply", "")).strip()
            if data.get("request") != RequestHandling.ACCEPT.value:
                return text, None
            try:
                proposal = parse_proposal(data)
                mid_goal = await self._mid_goals.accept(
                    session.plan, proposal, requested_by=request.user_name
                )
            except (ValueError, GoalRejectedError) as e:
                # 返答が、プランに入らないものを受けている: それは決して言わせない
                error = str(e)
                logger.warning(f"返答 {attempt} の頼みが使えない: {data!r}: {error}")
                continue
            return text, mid_goal
        raise TextGenerationError(f"no usable reply after {self._max_attempts} attempts: {error}")
