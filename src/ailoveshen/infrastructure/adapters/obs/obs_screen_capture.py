"""OBS のソースを撮るアダプター（obs-websocket 5、obsws-python）。"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Callable
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.screen_capture import IScreenCapture
from ailoveshen.domain.value_objects import Screenshot

# OBS の「リソースがない」（ソース名の間違い）
RESOURCE_NOT_FOUND = 600


def _silence_library_logs() -> None:
    """
    obsws-python のログを出させない: 接続のたびにパスワードを INFO で出し、つながらないたびに
    トレースバックを ERROR で出す（OBS を開いていないと 60 秒ごとに流れる）。失敗はこの
    アダプターが 1 行で出す。
    """
    library = logging.getLogger("obsws_python")
    library.setLevel(logging.CRITICAL + 1)
    library.propagate = False
    if not library.handlers:
        library.addHandler(logging.NullHandler())


def _default_client_factory(host: str, port: int, password: str, timeout: float) -> Any:
    # 重い依存（websocket-client）は使うときに読む
    import obsws_python

    _silence_library_logs()
    # host / port / password を必ず渡す（渡さないと、ライブラリは手元の toml を読みにいく）
    return obsws_python.ReqClient(host=host, port=port, password=password, timeout=timeout)


class ObsScreenCapture(IScreenCapture):
    """
    OBS WebSocket の GetSourceScreenshot で、ゲームのソースを JPEG で撮る
    （docs/design/23_screen_vision.md）。

    - 同期のクライアントをスレッドで使い、1 つのロックで 1 回ずつ呼ぶ
    - 接続は最初に撮るときにする。失敗したら接続を捨て、`retry_seconds` は撮らない
    - ソースがなければ（名前の間違い）それ以降は撮らない
    - どの失敗でも例外は出さずに None を返す（画像なしでプレイを続ける）
    - パスワードはログにもエラーにも出さない
    """

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        source: str,
        width: int = 768,
        quality: int = 70,
        timeout_seconds: float = 3.0,
        retry_seconds: float = 60.0,
        client_factory: Callable[[str, int, str, float], Any] = _default_client_factory,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            host: OBS のホスト
            port: OBS WebSocket のポート（既定 4455）
            password: OBS WebSocket のパスワード
            source: 撮るソースの名前（ゲームを映しているもの。シーン全体は撮らない）
            width: 縮める幅（縦横比は保つ）
            quality: JPEG の品質（0〜100）
            timeout_seconds: 1 回の撮影（と接続）の時間の上限
            retry_seconds: 失敗の後、次に撮るまでの待ち
            client_factory: OBS のクライアントを作るもの（テストで差し替える）
            clock: 時計（テストで差し替える）

        Raises:
            ValueError: source が空のとき
        """
        if not source:
            raise ValueError("an OBS source name is required (obs.game_source)")
        self._host = host
        self._port = port
        self._password = password
        self._source = source
        self._width = width
        self._quality = quality
        self._timeout = timeout_seconds
        self._retry = retry_seconds
        self._factory = client_factory
        self._clock = clock
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._retry_at = 0.0
        self._failing = False
        self._disabled = ""

    async def capture(self) -> Optional[Screenshot]:
        """1 枚撮る。撮れなければ None。"""
        if self._disabled or self._clock() < self._retry_at:
            return None
        async with self._lock:
            try:
                shot = await asyncio.wait_for(
                    asyncio.to_thread(self._capture_sync), timeout=self._timeout + 1.0
                )
            except Exception as e:  # noqa: BLE001 - 撮影の失敗でプレイを止めない
                self._fail(e)
                return None
        if self._failing:
            logger.info(f"OBS から画面を撮れるようになった（{self._source}）")
            self._failing = False
        return shot

    async def close(self) -> None:
        """接続を閉じる。"""
        async with self._lock:
            await asyncio.to_thread(self._drop)

    def _capture_sync(self) -> Screenshot:
        if self._client is None:
            self._client = self._factory(self._host, self._port, self._password, self._timeout)
        # 幅と高さに同じ値を渡す: OBS は小さいほうの比率で縮め、縦横比を保つ
        response = self._client.get_source_screenshot(
            self._source, "jpg", self._width, self._width, self._quality
        )
        return _parse(response.image_data, self._width)

    def _fail(self, error: Exception) -> None:
        self._drop()
        code = getattr(error, "code", None)
        if code == RESOURCE_NOT_FOUND:
            self._disabled = f"OBS has no source named {self._source!r}"
            logger.warning(
                f"OBS にソース「{self._source}」がない。画面を見ずに続ける"
                "（obs.game_source を OBS のソース名に合わせる）"
            )
            return
        self._retry_at = self._clock() + self._retry
        if not self._failing:
            # 型名と短い説明だけ（パスワードを含む repr は出さない）
            logger.warning(
                f"OBS から画面を撮れない（{type(error).__name__}）。"
                f"{self._retry:.0f} 秒は画面なしで続ける"
            )
        self._failing = True

    def _drop(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 - 閉じるときの失敗は気にしない
            pass


def _parse(data_uri: str, width: int) -> Screenshot:
    """`data:image/jpg;base64,...` を Screenshot にする（image/jpg は image/jpeg に直す）。"""
    header, _, payload = data_uri.partition(",")
    if not header.startswith("data:") or ";base64" not in header or not payload:
        raise ValueError("OBS returned an image that is not a base64 data URI")
    mime = header[len("data:") : header.index(";")].lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    return Screenshot(data=base64.b64decode(payload), mime_type=mime, width=width)
