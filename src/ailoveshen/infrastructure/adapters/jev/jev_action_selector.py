"""Jev で行動を選ぶアダプター（typesafe-sdk）。"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from loguru import logger
from typesafe_sdk import AsyncTypeSafeClient, Choice, TypeSafeError

from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import ActionDecision, Candidate

QUESTION = "action"


class JevActionSelector(IActionSelector):
    """
    TypeSafe AI の System One モデル（Jev）のインフラ側アダプター。

    IActionSelector を、Choice の質問 1つで実装する。質問の criteria は
    候補の ID とその説明（JSON オブジェクト）。
    """

    def __init__(
        self, api_key: str, model: str = "jev-latest", timeout_seconds: float = 10.0
    ) -> None:
        """
        Jev のクライアントを初期化する。

        Args:
            api_key: TypeSafe の API キー
            model: モデル名またはエイリアス
            timeout_seconds: リクエストごとのタイムアウト

        Raises:
            ValueError: api_key が空のとき。
        """
        if not api_key:
            raise ValueError("TypeSafe API key is required (set TYPESAFE_API_KEY)")
        self._model = model
        self._client = AsyncTypeSafeClient(api_key=api_key, timeout=timeout_seconds)

    async def select(
        self,
        state: dict[str, Any],
        actions: Sequence[Candidate],
        instructions: str,
    ) -> ActionDecision:
        """
        Jev に行動を 1つ選ばせる。

        Raises:
            ActionSelectionError: リクエストが失敗したとき
        """
        criteria = {a.action_id: a.description for a in actions}
        started = time.monotonic()
        try:
            response = await self._client.system_one(
                state=state,
                questions={QUESTION: Choice(instructions=instructions, criteria=criteria)},
                model=self._model,
            )
        except TypeSafeError as e:
            raise ActionSelectionError(f"Jev request failed: {e}") from e

        answer = response.choices[QUESTION]
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            f"Jev {response.model}: {elapsed_ms}ms, input={response.usage.input_tokens} "
            f"-> {answer.choice}（確信度 {answer.confidence:.2f}）"
        )
        return ActionDecision(
            action_id=answer.choice,
            confidence=answer.confidence,
            probabilities=dict(answer.probabilities),
        )

    async def close(self) -> None:
        """内部の HTTP クライアントを閉じる。"""
        await self._client.aclose()
