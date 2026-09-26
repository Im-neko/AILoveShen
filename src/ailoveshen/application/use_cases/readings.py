"""
視聴者の名前の読みの辞書（docs/design/30_name_readings.md）。

- 覚える: 本人のコマンド（`!yomi よみ`）、返答のときの Gemini（読みがなければ推測、本人が読み方を
  言ったらその読み）。本人が決めた読みは、推測で上書きしない
- 使う: 読み上げに渡すテキストだけ、名前を読みに置き換える（表示やログ、イベントは元のまま）
- 引く: 目標ボードの GET /api/readings
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime

from loguru import logger

from ailoveshen.application.ports.output.reading_store import IReadingStore, NameReading

VIEWER = "viewer"
GUESS = "guess"
MAX_LENGTH = 30
# 読みに使える文字: ひらがな、カタカナ、長音、中黒、空白
_KANA = re.compile(r"^[ぁ-ゟ゠-ヿー・　 ]+$")
# 本人が読み方を言っているらしいコメント（このときの Gemini の読みは本人の読みとして扱う）
_TELLS_READING = re.compile(r"読み|よみ|ヨミ|呼んで|よんで")
# !yomi / !読み / !よみ のコマンド
COMMAND = re.compile(r"^\s*!(?:yomi|読み|よみ)\s+(.+?)\s*$", re.IGNORECASE)


def valid_reading(reading: str) -> str | None:
    """読みとして使えるなら整えたもの（前後の空白を取る）、使えなければ None。"""
    text = reading.strip()
    if not text or len(text) > MAX_LENGTH or not _KANA.match(text):
        return None
    return text


def tells_reading(message: str) -> bool:
    """コメントが自分の名前の読み方を言っているらしいか。"""
    return bool(_TELLS_READING.search(message))


def _key(name: str) -> str:
    return name.strip().casefold()


class NameReadings:
    """名前 → 読みの辞書。変わるたびに保存する。"""

    def __init__(
        self,
        store: IReadingStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """
        Args:
            store: 保存先
            clock: 時計（テストで差し替える）
        """
        self._store = store
        self._clock = clock
        self._readings: dict[str, NameReading] = {_key(r.name): r for r in store.load()}
        self._pattern: re.Pattern[str] | None = None
        self._compile()

    def get(self, name: str) -> NameReading | None:
        """その名前の読み（なければ None）。大文字小文字は区別しない。"""
        return self._readings.get(_key(name))

    def all(self) -> tuple[NameReading, ...]:
        """全部（名前の順）。"""
        return tuple(sorted(self._readings.values(), key=lambda r: _key(r.name)))

    def learn(self, name: str, reading: str, source: str) -> NameReading | None:
        """
        読みを覚える。覚えたらその読み、使えない・変わらないなら None。

        推測（guess）は、本人が決めた読み（viewer）を上書きしない。
        """
        text = valid_reading(reading)
        if not name.strip() or text is None:
            logger.info(f"{name} の読みとして使えない: {reading!r}")
            return None
        old = self.get(name)
        if old is not None and (old.reading == text or (source == GUESS and old.source == VIEWER)):
            return None
        new = NameReading(
            name=name.strip(), reading=text, source=source, updated_at=self._clock().isoformat()
        )
        self._readings[_key(name)] = new
        self._compile()
        self._store.save(self.all())
        logger.info(f"名前の読みを覚えた: {new.name} → {new.reading}（{source}）")
        return new

    def apply(self, text: str) -> str:
        """読み上げるテキストの中の名前を読みに置き換える（長い名前から、英数字の途中は除く）。"""
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda m: self._readings[_key(m.group(0))].reading, text)

    def _compile(self) -> None:
        names = sorted((r.name for r in self._readings.values()), key=len, reverse=True)
        self._pattern = (
            re.compile(
                "|".join(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])" for n in names),
                re.IGNORECASE,
            )
            if names
            else None
        )
