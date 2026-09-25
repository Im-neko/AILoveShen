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
    parse_goal,
    predicates_now,
    reply_schema,
)
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import ChatResponseGeneratedEvent
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    GenerationContext,
    Goal,
    GoalPredicate,
    MessageType,
    ViewerRequest,
)


class GenerateResponseUseCase(IGenerateResponse):
    """
    Use case for replying to a viewer's chat (sub loop / interrupt).

    While playing, the reply sees what the streamer is doing (the same view as
    the goal decision) and may take the viewer's request: the reply and the
    goal come from one generation, so a reply that promises an action always
    carries its goal. The goal is left in the session as a request; the step
    loop applies it at the next step (it alone changes the goal).

    It coordinates:
    - Recording the viewer's chat in the conversation history
    - Prompt construction via adapter
    - Text (or reply + goal) generation via adapter
    - Leaving a taken request in the session
    - Recording the reply and publishing a domain event
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IPromptBuilder,
        event_publisher: IEventPublisher,
        conversation: Conversation,
        character: CharacterProfile,
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
            history_limit: Number of recent conversation messages given to the model
            max_attempts: Generations before giving up when the reply's goal is unusable
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._event_publisher = event_publisher
        self._conversation = conversation
        self._character = character
        self._history_limit = history_limit
        self._max_attempts = max_attempts

    async def execute(self, request: GenerateResponseRequest) -> GenerateResponseResponse:
        """
        Execute the generate chat response use case.

        Flow:
        1. Record the viewer's chat
        2. Build generation context (with the session's activity) and prompts
        3. Generate the reply, with a goal when it takes the request
        4. Leave the request in the session, record the reply and publish
           ChatResponseGeneratedEvent
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
            obs = activity.observation if activity is not None else None
            predicates = predicates_now(obs) if obs is not None else []
            context = GenerationContext(
                emotion_state=request.emotion_state,
                activity=activity,
                recent_messages=history,
            )

            logger.debug(f"Generating response for: {request.message[:50]}...")
            text, goal = await self._generate(request, context, predicates)

            if text:
                if goal is not None and session is not None:
                    session.request_goal(
                        ViewerRequest(
                            goal=goal, user_name=request.user_name, message=request.message
                        )
                    )
                    logger.info(
                        f"Viewer request taken: {goal.spec.describe()} ({request.user_name})"
                    )
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
                goal=goal if text else None,
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
        predicates: list[GoalPredicate],
    ) -> tuple[str, Goal | None]:
        system_prompt = self._prompt_builder.build_system_prompt(self._character)
        if not predicates:
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
                predicates=predicates,
                previous_error=error,
            )
            data = await self._text_generator.generate_json(
                prompt, reply_schema(predicates), system_instruction=system_prompt
            )
            text = str(data.get("reply", "")).strip()
            if not data.get("change_goal"):
                return text, None
            try:
                goal = parse_goal(data, requested_by=request.user_name)
                if goal.spec.predicate not in predicates:
                    raise ValueError(f"{goal.spec.predicate.value} is not one of the goals offered")
            except ValueError as e:
                # The reply promises something no goal stands for: never say it
                error = str(e)
                logger.warning(f"Reply {attempt} with an unusable goal: {data!r}: {error}")
                continue
            return text, goal
        raise TextGenerationError(
            f"no reply with a usable goal after {self._max_attempts} attempts"
        )
