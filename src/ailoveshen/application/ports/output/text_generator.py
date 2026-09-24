"""Text generator output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class ITextGenerator(ABC):
    """
    Output port for LLM text generation.

    This interface is defined in the Application layer.
    Infrastructure adapters implement this interface.
    """

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
    ) -> str:
        """
        Generate text from a prompt.

        Args:
            prompt: User prompt
            system_instruction: System instruction (character context)

        Returns:
            Generated text. Empty string if the model returned no usable text
            (e.g., blocked content).

        Raises:
            TextGenerationError: If generation fails
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """
        Release resources held by the generator.

        Should be called during cleanup.
        """
        ...
