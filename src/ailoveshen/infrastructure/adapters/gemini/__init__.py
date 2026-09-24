"""Gemini adapter implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import (
        GeminiTextGenerator,
    )


def __getattr__(name: str):
    """Lazy load modules with heavy dependencies."""
    if name == "GeminiTextGenerator":
        from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import (
            GeminiTextGenerator,
        )
        return GeminiTextGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["GeminiTextGenerator"]
