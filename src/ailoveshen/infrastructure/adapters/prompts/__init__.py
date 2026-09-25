"""プロンプトビルダーのアダプター実装。"""

from __future__ import annotations

from ailoveshen.infrastructure.adapters.prompts.game_prompt_template_builder import (
    GamePromptTemplateBuilder,
)
from ailoveshen.infrastructure.adapters.prompts.prompt_template_builder import (
    PromptTemplateBuilder,
)

__all__ = ["GamePromptTemplateBuilder", "PromptTemplateBuilder"]
