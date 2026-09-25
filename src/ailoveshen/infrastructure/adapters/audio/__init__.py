"""音声再生のアダプター実装。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ailoveshen.infrastructure.adapters.audio.sounddevice_player import (
        SounddevicePlayer,
    )


def __getattr__(name: str):
    """重い依存を持つモジュールを遅延読み込みする。"""
    if name == "SounddevicePlayer":
        from ailoveshen.infrastructure.adapters.audio.sounddevice_player import (
            SounddevicePlayer,
        )

        return SounddevicePlayer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["SounddevicePlayer"]
