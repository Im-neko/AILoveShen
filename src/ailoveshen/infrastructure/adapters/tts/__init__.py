"""TTS adapter implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ailoveshen.infrastructure.adapters.tts.style_bert_vits2_client import (
        StyleBertVits2Client,
    )


def __getattr__(name: str):
    """Lazy load modules with heavy dependencies."""
    if name == "StyleBertVits2Client":
        from ailoveshen.infrastructure.adapters.tts.style_bert_vits2_client import (
            StyleBertVits2Client,
        )
        return StyleBertVits2Client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["StyleBertVits2Client"]
