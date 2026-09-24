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
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import ChatResponseGeneratedEvent
from ailoveshen.domain.value_objects import CharacterProfile, GenerationContext, MessageType


class GenerateResponseUseCase(IGenerateResponse):
    """
    Use case for replying to a viewer's chat (sub loop / interrupt).

    It coordinates:
    - Recording the viewer's chat in the conversation history
    - Prompt construction via adapter
    - Text generation via adapter
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
    ) -> None:
        """
        Initialize use case with dependencies (Dependency Injection).

        Args:
            text_generator: LLM text generator adapter
            prompt_builder: Prompt builder adapter
            event_publisher: Event publisher for domain events
            conversation: Conversation history shared with the commentary use case
            character: The streamer's character profile
            history_limit: Number of recent conversation messages given to the model
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._event_publisher = event_publisher
        self._conversation = conversation
        self._character = character
        self._history_limit = history_limit

    async def execute(self, request: GenerateResponseRequest) -> GenerateResponseResponse:
        """
        Execute the generate chat response use case.

        Flow:
        1. Record the viewer's chat
        2. Build generation context and prompts
        3. Generate text
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

            context = GenerationContext(
                emotion_state=request.emotion_state,
                recent_messages=history,
            )

            system_prompt = self._prompt_builder.build_system_prompt(self._character)
            prompt = self._prompt_builder.build_chat_response_prompt(
                user_name=request.user_name,
                message=request.message,
                context=context,
            )

            logger.debug(f"Generating response for: {request.message[:50]}...")
            text = await self._text_generator.generate(
                prompt=prompt,
                system_instruction=system_prompt,
            )

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
            )

        except Exception as e:
            logger.error(f"Response generation failed: {e}")
            return GenerateResponseResponse.error_response(
                error=str(e),
                original_message=request.message,
                user_name=request.user_name,
            )
