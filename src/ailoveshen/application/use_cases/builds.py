"""
名前付きの建物を Gemini に設計させ、ブリッジに登録する（docs/design/25_builds.md）。

設計は形の組み合わせ（`BuildDesign`）で、コードがブロックに展開する。受け取る前に、形の決まり
（domain）、材料が手に入るか（ブリッジの `/check`）、置けるか（ブリッジの `PUT /builds`: 家の
ドア・ベッド・チェスト・室内と床、ほかの建物）を確かめる。だめなら理由をつけて作り直させる。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.exceptions import GoalRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import (
    BlockKind,
    BuildAnchor,
    BuildDesign,
    BuildShape,
    CharacterProfile,
    GoalPredicate,
    GoalSpec,
    ShapeKind,
)

MAX_HAVE_COUNT = 64  # ブリッジの have の上限（手に入るかを確かめるだけなので、数はここまでで十分）
_MATERIALS = [BlockKind.PLANKS, BlockKind.LOG, BlockKind.COBBLESTONE, BlockKind.DIRT]
_POINT = {
    "type": "object",
    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "z": {"type": "integer"}},
    "required": ["x", "y", "z"],
}

BUILD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "purpose": {
            "type": "string",
            "description": "One sentence: what the build is for and what it looks like",
        },
        "anchor": {"type": "string", "enum": [a.value for a in BuildAnchor]},
        "shapes": {
            "type": "array",
            "minItems": 1,
            "maxItems": BuildDesign.MAX_SHAPES,
            "items": {
                "type": "object",
                "properties": {
                    "shape": {"type": "string", "enum": [k.value for k in ShapeKind]},
                    "block": {
                        "type": "string",
                        "enum": [m.value for m in _MATERIALS],
                        "description": "fill and hollow_box only",
                    },
                    "from": _POINT,
                    "to": {**_POINT, "description": "Leave out for door"},
                },
                "required": ["shape", "from"],
            },
        },
    },
    "required": ["purpose", "anchor", "shapes"],
}


def parse_build(data: dict[str, Any], name: str) -> BuildDesign:
    """
    設計をパースする。

    Raises:
        ValueError: 形が正しくないか、BuildDesign の決まりを破るとき（理由は Gemini に返す）
    """
    try:
        shapes = []
        for i, item in enumerate(data["shapes"]):
            kind = ShapeKind(item["shape"])
            start = _point(item["from"])
            end = _point(item["to"]) if item.get("to") else start
            if kind == ShapeKind.DOOR:
                end = start
            block = BlockKind(item["block"]) if item.get("block") else None
            try:
                shapes.append(BuildShape(kind, start, end, block))
            except ValueError as e:
                raise ValueError(f"shape {i + 1} ({kind.value}): {e}") from e
        return BuildDesign(
            name=name,
            purpose=str(data.get("purpose", "")).strip(),
            anchor=BuildAnchor(data["anchor"]),
            shapes=tuple(shapes),
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"malformed build design: missing or wrong {e}") from e


def _point(p: dict[str, Any]) -> tuple[int, int, int]:
    return int(p["x"]), int(p["y"]), int(p["z"])


class BuildDesigner:
    """
    名前付きの建物を設計させて登録する。まだない名前の `built(name)` を中目標に足すとき、
    `MidGoalKeeper` が呼ぶ。
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        character: CharacterProfile,
        max_attempts: int = 3,
    ) -> None:
        """
        Args:
            text_generator: LLM（構造化出力）
            prompt_builder: プロンプトの組み立て
            bridge: 材料の確認と登録
            character: 配信者（設計に性格を反映する）
            max_attempts: あきらめるまでに設計させる回数
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._character = character
        self._max_attempts = max_attempts

    async def known(self) -> set[str]:
        """登録済みの建物の名前。"""
        return {str(b["name"]) for b in await self._bridge.builds()}

    async def design(self, name: str, brief: str) -> BuildDesign:
        """
        設計させ、確かめて登録する。

        Args:
            name: 建物の名前（built(name) の name）
            brief: 何のための建物か（中目標の題名と理由）

        Raises:
            GoalRejectedError: 使える設計が出なかったとき（最後の理由を持つ）
        """
        home_note, builds_note = await self._notes()
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_build_design_prompt(
                self._character,
                name=name,
                brief=brief,
                home_note=home_note,
                builds_note=builds_note,
                previous_error=error,
            )
            try:
                data = await self._text_generator.generate_json(
                    prompt, BUILD_SCHEMA, purpose="build_design"
                )
                design = parse_build(data, name)
                await self._check_materials(design)
                await self._bridge.set_build(design)
            except (ValueError, GoalRejectedError) as e:
                error = str(e)
                logger.warning(f"建物 {name} の設計の試行 {attempt} を差し戻した: {error}")
                continue
            except TextGenerationError as e:
                error = str(e)
                logger.warning(f"建物 {name} の設計を生成できなかった（試行 {attempt}）: {e}")
                continue
            width, height, depth = design.size()
            logger.info(
                f"建物を設計した: {name} {width}x{depth}x{height}"
                f"（{len(design.blocks())} ブロック、{design.anchor.value}）- {design.purpose}"
            )
            return design
        raise GoalRejectedError(
            f"could not design the build {name} after {self._max_attempts} attempts: {error}"
        )

    async def _check_materials(self, design: BuildDesign) -> None:
        specs = [
            GoalSpec(GoalPredicate.HAVE, item=kind.value, count=min(count, MAX_HAVE_COUNT))
            for kind, count in design.material_counts().items()
        ]
        for status in await self._bridge.check(specs):
            if status.impossible:
                raise ValueError(
                    f"{status.spec.item} cannot be obtained now ({', '.join(status.impossible)}): "
                    "use other materials"
                )

    async def _notes(self) -> tuple[str, str]:
        obs = await self._bridge.observe()
        home = obs.state.get("home") or {}
        design = home.get("design") or {}
        if design:
            home_note = (
                f"「{design.get('name', '家')}」: 幅 {design.get('width')}（x）× 奥行き "
                f"{design.get('depth')}（z）、壁の高さ {design.get('wall_height')}、ドアは "
                f"{design.get('door_side')} の壁"
            )
        else:
            home_note = "家はある（大きさは分からない）" if home else "家はまだない"
        builds = obs.state.get("builds") or []
        builds_note = "\n".join(
            f"- {b['name']}（{b.get('anchor')}、{b.get('placed')}/{b.get('total')} ブロック）: "
            f"{b.get('purpose', '')}"
            for b in builds
        )
        return home_note, builds_note
