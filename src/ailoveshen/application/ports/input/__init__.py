"""Input ports (use case interfaces)."""

from __future__ import annotations

from ailoveshen.application.ports.input.build_house import IAdvanceHouseProject, IStartHouseProject
from ailoveshen.application.ports.input.generate_commentary import IGenerateCommentary
from ailoveshen.application.ports.input.generate_response import IGenerateResponse
from ailoveshen.application.ports.input.speak_text import ISpeakText

__all__ = [
    "IAdvanceHouseProject",
    "IGenerateCommentary",
    "IGenerateResponse",
    "ISpeakText",
    "IStartHouseProject",
]
