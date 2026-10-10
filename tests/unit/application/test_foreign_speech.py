"""ForeignSpeech（外国語をカタカナ読みにして日本語の声に渡す、設計書 152）のテスト。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from ailoveshen.application.use_cases.foreign_speech import (
    ForeignSpeech,
    is_foreign_comment,
    mostly_japanese,
    needs_katakana,
)
from ailoveshen.domain.exceptions import TextGenerationError


@pytest.mark.parametrize(
    "text",
    [
        "Hello, thank you for watching!",
        "Bonjour, merci d'être venu.",
        "¡Hola! Gracias por venir.",
        "안녕하세요, 시청해 주셔서 감사합니다.",
        "Привет, спасибо, что пришли!",
        "你好，谢谢你来看我直播。",
        "今日はいい天気だね、good morning!",
        "Xin chào các bạn",
    ],
)
def test_text_a_japanese_voice_cannot_read_is_found(text):
    assert needs_katakana(text)


@pytest.mark.parametrize(
    "text",
    [
        "こんにちは！ 今日は丸石を掘るよ。",
        "ハロー、サンキュー、フォー、ウォッチング！",
        "石のツルハシを作るね。1 個ずつ集めよう！",
        "了解、頑張るね。",  # 漢字だけでも、仮名が混ざる句は日本語
        "",
    ],
)
def test_japanese_text_is_left_alone(text):
    assert not needs_katakana(text)


def _generator(*answers):
    generator = AsyncMock()
    generator.generate.side_effect = list(answers)
    return generator


@pytest.mark.asyncio
async def test_a_foreign_line_is_sent_to_the_model_and_comes_back_in_katakana():
    generator = _generator("ハロー、サンキュー フォー ウォッチング！")
    speech = ForeignSpeech(generator)

    spoken = await speech.pronounce("Hello, thank you for watching!")

    assert spoken == "ハロー、サンキュー フォー ウォッチング！"
    kwargs = generator.generate.call_args.kwargs
    assert kwargs["purpose"] == "reading"
    assert "katakana" in kwargs["system_instruction"]


@pytest.mark.asyncio
async def test_a_japanese_line_costs_no_model_call():
    generator = _generator()
    assert await ForeignSpeech(generator).pronounce("今日は丸石を掘るよ！") == "今日は丸石を掘るよ！"
    generator.generate.assert_not_called()


@pytest.mark.asyncio
async def test_the_same_line_is_converted_once():
    generator = _generator("ハロー！")
    speech = ForeignSpeech(generator)

    assert await speech.pronounce("Hello!") == "ハロー！"
    assert await speech.pronounce("Hello!") == "ハロー！"
    assert generator.generate.await_count == 1


@pytest.mark.asyncio
async def test_a_failing_or_slow_model_means_the_line_is_read_as_it_is():
    failing = AsyncMock()
    failing.generate.side_effect = TextGenerationError("down")
    assert await ForeignSpeech(failing).pronounce("Hello!") == "Hello!"

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    late = AsyncMock()
    late.generate.side_effect = slow
    assert await ForeignSpeech(late, timeout_seconds=0.01).pronounce("Hello!") == "Hello!"


@pytest.mark.asyncio
async def test_an_answer_that_is_still_foreign_is_not_remembered():
    generator = _generator("Hello!", "ハロー！")
    speech = ForeignSpeech(generator)

    assert await speech.pronounce("Hello!") == "Hello!"
    assert await speech.pronounce("Hello!") == "ハロー！"


@pytest.mark.parametrize(
    "text",
    ["How are you doing?", "hello", "你好，在做什么？", "안녕하세요", "Привет!", "Bonjour tout le monde"],
)
def test_a_comment_in_another_language_is_found(text):
    assert is_foreign_comment(text)


@pytest.mark.parametrize(
    "text", ["gg", "lol", "ok", "www", "元気？", "これ good?", "がんばれ！", "Minecraft楽しい", ""]
)
def test_a_japanese_or_short_comment_is_not_foreign(text):
    assert not is_foreign_comment(text)


def test_a_reply_is_japanese_when_kana_make_up_the_letters():
    assert mostly_japanese("ねこかもさん、ありがとう！")
    assert mostly_japanese("OK、ねこかもさん、今から掘るよ！")
    assert not mostly_japanese("I'm great, ねこかも-san! Digging stone now!")
    assert not mostly_japanese("我很好，谢谢！")
    assert not mostly_japanese("!!!")
