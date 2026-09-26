"""視聴者の名前の読みの辞書のテスト（docs/design/30_name_readings.md）。"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from ailoveshen.application.dto.llm_dto import GenerateResponseRequest
from ailoveshen.application.dto.speech_dto import SpeakTextRequest
from ailoveshen.application.ports.output.reading_store import IReadingStore, NameReading
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.application.use_cases.readings import (
    GUESS,
    VIEWER,
    NameReadings,
    tells_reading,
    valid_reading,
)
from ailoveshen.application.use_cases.speak_text import SpeakTextUseCase
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    ChatComment,
    EmotionState,
)
from ailoveshen.infrastructure.adapters.storage import JsonReadingStore
from ailoveshen.presentation.services.chat_responder import ChatResponder


class MemoryStore(IReadingStore):
    def __init__(self, readings=()):
        self.readings = tuple(readings)
        self.saves = 0

    def load(self):
        return self.readings

    def save(self, readings):
        self.readings = readings
        self.saves += 1


def _readings(*entries):
    clock = lambda: datetime(2026, 9, 26, tzinfo=UTC)  # noqa: E731
    return NameReadings(MemoryStore(NameReading(*e, "") for e in entries), clock=clock)


class TestNameReadings:
    def test_valid_readings_are_kana_only(self):
        assert valid_reading(" ねこまる ") == "ねこまる"
        assert valid_reading("ネコ・マル") == "ネコ・マル"
        assert valid_reading("neko") is None
        assert valid_reading("猫") is None
        assert valid_reading("あ" * 31) is None
        assert valid_reading("") is None

    def test_tells_reading(self):
        assert tells_reading("名前の読みはねこまるです")
        assert tells_reading("ねこまるって呼んで")
        assert not tells_reading("ベッド作って")

    def test_learns_and_saves_and_ignores_case(self):
        store = MemoryStore()
        readings = NameReadings(store)
        assert readings.learn("Neko_Maru", "ねこまる", VIEWER).reading == "ねこまる"
        assert readings.get("neko_maru").reading == "ねこまる"
        assert store.saves == 1
        assert readings.learn("neko_maru", "ねこまる", VIEWER) is None  # 変わらない
        assert store.saves == 1

    def test_a_guess_never_overwrites_the_viewers_reading(self):
        readings = _readings(("neko", "ねこ", VIEWER))
        assert readings.learn("neko", "ねこさん", GUESS) is None
        assert readings.get("neko").reading == "ねこ"
        assert readings.learn("neko", "にゃんこ", VIEWER).reading == "にゃんこ"

    def test_refuses_what_is_not_a_reading(self):
        readings = _readings()
        assert readings.learn("neko", "neko", VIEWER) is None
        assert readings.get("neko") is None

    def test_apply_replaces_whole_names_longest_first(self):
        readings = _readings(("neko", "ねこ", VIEWER), ("neko_maru", "ねこまる", GUESS))
        assert readings.apply("neko_maruさんとNEKOさん、ありがとう") == (
            "ねこまるさんとねこさん、ありがとう"
        )
        # 英数字の途中は置き換えない
        assert readings.apply("nekomimi") == "nekomimi"

    def test_apply_without_readings_keeps_the_text(self):
        assert _readings().apply("neko") == "neko"


class TestJsonReadingStore:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "data" / "readings.json"
        readings = NameReadings(JsonReadingStore(path))
        readings.learn("neko", "ねこ", VIEWER)
        again = NameReadings(JsonReadingStore(path))
        assert again.get("NEKO").reading == "ねこ"
        assert again.get("neko").source == VIEWER

    def test_missing_file_is_empty(self, tmp_path):
        assert JsonReadingStore(tmp_path / "none.json").load() == ()


@pytest.mark.asyncio
async def test_speech_uses_the_reading_but_events_keep_the_name():
    synthesizer = AsyncMock()
    synthesizer.synthesize.return_value = b"audio"
    player = AsyncMock()
    player.play.return_value = True
    player.is_playing = Mock(return_value=False)
    player.get_duration_ms = Mock(return_value=100)
    events = AsyncMock()
    readings = _readings(("neko", "ねこ", VIEWER))
    use_case = SpeakTextUseCase(
        synthesizer, player, events, get_current_emotion=EmotionState, pronounce=readings.apply
    )

    await use_case.execute(SpeakTextRequest(text="nekoさん、ありがとう"))

    assert synthesizer.synthesize.call_args.kwargs["text"] == "ねこさん、ありがとう"
    assert events.publish.call_args_list[0].args[0].text == "nekoさん、ありがとう"


def _reply_use_case(generator, builder, readings):
    keeper = Mock()
    return GenerateResponseUseCase(
        text_generator=generator,
        prompt_builder=builder,
        event_publisher=AsyncMock(),
        conversation=Conversation(),
        character=CharacterProfile(),
        mid_goals=keeper,
        readings=readings,
    )


def _session():
    session = Mock()
    session.activity.return_value = None
    return session


class TestReplyLearnsReadings:
    @pytest.fixture
    def builder(self):
        b = Mock()
        b.build_system_prompt.return_value = "system"
        b.build_chat_response_prompt.return_value = "prompt"
        return b

    @pytest.mark.asyncio
    async def test_guesses_a_reading_for_a_new_name(self, builder):
        generator = AsyncMock()
        generator.generate_json.return_value = {
            "reply": "nekoさんこんにちは",
            "request": "none",
            "name_reading": "ねこ",
        }
        readings = _readings()
        use_case = _reply_use_case(generator, builder, readings)

        await use_case.execute(GenerateResponseRequest("neko", "こんにちは", session=_session()))

        assert (readings.get("neko").reading, readings.get("neko").source) == ("ねこ", GUESS)
        assert builder.build_chat_response_prompt.call_args.kwargs["name_reading"] == ""

    @pytest.mark.asyncio
    async def test_known_reading_is_shown_and_not_replaced_by_a_guess(self, builder):
        generator = AsyncMock()
        generator.generate_json.return_value = {
            "reply": "やあ",
            "request": "none",
            "name_reading": "ねこちゃん",
        }
        readings = _readings(("neko", "ねこ", GUESS))
        use_case = _reply_use_case(generator, builder, readings)

        await use_case.execute(GenerateResponseRequest("neko", "やあ", session=_session()))

        assert readings.get("neko").reading == "ねこ"
        assert builder.build_chat_response_prompt.call_args.kwargs["name_reading"] == "ねこ"

    @pytest.mark.asyncio
    async def test_the_viewer_telling_the_reading_wins(self, builder):
        generator = AsyncMock()
        generator.generate_json.return_value = {
            "reply": "わかった、にゃんこさん！",
            "request": "none",
            "name_reading": "にゃんこ",
        }
        readings = _readings(("neko", "ねこ", VIEWER))
        use_case = _reply_use_case(generator, builder, readings)

        await use_case.execute(
            GenerateResponseRequest("neko", "読みはにゃんこです", session=_session())
        )

        assert readings.get("neko").reading == "にゃんこ"
        assert readings.get("neko").source == VIEWER


@pytest.mark.asyncio
async def test_yomi_command_learns_and_acknowledges():
    said = []

    async def say(text):
        said.append(text)

    readings = _readings()
    responder = ChatResponder(AsyncMock(), session=lambda: None, say=say, readings=readings)
    responder.accept(ChatComment("neko", "!yomi ねこまる"))
    responder.accept(ChatComment("tama", "!読み tama"))  # かなでない: 覚えない
    await asyncio.sleep(0)

    assert readings.get("neko").reading == "ねこまる"
    assert readings.get("neko").source == VIEWER
    assert readings.get("tama") is None
    assert said == ["nekoさん、読み方覚えたよ！"]
    assert not await responder.answer_next()  # コマンドには返事の順番を使わない
