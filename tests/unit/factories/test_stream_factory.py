"""本番の配信のファクトリーと入口のテスト。"""

import pytest

pytest.importorskip("google.genai", reason="google-genai not installed")
pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")

from ailoveshen.factories.stream import StreamSetupError, create_stream  # noqa: E402
from ailoveshen.infrastructure.config import (  # noqa: E402
    GeminiSettings,
    JevSettings,
    Settings,
    StreamSettings,
    TwitchSettings,
)
from ailoveshen.stream import main  # noqa: E402


def _settings(**stream):
    return Settings(
        gemini=GeminiSettings(api_key="g"),
        jev=JevSettings(api_key="j"),
        stream=StreamSettings(**stream),
        twitch=TwitchSettings(channel="Shen"),
    )


@pytest.mark.asyncio
async def test_the_chat_is_read_when_there_is_a_channel(tmp_path):
    stream = await create_stream(_settings(board_port=0, speak=False), {}, tmp_path)
    assert stream._chat.channel == "shen"
    assert stream._board is None
    await stream._close()


@pytest.mark.asyncio
async def test_no_channel_no_chat_and_the_board_is_served(tmp_path):
    settings = _settings(board_port=8765, speak=False)
    settings.twitch = TwitchSettings(channel="")
    stream = await create_stream(settings, {}, tmp_path)
    assert stream._chat is None and stream._board is not None
    await stream._close()


@pytest.mark.asyncio
async def test_an_unreachable_tts_server_stops_the_start_with_how_to_fix(tmp_path):
    pytest.importorskip("sounddevice", reason="sounddevice not installed")
    tts = {"server": {"host": "127.0.0.1", "port": 1, "timeout_seconds": 1}}
    with pytest.raises(StreamSetupError, match="--no-speak"):
        await create_stream(_settings(board_port=0, speak=True), tts, tmp_path)


def test_main_needs_the_config_and_the_keys(tmp_path, monkeypatch, capsys):
    import os

    assert main(["--config-dir", str(tmp_path / "nowhere")]) == 2
    monkeypatch.setattr(os, "environ", {})
    (tmp_path / "default.yaml").write_text("logging:\n  file: ''\n")
    monkeypatch.chdir(tmp_path)
    assert main(["--config-dir", str(tmp_path)]) == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().err
