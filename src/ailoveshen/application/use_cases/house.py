"""家の設計: LLM が設計図を書き、HouseBlueprint が確かめる（最初の家と、引っ越し先の家）。"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import CharacterProfile, HouseBlueprint, Side

_SIDES = [s.value for s in Side]

HOUSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "A short name for the house"},
        "concept": {
            "type": "string",
            "description": "One sentence about the idea behind the design",
        },
        "width": {
            "type": "integer",
            "minimum": HouseBlueprint.MIN_SIDE,
            "maximum": HouseBlueprint.MAX_SIDE,
        },
        "depth": {
            "type": "integer",
            "minimum": HouseBlueprint.MIN_SIDE,
            "maximum": HouseBlueprint.MAX_SIDE,
        },
        "wall_height": {
            "type": "integer",
            "minimum": HouseBlueprint.MIN_WALL_HEIGHT,
            "maximum": HouseBlueprint.MAX_WALL_HEIGHT,
        },
        "door_side": {"type": "string", "enum": _SIDES},
        "door_offset": {"type": "integer", "minimum": 1, "maximum": HouseBlueprint.MAX_SIDE - 2},
        "corner_pillars": {"type": "boolean"},
    },
    "required": [
        "name",
        "concept",
        "width",
        "depth",
        "wall_height",
        "door_side",
        "door_offset",
        "corner_pillars",
    ],
}


def parse_blueprint(data: dict[str, Any]) -> HouseBlueprint:
    """
    設計図をパースする（ブリッジが持っている設計も同じ形）。

    Raises:
        ValueError: 形が正しくないか、HouseBlueprint の制約を破るとき
    """
    try:
        return HouseBlueprint(
            name=str(data["name"]),
            concept=str(data["concept"]),
            width=int(data["width"]),
            depth=int(data["depth"]),
            wall_height=int(data["wall_height"]),
            door_side=Side(data["door_side"]),
            door_offset=int(data["door_offset"]),
            corner_pillars=bool(data.get("corner_pillars", False)),
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"malformed blueprint: {e}") from e


class HouseDesigner:
    """
    LLM に家を設計させる。正しくない設計は、検証のエラーをつけて差し戻す
    （max_attempts 回まで）。
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        character: CharacterProfile,
        max_attempts: int = 3,
    ) -> None:
        """
        依存を受け取って初期化する（依存性の注入）。

        Args:
            text_generator: LLM のアダプター（構造化出力）
            prompt_builder: ゲームのプロンプト組み立てのアダプター
            character: 配信者のキャラクターのプロフィール（設計に反映する）
            max_attempts: あきらめるまでに設計を試す回数
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._character = character
        self._max_attempts = max_attempts

    async def design(self, site_note: str = "") -> HouseBlueprint:
        """
        家を設計する。

        Args:
            site_note: 建てる場所についての説明（引っ越し先の家。最初の家では空）

        Raises:
            TextGenerationError: 使える設計が出なかったとき
        """
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_house_design_prompt(
                self._character, site_note=site_note, previous_error=error
            )
            data = await self._text_generator.generate_json(prompt, HOUSE_SCHEMA, purpose="house")
            try:
                blueprint = parse_blueprint(data)
            except ValueError as e:
                error = str(e)
                logger.warning(f"家の設計の試行 {attempt} を差し戻した: {error}")
                continue
            logger.info(
                f"家を設計した: {blueprint.name} "
                f"{blueprint.width}x{blueprint.depth}x{blueprint.wall_height} "
                f"door={blueprint.door_side.value}:{blueprint.door_offset} "
                f"pillars={blueprint.corner_pillars} - {blueprint.concept}"
            )
            return blueprint
        raise TextGenerationError(
            f"no valid house design after {self._max_attempts} attempts: {error}"
        )
