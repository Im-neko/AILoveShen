"""
組み込みの述語で書けない頼みの完了の判定（custom(name)）を Gemini に書かせ、ブリッジに登録する
（docs/design/42_custom_conditions.md）。

判定は世界を読むだけの JS（ブリッジの isolated-vm で同期に動く）。受け取る前に、ブリッジが形を
確かめ、1 回動かし、今もう成り立っていないことを確かめる。だめなら理由をつけて書き直させる。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.exceptions import (
    CheckNotWrittenError,
    GameBridgeError,
    GoalRejectedError,
    TextGenerationError,
)

CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "What the check tests, one English sentence",
        },
        "code": {"type": "string", "description": "function check(w) { ... } in JavaScript"},
    },
    "required": ["description", "code"],
}


class CheckWriter:
    """
    custom(name) の判定を書かせて登録する。まだない名前の `custom(name)` を中目標に足すとき、
    `MidGoalKeeper` が呼ぶ。
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        max_attempts: int = 3,
    ) -> None:
        """
        Args:
            text_generator: LLM（構造化出力）
            prompt_builder: プロンプトの組み立て
            bridge: 登録（確かめて 1 回動かす）
            max_attempts: あきらめるまでに書かせる回数
        """
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._bridge = bridge
        self._max_attempts = max_attempts

    async def known(self) -> set[str]:
        """登録済みの判定の名前。"""
        return {str(c["name"]) for c in await self._bridge.conditions()}

    async def write(self, name: str, brief: str) -> str:
        """
        判定を書かせ、ブリッジに登録する。何を確かめるか（description）を返す。

        Args:
            name: 判定の名前（custom(name) の name）
            brief: 何を確かめるか（中目標の題名と理由）

        Raises:
            CheckNotWrittenError: 使える判定が出なかったとき（最後の理由を持つ）
        """
        place_note = await self._place_note()
        error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = self._prompt_builder.build_check_prompt(
                name=name, brief=brief, place_note=place_note, previous_error=error
            )
            try:
                data = await self._text_generator.generate_json(
                    prompt, CHECK_SCHEMA, purpose="check_write"
                )
                description = str(data.get("description", "")).strip()
                result = await self._bridge.set_condition(
                    name, description, str(data.get("code", ""))
                )
            except GoalRejectedError as e:
                error = str(e)
                logger.warning(f"判定 {name} の試行 {attempt} を差し戻した: {error}")
                continue
            except TextGenerationError as e:
                error = str(e)
                logger.warning(f"判定 {name} を生成できなかった（試行 {attempt}）: {e}")
                continue
            logger.info(
                f"判定を書いた: {name} - {description}（残り {result.get('remaining')}: "
                f"{'; '.join(result.get('lines') or [])}）"
            )
            return description
        raise CheckNotWrittenError(
            f"could not write the check {name} after {self._max_attempts} attempts: {error}",
            name=name,
        )

    async def _place_note(self) -> str:
        """家（ドア、外壁の角）と今の足の位置。判定の座標の基準。"""
        try:
            obs = await self._bridge.observe()
        except GameBridgeError as e:
            logger.warning(f"判定のために今の場所を読めない: {e}")
            return "unknown"
        home = obs.state.get("home") or {}
        me = (obs.state.get("self") or {}).get("position") or {}
        lines = [f"- 今の足の位置: {_xyz(me)}" if me else "- 今の足の位置: unknown"]
        if home:
            lines.append(
                f"- 家: ドア {_xyz(home.get('door'))}、外壁の角 {_xyz(home.get('min'))} 〜 "
                f"{_xyz(home.get('max'))}"
            )
        else:
            lines.append("- 家: まだない")
        return "\n".join(lines)


def _xyz(p: Any) -> str:
    if not isinstance(p, dict):
        return "unknown"
    return ",".join(str(round(float(p.get(k, 0)))) for k in ("x", "y", "z"))
