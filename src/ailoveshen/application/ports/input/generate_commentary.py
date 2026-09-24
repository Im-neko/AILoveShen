"""Generate commentary input port (use case interface)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ailoveshen.application.dto.llm_dto import (
    GenerateCommentaryRequest,
    GenerateCommentaryResponse,
)


class IGenerateCommentary(ABC):
    """Input port for the game commentary use case (main loop)."""

    @abstractmethod
    async def execute(self, request: GenerateCommentaryRequest) -> GenerateCommentaryResponse:
        """
        Execute commentary generation.

        Args:
            request: Commentary request parameters

        Returns:
            Response containing the generated text, or an error.
        """
        ...
