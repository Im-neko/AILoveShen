"""
建物・施設の呼び名（docs/design/160）。

識別子（workshop、wheat_farm）は判定や目標に使うので変えない。視聴者に見せ、配信者が話す名前を、
LoL のシェンの世界観で別に付ける。設計した建物・施設だけでなく、もうあるものにも付く（呼び名のないものを
見つけて付ける）。呼び名はブリッジが持つ（state.json。ワールドのリセットで一緒に退く）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.exceptions import AILoveShenError

MAX_TITLE = 24  # minecraft-bridge/src/titles.mjs の MAX_TITLE
MAX_AT_ONCE = 8  # 1 回に付ける数（残りは次の機会）

NAMING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "titles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The original name, as given"},
                    "title": {
                        "type": "string",
                        "description": f"The name to call it on stream, Japanese, up to {MAX_TITLE} characters",
                    },
                },
                "required": ["name", "title"],
            },
        }
    },
    "required": ["titles"],
}


class NameGiver:
    """呼び名のない建物・施設を見つけ、LLM に呼び名を付けさせて、ブリッジに置く。"""

    def __init__(
        self,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        retry_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            bridge: 建物・施設の一覧と、呼び名を置く先
            text_generator: LLM（構造化出力。purpose=naming）
            prompt_builder: 呼び名のプロンプト
            retry_seconds: 付けられなかったものを、また試すまでの間
            clock: 時計（テストで差し替える）
        """
        self._bridge = bridge
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._retry = retry_seconds
        self._clock = clock
        self._tried: dict[str, float] = {}

    async def name_missing(self) -> list[str]:
        """呼び名のないものに付ける。付けたものの識別子を返す（なければ空）。失敗しても例外にしない。"""
        try:
            things, taken = await self._missing()
            if not things:
                return []
            now = self._clock()
            for t in things:
                self._tried[t["name"]] = now
            data = await self._text_generator.generate_json(
                self._prompt_builder.build_naming_prompt(things, list(taken)),
                NAMING_SCHEMA,
                purpose="naming",
            )
            known = {t["name"] for t in things}
            titles: dict[str, str] = {}
            for row in data.get("titles") or []:
                name, title = str(row.get("name", "")), str(row.get("title", "")).strip()
                if name in known and 0 < len(title) <= MAX_TITLE and title not in taken:
                    titles[name] = title
                    taken.append(title)
            if not titles:
                logger.warning(f"呼び名が使えなかった: {data!r}")
                return []
            result = await self._bridge.set_titles(titles)
            for name, why in (result.get("refused") or {}).items():
                logger.warning(f"呼び名を置けなかった: {name}: {why}")
            done = [str(n) for n in result.get("set") or []]
            for name in done:
                logger.info(f"呼び名をつけた: {name} = {titles[name]}")
            return done
        except (AILoveShenError, ValueError) as e:
            logger.warning(f"呼び名を付けられなかった（あとでまた試す）: {e}")
            return []

    async def _missing(self) -> tuple[list[dict[str, str]], list[str]]:
        builds = await self._bridge.builds()
        facilities = await self._bridge.facilities()
        built = {str(b["name"]) for b in builds}
        taken = [str(b["title"]) for b in builds if b.get("title")]
        taken += [str(f["title"]) for f in facilities if f.get("title")]
        now = self._clock()
        things: list[dict[str, str]] = []
        for b in builds:
            if not b.get("title"):
                things.append({"name": str(b["name"]), "what": f"建物。{b.get('purpose') or ''}"})
        for f in facilities:
            name = str(f["name"])
            if f.get("title") or name in built or f.get("kind") == "build":
                continue  # 建物は上で数えた
            things.append({"name": name, "what": f"施設（{f.get('kind')}）。{f.get('purpose') or ''}"})
        fresh = [t for t in things if now - self._tried.get(t["name"], -self._retry) >= self._retry]
        return fresh[:MAX_AT_ONCE], taken
