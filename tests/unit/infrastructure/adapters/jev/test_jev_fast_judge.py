"""JevFastJudge の変換のテスト（typesafe-sdk の型との間）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")

from typesafe_sdk import Choice, Noul, Score  # noqa: E402

from ailoveshen.domain.value_objects import FastQuestion, FastQuestionKind  # noqa: E402
from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import (  # noqa: E402
    JevFastJudge,
    to_answer,
    to_sdk_question,
)

YES_NO = FastQuestion(
    "progressing", FastQuestionKind.YES_NO, "Is it moving?", {"yes": "moves", "no": "stuck"}
)


def test_questions_become_sdk_questions():
    noul = to_sdk_question(YES_NO)
    assert isinstance(noul, Noul)
    assert noul.criteria == {"true": "moves", "false": "stuck"}
    assert isinstance(to_sdk_question(FastQuestion("w", FastQuestionKind.YES_NO, "q")), Noul)
    choice = to_sdk_question(FastQuestion("c", FastQuestionKind.CHOICE, "which", {"a": None}))
    assert isinstance(choice, Choice)
    assert isinstance(
        to_sdk_question(FastQuestion("s", FastQuestionKind.SCORE, "how", ["0", "1"])), Score
    )


def test_a_yes_no_answer_is_the_likelier_side_with_its_probability():
    assert to_answer("q", FastQuestionKind.YES_NO, SimpleNamespace(noul=0.8)).value is True
    no = to_answer("q", FastQuestionKind.YES_NO, SimpleNamespace(noul=0.1))
    assert (no.value, round(no.confidence, 2)) == (False, 0.9)


@pytest.mark.asyncio
async def test_all_questions_go_in_one_call_and_the_tokens_are_kept():
    response = SimpleNamespace(
        model="jev-1",
        usage=SimpleNamespace(input_tokens=321),
        answers={"progressing": SimpleNamespace(noul=0.3), "unasked": SimpleNamespace(noul=1.0)},
    )
    with patch(
        "ailoveshen.infrastructure.adapters.jev.jev_fast_judge.AsyncTypeSafeClient"
    ) as client_cls:
        client_cls.return_value.system_one = AsyncMock(return_value=response)
        verdict = await JevFastJudge(api_key="k").ask({"action": {}}, [YES_NO])
    kwargs = client_cls.return_value.system_one.call_args.kwargs
    assert list(kwargs["questions"]) == ["progressing"]
    assert set(verdict.answers) == {"progressing"}
    assert verdict.answers["progressing"].value is False
    assert verdict.input_tokens == 321
