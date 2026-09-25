"""ObsScreenCapture のテスト（偽の OBS クライアント）。"""

import base64
import logging
from types import SimpleNamespace

import pytest
from loguru import logger

from ailoveshen.infrastructure.adapters.obs.obs_screen_capture import ObsScreenCapture

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
PASSWORD = "s3cret-obs-password"


class FakeClient:
    def __init__(self, image=None, error=None):
        self.image = image or "data:image/jpg;base64," + base64.b64encode(JPEG).decode()
        self.error = error
        self.calls = []
        self.disconnected = False

    def get_source_screenshot(self, name, img_format, width, height, quality):
        self.calls.append((name, img_format, width, height, quality))
        if self.error:
            raise self.error
        return SimpleNamespace(image_data=self.image)

    def disconnect(self):
        self.disconnected = True


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _capture(clients, clock=None, **kwargs):
    made = []

    def factory(host, port, password, timeout):
        made.append((host, port, password, timeout))
        return clients.pop(0)

    capture = ObsScreenCapture(
        host="127.0.0.1",
        port=4455,
        password=PASSWORD,
        source="Minecraft",
        client_factory=factory,
        clock=clock or Clock(),
        **kwargs,
    )
    return capture, made


@pytest.mark.asyncio
async def test_the_game_source_is_taken_as_jpeg_scaled_to_the_width():
    client = FakeClient()
    capture, made = _capture([client], width=640, quality=60, timeout_seconds=2.5)

    shot = await capture.capture()

    assert shot.data == JPEG
    assert shot.mime_type == "image/jpeg"  # OBS の image/jpg を直す
    assert client.calls == [("Minecraft", "jpg", 640, 640, 60)]
    assert made == [("127.0.0.1", 4455, PASSWORD, 2.5)]  # 時間の上限と、明示の接続先
    await capture.capture()
    assert len(made) == 1  # 接続は使い回す


@pytest.mark.asyncio
async def test_a_failure_returns_none_and_waits_before_trying_again():
    clock = Clock()
    broken = FakeClient(error=ConnectionRefusedError("OBS is closed"))
    capture, made = _capture([broken, FakeClient()], clock=clock, retry_seconds=60)

    assert await capture.capture() is None
    assert broken.disconnected
    clock.now += 30
    assert await capture.capture() is None  # 待っている間は接続もしない
    assert len(made) == 1
    clock.now += 31
    assert (await capture.capture()).data == JPEG
    assert len(made) == 2


@pytest.mark.asyncio
async def test_a_missing_source_turns_capturing_off():
    clock = Clock()
    missing = FakeClient(error=type("OBSSDKRequestError", (Exception,), {"code": 600})("x"))
    capture, made = _capture([missing], clock=clock)

    assert await capture.capture() is None
    clock.now += 10_000
    assert await capture.capture() is None
    assert len(made) == 1


@pytest.mark.asyncio
async def test_the_password_never_reaches_the_logs(caplog):
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        with caplog.at_level(logging.DEBUG):
            capture, _ = _capture([FakeClient(error=RuntimeError(f"auth failed for {PASSWORD!r}"))])
            await capture.capture()
    finally:
        logger.remove(sink)
    assert lines  # 失敗はログに出す
    assert not any(PASSWORD in line for line in lines)
    assert PASSWORD not in caplog.text


def test_a_source_name_is_required():
    with pytest.raises(ValueError, match="obs.game_source"):
        ObsScreenCapture(host="h", port=1, password="", source="")


@pytest.mark.asyncio
async def test_not_a_data_uri_is_a_failure_not_an_exception():
    capture, _ = _capture([FakeClient(image="not a data uri")])
    assert await capture.capture() is None


def test_the_real_library_is_quiet_and_never_shows_the_password():
    """本物の obsws-python で閉じたポートにつなぐ: パスワードもトレースバックも出さない。"""
    import subprocess
    import sys

    pytest.importorskip("obsws_python")
    code = (
        "from ailoveshen.infrastructure.adapters.obs.obs_screen_capture import "
        "_default_client_factory\n"
        "try:\n"
        "    _default_client_factory('127.0.0.1', 1, 'pw-test-secret', 1.0)\n"
        "except Exception as e:\n"
        "    print(type(e).__name__)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert "pw-test-secret" not in r.stdout + r.stderr
    assert "Traceback" not in r.stderr


@pytest.mark.asyncio
async def test_a_missing_library_says_how_to_install_it_and_stops_trying():
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    made = []

    def factory(host, port, password, timeout):
        made.append(1)
        raise ModuleNotFoundError("No module named 'obsws_python'", name="obsws_python")

    capture = ObsScreenCapture(
        host="127.0.0.1", port=4455, password=PASSWORD, source="Minecraft", client_factory=factory
    )
    try:
        assert await capture.capture() is None
        assert await capture.capture() is None
    finally:
        logger.remove(sink)
    assert made == [1]  # 入れ直すまで撮らない
    assert any("obsws_python" in m and "pip install" in m for m in messages)
