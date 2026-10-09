"""
advisor に意見を聞く（docs/design/143）。

道具の選択で配信者が `ask_advisor` を呼ぶと、迷った時に見ていたプロンプトと聞きたいことを、別の
モデル（`LITELLM_ADVISOR_MODEL`。用途 `advice`）に渡して助言を書かせる。advisor は道具を呼ばず、答え
は直近の道具に出るだけで、従うかは配信者が決める。答えない・遅いときは失敗として返し、配信者は
そのまま別の道具を選ぶ。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from loguru import logger

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.exceptions import TextGenerationError

PURPOSE = "advice"


class Advisor:
    """迷った配信者に、より強いモデルの意見を返す。"""

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            text_generator: LLM（purpose=advice で LITELLM_ADVISOR_MODEL に向かう）
            prompt_builder: プロンプトの組み立て
            clock: 秒を返す（ログ用）
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._clock = clock

    async def ask(self, question: str, situation: str) -> tuple[bool, str]:
        """(ok, 助言の文)。聞くことがない・答えが空・生成に失敗したときは ok が偽。"""
        question = question.strip()
        if not question:
            return False, "invalid call: question is empty"
        prompt = self._prompt_builder.build_advice_prompt(question, situation)
        started = self._clock()
        try:
            advice = (await self._text_generator.generate(prompt, purpose=PURPOSE)).strip()
        except TextGenerationError as e:
            logger.warning(f"advisor に聞けなかった: {e}")
            return False, f"the advisor could not answer: {e}"
        if not advice:
            return False, "the advisor gave no answer"
        logger.info(f"advisor に聞いた（{self._clock() - started:.1f} 秒）: {question[:80]} -> {advice[:200]}")
        return True, advice
