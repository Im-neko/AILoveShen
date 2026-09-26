"""IrodoriTtsClient のテスト（HTTP はモックの transport）。"""

import json

import pytest

httpx = pytest.importorskip("httpx", reason="httpx not installed")

from ailoveshen.domain.exceptions import SynthesisError
from ailoveshen.domain.value_objects import EmotionState, EmotionType
from ailoveshen.factories.tts import create_synthesizer, describe_engine, engine_of
from ailoveshen.infrastructure.adapters.tts.irodori_tts_client import IrodoriTtsClient
from ailoveshen.infrastructure.adapters.tts.style_bert_vits2_client import StyleBertVits2Client

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "


def _connected(client, handler):
    client._client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    return client


def test_request_body_has_the_voice_and_fixed_seed():
    body = IrodoriTtsClient(voice="shen", speed=1.1, seed=7).request_body("こんにちは", EmotionState())
    assert body == {
        "model": "irodori-tts",
        "input": "こんにちは",
        "voice": "shen",
        "response_format": "wav",
        "speed": 1.1,
        "irodori": {"seed": 7},
    }


def test_caption_only_for_strong_emotions_with_a_caption():
    client = IrodoriTtsClient(
        emotion_captions={EmotionType.HAPPY: "明るく"}, caption_min_intensity=0.6
    )
    assert client.caption_for(EmotionState(EmotionType.HAPPY, 0.8)) == "明るく"
    assert client.caption_for(EmotionState(EmotionType.HAPPY, 0.3)) is None
    assert client.caption_for(EmotionState(EmotionType.SAD, 0.9)) is None
    assert "caption" not in IrodoriTtsClient().request_body("x", EmotionState(EmotionType.HAPPY, 1.0)).get(
        "irodori", {}
    )


@pytest.mark.asyncio
async def test_synthesize_posts_and_returns_wav():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=WAV, headers={"content-type": "audio/wav"})

    client = _connected(IrodoriTtsClient(voice="shen"), handler)
    assert await client.synthesize("やった！", EmotionState()) == WAV
    assert seen["path"] == "/v1/audio/speech"
    assert seen["body"]["input"] == "やった！"


@pytest.mark.asyncio
async def test_synthesize_errors_are_synthesis_errors():
    client = _connected(IrodoriTtsClient(), lambda r: httpx.Response(404, text="voice not found"))
    with pytest.raises(SynthesisError, match="404: voice not found"):
        await client.synthesize("x", EmotionState())
    client = _connected(IrodoriTtsClient(), lambda r: httpx.Response(200, json={"error": "x"}))
    with pytest.raises(SynthesisError, match="no WAV"):
        await client.synthesize("x", EmotionState())


@pytest.mark.asyncio
async def test_synthesize_without_connect_raises():
    with pytest.raises(SynthesisError, match="not connected"):
        await IrodoriTtsClient().synthesize("x", EmotionState())


def test_factory_picks_the_engine():
    assert isinstance(create_synthesizer({}), StyleBertVits2Client)
    irodori = create_synthesizer(
        {
            "engine": "irodori",
            "irodori": {
                "voice": "shen",
                "emotion": True,
                "emotion_captions": {"happy": "明るく", "unknown": "x"},
            },
        }
    )
    assert isinstance(irodori, IrodoriTtsClient)
    assert irodori.caption_for(EmotionState(EmotionType.HAPPY, 0.9)) == "明るく"
    off = create_synthesizer({"engine": "irodori", "irodori": {"emotion_captions": {"happy": "明るく"}}})
    assert off.caption_for(EmotionState(EmotionType.HAPPY, 0.9)) is None
    assert "Irodori-TTS localhost:8088" in describe_engine({"engine": "irodori"})
    with pytest.raises(ValueError, match="tts.engine"):
        engine_of({"engine": "voicevox"})


def test_lora_adapter_goes_with_each_request():
    body = IrodoriTtsClient(lora_adapter="/x/lora/checkpoint_final", seed=None).request_body("x", EmotionState())
    assert body["irodori"] == {"lora_adapter": "/x/lora/checkpoint_final"}
    assert create_synthesizer({"engine": "irodori", "irodori": {"lora_adapter": "/y"}}).request_body(
        "x", EmotionState()
    )["irodori"]["lora_adapter"] == "/y"


def test_a_strong_emotion_reads_with_its_own_voice():
    client = IrodoriTtsClient(
        voice="shen_sbv2", emotion_voices={EmotionType.HAPPY: "shen_sbv2_happy"}, caption_min_intensity=0.6
    )
    assert client.request_body("x", EmotionState(EmotionType.HAPPY, 0.8))["voice"] == "shen_sbv2_happy"
    assert client.request_body("x", EmotionState(EmotionType.HAPPY, 0.3))["voice"] == "shen_sbv2"
    assert client.request_body("x", EmotionState(EmotionType.SAD, 0.9))["voice"] == "shen_sbv2"
    made = create_synthesizer(
        {"engine": "irodori", "irodori": {"voice": "v", "emotion_voices": {"sad": "v_sad"}}}
    )
    assert made.voice_for(EmotionState(EmotionType.SAD, 0.9)) == "v_sad"


def test_style_weight_is_sent_only_when_set():
    assert create_synthesizer({"synthesis": {"style_weight": 2.5}})._style_weight == 2.5
    assert create_synthesizer({})._style_weight is None
