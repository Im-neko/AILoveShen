"""Jev に、配信者やコードが書いた質問にまとめて答えさせるアダプター（typesafe-sdk）。"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from loguru import logger
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score, TypeSafeError

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.domain.exceptions import ActionSelectionError
from ailoveshen.domain.value_objects import (
    FastAnswer,
    FastQuestion,
    FastQuestionKind,
    FastVerdict,
)


def to_sdk_question(q: FastQuestion) -> Noul | Choice | Score:
    """モデルに依存しない質問を、typesafe-sdk の質問にする。"""
    if q.kind == FastQuestionKind.YES_NO:
        criteria = None
        if q.criteria:
            criteria = {"true": q.criteria.get("yes"), "false": q.criteria.get("no")}
        return Noul(instructions=q.instructions, criteria=criteria)
    if q.kind == FastQuestionKind.CHOICE:
        return Choice(instructions=q.instructions, criteria=dict(q.criteria))
    return Score(instructions=q.instructions, criteria=list(q.criteria))


def to_answer(name: str, kind: FastQuestionKind, answer: Any) -> FastAnswer:
    """typesafe-sdk の答えを、モデルに依存しない答えにする。"""
    if kind == FastQuestionKind.YES_NO:
        p_yes = float(answer.noul)  # 「はい」の確率
        yes = p_yes >= 0.5
        return FastAnswer(name=name, value=yes, confidence=p_yes if yes else 1.0 - p_yes)
    if kind == FastQuestionKind.CHOICE:
        return FastAnswer(name=name, value=answer.choice, confidence=float(answer.confidence))
    return FastAnswer(name=name, value=float(answer.score), confidence=float(answer.confidence))


class JevFastJudge(IFastJudge):
    """
    TypeSafe AI の System One（Jev）のインフラ側アダプター。

    IFastJudge を、system_one の 1 回の呼び出し（名前つきの質問を全部まとめる）で実装する。
    ティックごとの入力トークンと所要時間を返す（見張りの費用の比較、設計書 19 §11.5）。
    """

    def __init__(
        self, api_key: str, model: str = "jev-latest", timeout_seconds: float = 10.0
    ) -> None:
        """
        Raises:
            ValueError: api_key が空のとき。
        """
        if not api_key:
            raise ValueError("TypeSafe API key is required (set TYPESAFE_API_KEY)")
        self._model = model
        self._client = AsyncTypeSafeClient(api_key=api_key, timeout=timeout_seconds)

    async def ask(self, state: dict[str, Any], questions: Sequence[FastQuestion]) -> FastVerdict:
        """すべての質問を 1 回で聞く。"""
        started = time.monotonic()
        try:
            response = await self._client.system_one(
                state=state,
                questions={q.name: to_sdk_question(q) for q in questions},
                model=self._model,
            )
        except TypeSafeError as e:
            raise ActionSelectionError(f"Jev request failed: {e}") from e
        elapsed_ms = int((time.monotonic() - started) * 1000)
        kinds = {q.name: q.kind for q in questions}
        answers = {
            name: to_answer(name, kinds[name], answer)
            for name, answer in response.answers.items()
            if name in kinds
        }
        tokens = response.usage.input_tokens
        logger.debug(
            f"Jev {response.model}: {elapsed_ms}ms, input={tokens}, "
            + ", ".join(f"{n}={a.value}({a.confidence:.2f})" for n, a in answers.items())
        )
        return FastVerdict(answers=answers, elapsed_ms=elapsed_ms, input_tokens=tokens)

    async def close(self) -> None:
        """内部の HTTP クライアントを閉じる。"""
        await self._client.aclose()
