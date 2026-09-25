"""入力ポート（ユースケースのインターフェース）。"""

from __future__ import annotations

from ailoveshen.application.ports.input.generate_commentary import IGenerateCommentary
from ailoveshen.application.ports.input.generate_response import IGenerateResponse
from ailoveshen.application.ports.input.play import IAdvancePlay, IStartPlay
from ailoveshen.application.ports.input.speak_text import ISpeakText

__all__ = [
    "IAdvancePlay",
    "IGenerateCommentary",
    "IGenerateResponse",
    "ISpeakText",
    "IStartPlay",
]
