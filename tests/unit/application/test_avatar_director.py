"""Jev がアバターの表情としぐさを選ぶことのテスト（docs/design/24_avatar.md §8）。"""

import asyncio

from ailoveshen.application.use_cases.avatar_director import (
    QUESTIONS,
    AvatarDirector,
    AvatarReaction,
    _intensity,
)
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import FastAnswer, FastVerdict

RULE = AvatarReaction(emotion="happy", intensity=0.7, gesture=None)


class FakeJudge:
    def __init__(self, answers=None, error=None, delay=0.0):
        self.answers = answers or {}
        self.error = error
        self.delay = delay
        self.asked = []
        self.closed = False

    async def ask(self, state, questions):
        self.asked.append((state, questions))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return FastVerdict(
            answers={n: FastAnswer(n, v, c) for n, (v, c) in self.answers.items()},
            elapsed_ms=120,
        )

    async def close(self):
        self.closed = True


class Recorder:
    def __init__(self):
        self.records = []

    def record(self, entry):
        self.records.append(entry)


async def test_jev_answer_is_used_and_recorded_next_to_the_rule():
    judge = FakeJudge(
        {"emotion": ("surprised", 0.8), "intensity": (3.0, 0.9), "gesture": ("flinch", 0.7)}
    )
    recorder = Recorder()
    director = AvatarDirector(judge, recorder=recorder)
    reaction = await director.react("took damage", "いたっ", RULE)
    assert reaction == AvatarReaction("surprised", 1.0, "flinch", "jev", 0.8)
    state, questions = judge.asked[0]
    assert state == {"moment": "took damage", "line": "いたっ"}
    assert questions == QUESTIONS
    [entry] = recorder.records
    assert entry["rule"] == {"emotion": "happy", "gesture": None}
    assert entry["chosen"]["source"] == "jev"
    assert entry["jev"]["emotion"] == {"value": "surprised", "confidence": 0.8}


async def test_neutral_and_none_mean_no_change():
    judge = FakeJudge(
        {"emotion": ("neutral", 0.9), "intensity": (0.0, 0.9), "gesture": ("none", 0.9)}
    )
    reaction = await AvatarDirector(judge).react("speaking", "えーと", RULE)
    assert (reaction.emotion, reaction.gesture, reaction.source) == (None, None, "jev")


async def test_rule_is_used_on_error_timeout_low_confidence_or_unknown_answer():
    recorder = Recorder()
    cases = [
        FakeJudge(error=AILoveShenError("down")),
        FakeJudge({"emotion": ("happy", 0.9)}, delay=0.5),
        FakeJudge({"emotion": ("sad", 0.2)}),
        FakeJudge({"emotion": ("furious", 0.9)}),
        FakeJudge({"emotion": ("sad", 0.9), "gesture": ("dance", 0.9)}),
    ]
    for judge in cases:
        director = AvatarDirector(judge, timeout_seconds=0.05, recorder=recorder)
        assert await director.react("speaking", "x", RULE) == RULE
    assert [r["chosen"]["source"] for r in recorder.records] == ["rule"] * len(cases)


async def test_state_carries_the_situation_and_close_closes_the_judge():
    judge = FakeJudge()
    director = AvatarDirector(judge)
    assert director.state("speaking", "  やあ ") == {"moment": "speaking", "line": "やあ"}
    await director.close()
    assert judge.closed


def test_intensity_maps_the_score_to_a_weight():
    assert _intensity(0) == 0.3
    assert _intensity(3) == 1.0
    assert 0.3 < _intensity(1.5) < 1.0


def test_questions_convert_to_jev_questions():
    import pytest

    pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")
    from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import to_sdk_question

    assert [type(to_sdk_question(q)).__name__ for q in QUESTIONS] == ["Choice", "Score", "Choice"]
