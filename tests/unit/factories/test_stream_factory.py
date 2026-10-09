"""本番の配信のファクトリーと入口のテスト。"""

import pytest

pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")

from ailoveshen.factories.stream import StreamSetupError, create_stream  # noqa: E402
from ailoveshen.infrastructure.config import (  # noqa: E402
    JevSettings,
    LiteLlmSettings,
    LlmSettings,
    Settings,
    StreamSettings,
    TwitchSettings,
)
from ailoveshen.stream import main  # noqa: E402


def _settings(**stream):
    return Settings(
        llm=LlmSettings(litellm=LiteLlmSettings(model="haiku-5.5")),
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
async def test_support_is_thanked_over_the_chat_connection_and_follows_need_the_client_id(tmp_path):
    # チャンネルだけ: サブスク・ギフト・ビッツ・レイドはトークンなしで届く。フォローは受けない
    stream = await create_stream(_settings(board_port=0, speak=False), {}, tmp_path)
    assert stream._supports == (stream._chat,) and stream._thanker is not None
    await stream._close()
    # Client-Id があれば、フォローの EventSub が加わる（トークンは自動で取って更新する）
    settings = _settings(board_port=0, speak=False)
    settings.twitch = TwitchSettings(channel="Shen", client_id="cid", token_path=str(tmp_path / "token.json"))
    stream = await create_stream(settings, {}, tmp_path)
    assert [type(s).__name__ for s in stream._supports] == ["TwitchIrcChat", "TwitchEventSub"]
    assert type(stream._supports[1]._token).__name__ == "TwitchUserToken"
    await stream._close()
    # 手で作ったトークンを書けば、それをそのまま使う（更新はしない）
    settings.twitch.eventsub_token = "oauth:mine"
    stream = await create_stream(settings, {}, tmp_path)
    assert type(stream._supports[1]._token).__name__ == "StaticToken"
    await stream._close()
    # オフにできる
    settings.twitch.support.enabled = False
    stream = await create_stream(settings, {}, tmp_path)
    assert stream._supports == () and stream._thanker is None
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
    assert "LITELLM_MODEL" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_a_busy_board_port_stops_the_start_with_how_to_fix(tmp_path):
    import socket

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        with pytest.raises(StreamSetupError, match="--board-port"):
            await create_stream(_settings(board_port=port, speak=False), {}, tmp_path)


@pytest.mark.asyncio
async def test_what_the_tts_did_with_a_line_is_recorded_with_the_state(tmp_path, monkeypatch):
    """視聴者が聞いたかの記録（設計書 65 §2）: 読み上げの結果が `logs/spoken` に残る。"""
    import json
    from unittest.mock import AsyncMock

    from ailoveshen.application.dto.speech_dto import SpeakTextRequest
    from ailoveshen.factories import tts as tts_factory
    from ailoveshen.presentation.services.tts_service import TTSService

    service = TTSService(AsyncMock())

    async def fake_tts(**kwargs):
        return service

    monkeypatch.setattr(tts_factory, "create_and_connect_tts_service", fake_tts)
    stream = await create_stream(_settings(board_port=0, speak=True), {}, tmp_path)
    try:
        service.on_outcome(SpeakTextRequest(text="家に入れたよ", source="commentary"), "dropped_old", 25.0)
    finally:
        await stream._close()

    (record,) = tmp_path.glob("logs/spoken/watch-*.jsonl")
    (row,) = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert (row["event"], row["text"], row["outcome"], row["waited"]) == (
        "tts",
        "家に入れたよ",
        "dropped_old",
        25.0,
    )
