"""Audio adapter implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ailoveshen.tts.infrastructure.adapters.audio.sounddevice_player import (
        SounddevicePlayer,
    )


def __getattr__(name: str):
    """Lazy load modules with heavy dependencies."""
    if name == "SounddevicePlayer":
        from ailoveshen.tts.infrastructure.adapters.audio.sounddevice_player import (
            SounddevicePlayer,
        )
        return SounddevicePlayer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["SounddevicePlayer"]
