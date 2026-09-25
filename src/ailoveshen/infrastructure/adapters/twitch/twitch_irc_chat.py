"""
Twitch のチャットを読むだけのアダプター（IRC、匿名）。

Twitch の IRC は `justinfan<数字>` の名前なら認証なしで読める（書き込みはできない）。
配信者の返事は声（TTS）で返すので、読むだけでよい。トークンも API キーも要らない。
"""

from __future__ import annotations

import asyncio
import random
import ssl
from collections.abc import AsyncIterator, Callable
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.chat_source import IChatSource
from ailoveshen.domain.value_objects import ChatComment

HOST = "irc.chat.twitch.tv"
TLS_PORT = 6697
# Twitch はおよそ 5 分ごとに PING を送る。これより長く何も来なければ切れたとみなす
READ_TIMEOUT_SECONDS = 360.0
CONNECT_TIMEOUT_SECONDS = 15.0

Opener = Callable[[], Any]  # () -> awaitable (reader, writer)


def parse_privmsg(line: str) -> Optional[ChatComment]:
    """
    IRC の 1 行がチャットのコメント（PRIVMSG）なら ChatComment にする。

    形: `@tag=value;... :nick!nick@nick.tmi.twitch.tv PRIVMSG #channel :本文`
    （タグは `CAP REQ :twitch.tv/tags` のときだけ付く）
    """
    tags: dict[str, str] = {}
    rest = line
    if rest.startswith("@"):
        raw_tags, _, rest = rest[1:].partition(" ")
        for item in raw_tags.split(";"):
            key, _, value = item.partition("=")
            tags[key] = value.replace("\\s", " ")
    if not rest.startswith(":"):
        return None
    prefix, _, rest = rest[1:].partition(" ")
    command, _, rest = rest.partition(" ")
    if command != "PRIVMSG":
        return None
    _channel, _, message = rest.partition(" :")
    nick = prefix.split("!", 1)[0]
    name = tags.get("display-name") or nick
    try:
        return ChatComment(user_name=name, message=message, user_id=tags.get("user-id") or None)
    except ValueError:
        return None


class TwitchIrcChat(IChatSource):
    """
    Twitch のチャンネルのチャットを匿名で読む。

    - つながらない・切れたら、1 秒から倍々に最長 60 秒待ってつなぎ直す（成功したら戻す）
    - PING には PONG を返す。RECONNECT（Twitch の保守）ならつなぎ直す
    - 例外は出さない（ログに残して続ける）
    """

    def __init__(
        self,
        channel: str,
        opener: Opener | None = None,
        retry_seconds: float = 1.0,
        retry_max_seconds: float = 60.0,
        read_timeout_seconds: float = READ_TIMEOUT_SECONDS,
        connect_timeout_seconds: float = CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        """
        Args:
            channel: チャンネル名（`#` は付けても付けなくてもよい）
            opener: 接続を開くもの（テストで差し替える）。既定は TLS で Twitch へ
            retry_seconds: つなぎ直す前の最初の待ち
            retry_max_seconds: つなぎ直す前の最長の待ち
            read_timeout_seconds: これより長く何も届かなければつなぎ直す
            connect_timeout_seconds: 接続とログインの時間の上限（応答しない接続で止まらない）

        Raises:
            ValueError: channel が空のとき
        """
        channel = channel.strip().lstrip("#").lower()
        if not channel:
            raise ValueError("a Twitch channel name is required (twitch.channel)")
        self._channel = channel
        self._opener = opener or self._open_tls
        self._retry = retry_seconds
        self._retry_max = retry_max_seconds
        self._read_timeout = read_timeout_seconds
        self._connect_timeout = connect_timeout_seconds
        self._writer: Any = None
        self._closed = False

    @property
    def channel(self) -> str:
        return self._channel

    async def comments(self) -> AsyncIterator[ChatComment]:
        failures = 0
        while not self._closed:
            try:
                reader, writer = await asyncio.wait_for(
                    self._opener(), timeout=self._connect_timeout
                )
                self._writer = writer
                await asyncio.wait_for(self._login(writer), timeout=self._connect_timeout)
                async for comment, joined in self._read(reader, writer):
                    if joined:
                        logger.info(f"Twitch のチャット #{self._channel} を読み始めた")
                        failures = 0
                    if comment is not None:
                        yield comment
                if self._closed:
                    return
                logger.warning(f"Twitch のチャットの接続が切れた（#{self._channel}）")
            except Exception as e:  # noqa: BLE001 - どの失敗でもつなぎ直す（ポートの約束）
                if self._closed:
                    return
                logger.warning(f"Twitch のチャットにつながらない（{type(e).__name__}: {e}）")
            finally:
                await self._drop()
            failures += 1
            wait = min(self._retry_max, self._retry * 2 ** (failures - 1))
            logger.info(f"{wait:.0f} 秒後に Twitch のチャットにつなぎ直す")
            await asyncio.sleep(wait)

    async def close(self) -> None:
        self._closed = True
        await self._drop()

    async def _login(self, writer: Any) -> None:
        nick = f"justinfan{random.randint(10000, 99999)}"
        for line in ("CAP REQ :twitch.tv/tags", f"NICK {nick}", f"JOIN #{self._channel}"):
            writer.write(f"{line}\r\n".encode())
        await writer.drain()

    async def _read(self, reader: Any, writer: Any):
        """(コメント, 参加できたか) を届いた順に。接続が終わったら終わる。"""
        while not self._closed:
            raw = await asyncio.wait_for(reader.readline(), timeout=self._read_timeout)
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("PING"):
                writer.write(f"PONG{line[4:]}\r\n".encode())
                await writer.drain()
                continue
            command = _command(line)
            if command == "RECONNECT":
                logger.info("Twitch がつなぎ直しを求めた")
                return
            if command == "JOIN":
                yield None, True
                continue
            if command == "NOTICE":
                logger.warning(f"Twitch から: {line}")
                continue
            comment = parse_privmsg(line)
            if comment is not None:
                yield comment, False

    async def _drop(self) -> None:
        writer, self._writer = self._writer, None
        if writer is None:
            return
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 - 閉じるときの失敗は気にしない
            pass

    @staticmethod
    async def _open_tls():
        return await asyncio.open_connection(HOST, TLS_PORT, ssl=ssl.create_default_context())


def _command(line: str) -> str:
    rest = line
    if rest.startswith("@"):
        rest = rest.partition(" ")[2]
    if rest.startswith(":"):
        rest = rest.partition(" ")[2]
    return rest.partition(" ")[0]
