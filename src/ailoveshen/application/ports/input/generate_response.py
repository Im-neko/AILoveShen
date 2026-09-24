"""Generate chat response input port (use case interface)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.llm_dto import (
    GenerateResponseRequest,
    GenerateResponseResponse,
)


class IGenerateResponse(ABC):
    """Input port for the chat response use case (sub loop / interrupt)."""

    @abstractmethod
    async def execute(self, request: GenerateResponseRequest) -> GenerateResponseResponse:
        """
        Execute chat response generation.

        Args:
            request: Chat response request parameters

        Returns:
            Response containing the generated text, or an error.
        """
        ...
