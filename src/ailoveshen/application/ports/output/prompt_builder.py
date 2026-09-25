"""Prompt builder output port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.domain.value_objects import CharacterProfile, GenerationContext


class IPromptBuilder(ABC):
    """
    Output port for building LLM prompts.

    This interface is defined in the Application layer.
    Infrastructure adapters implement this interface.
    """

    @abstractmethod
    def build_system_prompt(self, character: CharacterProfile) -> str:
        """Build the system instruction describing the character."""
        ...

    @abstractmethod
    def build_commentary_prompt(self, context: GenerationContext) -> str:
        """Build the prompt for game commentary."""
        ...

    @abstractmethod
    def build_chat_response_prompt(
        self,
        user_name: str,
        message: str,
        context: GenerationContext,
        takes_requests: bool = False,
        previous_error: str = "",
    ) -> str:
        """
        Build the prompt for replying to a viewer's chat.

        Args:
            user_name: The viewer
            message: Their chat message
            context: What the streamer is doing and the recent conversation
            takes_requests: Whether the reply may accept the request as a mid goal (JSON output)
            previous_error: Why the previous reply's accepted request could not be used
        """
        ...
