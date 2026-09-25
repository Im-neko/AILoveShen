"""LLM service for presentation layer."""

from __future__ import annotations

from typing import Optional

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateResponseRequest,
)
from ailoveshen.application.ports.input.generate_commentary import IGenerateCommentary
from ailoveshen.application.ports.input.generate_response import IGenerateResponse
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.value_objects import Activity, EmotionState


class LLMService:
    """
    Presentation layer service for LLM operations.

    Provides a simple interface for other components (orchestrator, chat
    handler) to get commentary and chat replies. Holds the current emotion
    passed to generation. Returns an empty string when generation fails.
    """

    def __init__(
        self,
        generate_commentary_use_case: IGenerateCommentary,
        generate_response_use_case: IGenerateResponse,
        text_generator: ITextGenerator,
    ) -> None:
        """
        Initialize LLM service.

        Args:
            generate_commentary_use_case: Use case for game commentary
            generate_response_use_case: Use case for chat responses
            text_generator: Text generator, closed together with the service
        """
        self._generate_commentary = generate_commentary_use_case
        self._generate_response = generate_response_use_case
        self._text_generator = text_generator
        self._current_emotion = EmotionState()

    async def generate_commentary(
        self,
        recent_events: Optional[list[str]] = None,
        activity: Optional[Activity] = None,
    ) -> str:
        """
        Generate game commentary.

        Args:
            recent_events: Recent game event descriptions (newest last: what to talk about)
            activity: What the streamer is doing and why (PlaySession.activity())

        Returns:
            Generated commentary, or empty string on failure
        """
        response = await self._generate_commentary.execute(
            GenerateCommentaryRequest(
                emotion_state=self._current_emotion,
                recent_events=recent_events or [],
                activity=activity,
            )
        )
        return response.text if response.success else ""

    async def generate_response(
        self,
        user_name: str,
        message: str,
        user_id: Optional[str] = None,
        session: Optional[PlaySession] = None,
    ) -> str:
        """
        Generate a reply to a viewer's chat message.

        Args:
            user_name: Viewer's display name
            message: Chat message content
            user_id: Viewer's platform ID, if known
            session: The play session, if playing: the reply sees what the
                streamer is doing and may take the viewer's request as the next goal

        Returns:
            Generated reply, or empty string on failure
        """
        response = await self._generate_response.execute(
            GenerateResponseRequest(
                user_name=user_name,
                message=message,
                user_id=user_id,
                emotion_state=self._current_emotion,
                session=session,
            )
        )
        return response.text if response.success else ""

    def update_emotion(self, emotion_state: EmotionState) -> None:
        """Update the emotion used for subsequent generations."""
        self._current_emotion = emotion_state

    def get_current_emotion(self) -> EmotionState:
        """Get the emotion used for generation."""
        return self._current_emotion

    async def close(self) -> None:
        """Release resources held by the text generator."""
        await self._text_generator.close()
