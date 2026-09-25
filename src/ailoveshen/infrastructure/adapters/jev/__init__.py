"""Jev（TypeSafe AI の System One）のアダプター実装。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ailoveshen.infrastructure.adapters.jev.jev_action_selector import JevActionSelector
    from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import JevFastJudge


def __getattr__(name: str):
    """重い依存を持つモジュールを遅延読み込みする。"""
    if name == "JevActionSelector":
        from ailoveshen.infrastructure.adapters.jev.jev_action_selector import JevActionSelector

        return JevActionSelector
    if name == "JevFastJudge":
        from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import JevFastJudge

        return JevFastJudge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["JevActionSelector", "JevFastJudge"]
