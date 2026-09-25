"""Generate commentary use case implementation."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateCommentaryResponse,
)
from ailoveshen.application.ports.input.generate_commentary import IGenerateCommentary
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.prompt_builder import IPromptBuilder
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import CommentaryGeneratedEvent
from ailoveshen.domain.value_objects import CharacterProfile, GenerationContext, MessageType


class GenerateCommentaryUseCase(IGenerateCommentary):
    """
    Use case for generating game commentary (main loop).

    It coordinates:
    - Building the generation context from the request and conversation history
    - Prompt construction via adapter
    - Text generation via adapter
    - Recording the utterance and publishing a domain event
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IPromptBuilder,
        event_publisher: IEventPublisher,
        conversation: Conversation,
        character: CharacterProfile,
        max_recent_events: int = 3,
        history_limit: int = 10,
    ) -> None:
        """
        Initialize use case with dependencies (Dependency Injection).

        Args:
            text_generator: LLM text generator adapter
            prompt_builder: Prompt builder adapter
            event_publisher: Event publisher for domain events
            conversation: Conversation history shared with the chat response use case
            character: The streamer's character profile
            max_recent_events: Number of recent game events given to the model
            history_limit: Number of recent conversation messages given to the model
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._event_publisher = event_publisher
        self._conversation = conversation
        self._character = character
        self._max_recent_events = max_recent_events
        self._history_limit = history_limit

    async def execute(self, request: GenerateCommentaryRequest) -> GenerateCommentaryResponse:
        """
        Execute the generate commentary use case.

        Flow:
        1. Build generation context
        2. Build prompts
        3. Generate text
        4. Record the commentary and publish CommentaryGeneratedEvent
        """
        try:
            context = GenerationContext(
                emotion_state=request.emotion_state,
                activity=request.activity,
                recent_events=tuple(request.recent_events[-self._max_recent_events :]),
                recent_messages=self._conversation.recent_messages(self._history_limit),
            )

            system_prompt = self._prompt_builder.build_system_prompt(self._character)
            prompt = self._prompt_builder.build_commentary_prompt(context)

            logger.debug("Generating commentary")
            text = await self._text_generator.generate(
                prompt=prompt,
                system_instruction=system_prompt,
            )

            if text:
                self._conversation.add_streamer_message(text, MessageType.COMMENTARY)
                await self._event_publisher.publish(CommentaryGeneratedEvent(text=text))

            return GenerateCommentaryResponse.ok(text)

        except Exception as e:
            logger.error(f"Commentary generation failed: {e}")
            return GenerateCommentaryResponse.error_response(str(e))
