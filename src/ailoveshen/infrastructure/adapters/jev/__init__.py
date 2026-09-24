"""Jev (TypeSafe AI System One) adapter implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ailoveshen.infrastructure.adapters.jev.jev_action_selector import JevActionSelector


def __getattr__(name: str):
    """Lazy load modules with heavy dependencies."""
    if name == "JevActionSelector":
        from ailoveshen.infrastructure.adapters.jev.jev_action_selector import JevActionSelector

        return JevActionSelector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["JevActionSelector"]
