"""Generate chat response use case implementation."""

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
    Use case for replying to a viewer's chat (sub loop / interrupt).

    While playing, the reply sees what the streamer is doing (the same view as
    the goal decision: the mission, the mid goals, the small goal) and may
    accept the viewer's request as a mid goal: the reply and its handling come
    from one generation, so a reply that accepts always adds the mid goal. It
    goes behind the mid goal worked on now; the small goal is not interrupted.
    A request that breaks the plan's limits (one per viewer, ...) or cannot be
    judged goes back to the model with the reason, to be declined.

    It coordinates:
    - Recording the viewer's chat in the conversation history
    - Prompt construction via adapter
    - Text (or reply + request handling) generation via adapter
    - Adding an accepted request to the mid goals
    - Recording the reply and publishing a domain event
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
        Initialize use case with dependencies (Dependency Injection).

        Args:
            text_generator: LLM text generator adapter
            prompt_builder: Prompt builder adapter
            event_publisher: Event publisher for domain events
            conversation: Conversation history shared with commentary and the goal decision
            character: The streamer's character profile
            mid_goals: Adds accepted requests to the mid goals (None: replies only talk)
            history_limit: Number of recent conversation messages given to the model
            max_attempts: Generations before giving up when the accepted request is unusable
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
        Execute the generate chat response use case.

        Flow:
        1. Record the viewer's chat
        2. Build generation context (with the session's activity) and prompts
        3. Generate the reply, adding the request to the mid goals when it accepts it
        4. Record the reply and publish ChatResponseGeneratedEvent
        """
        try:
            # History given to the model excludes the chat being answered,
            # which the prompt presents separately.
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

            logger.debug(f"Generating response for: {request.message[:50]}...")
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
            logger.error(f"Response generation failed: {e}")
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
                # The reply accepts something the plan does not take: never say it
                error = str(e)
                logger.warning(f"Reply {attempt} with an unusable request: {data!r}: {error}")
                continue
            return text, mid_goal
        raise TextGenerationError(f"no usable reply after {self._max_attempts} attempts: {error}")
