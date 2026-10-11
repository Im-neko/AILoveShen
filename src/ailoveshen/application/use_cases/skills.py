"""
技を書く・直す（docs/design/22_skills.md §4）。

道具の選択で配信者が `write_skill` を呼ぶと、別の呼び出し（`purpose=skill_write`、thinking high）で
Gemini に技（JS の手順、引数、できたこと `expects`）を書かせ、ブリッジに保存する。ブリッジが受け取らな
ければ理由をつけて書き直させる。直す回数は 1 つの技につき 1 時間に決まった回数まで（費用の上限）。
実行と成功の判定はブリッジ（サンドボックスと、expects の世界での判定）。
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.exceptions import SkillRejectedError, TextGenerationError
from ailoveshen.domain.value_objects import Activity, SkillDraft, SkillInfo, ToolOutcome

EXPECT_PREDICATES = [
    "have",
    "stored",
    "planted",
    "farmed",
    "built",
    "placed",
    "lit",
    "leveled",
    "custom",
    "facility",
    "progress",
]
SKILLS_SHOWN = 12  # 道具の選択と技を書く呼び出しに見せる技の数
HOUR = 3600.0


def skill_schema(raw_bot: bool = False) -> dict[str, Any]:
    """技を書かせる JSON スキーマ（引数のスキーマと試しの引数は JSON の文字列で受ける）。"""
    schema = {
        "type": "object",
        "properties": {
            "diagnosis": {
                "type": "string",
                "description": "Only when fixing a skill that failed: the cause found in the failure "
                "record (which call failed or was refused, its message, and why), then what the fix "
                "changes. One or two sentences. Empty for a new skill",
            },
            "description": {
                "type": "string",
                "description": "What the skill does, one short sentence in Japanese",
            },
            "params_json": {
                "type": "string",
                "description": 'JSON Schema of the arguments, e.g. {"type": "object", '
                '"properties": {"animal": {"type": "string"}}}',
            },
            "expects": {
                "type": "object",
                "description": "What holds in the world when the skill succeeded",
                "properties": {
                    "predicate": {"type": "string", "enum": EXPECT_PREDICATES},
                    "item": {"type": "string", "description": "have / stored / planted / farmed: item, crop or group"},
                    "count": {
                        "type": "string",
                        "description": 'have / stored / planted / farmed: a number, "+N" (N more than before) '
                        'or "$param"',
                    },
                    "name": {
                        "type": "string",
                        "description": "built: the build's name; custom: the check's name; facility: the "
                        "facility's name",
                    },
                    "where": {
                        "type": "string",
                        "description": "placed: home; leveled: home or x,y,z",
                    },
                    "distance": {"type": "integer", "description": "lit / leveled: radius"},
                },
                "required": ["predicate"],
            },
            "code": {
                "type": "string",
                "description": "export default async function (t, args) { ... } (at most 4000 "
                "characters)",
            },
            "trial_args_json": {
                "type": "string",
                "description": "Arguments for the first trial right now, as JSON (usually {})",
            },
        },
        "required": ["description", "params_json", "expects", "code", "trial_args_json"],
    }
    if raw_bot:
        schema["properties"]["raw"] = {
            "type": "boolean",
            "description": "true when the code uses t.bot (the real mineflayer bot), t.Vec3, t.goals "
            "or t.sleep; false for a skill over the t.* tools only",
        }
    return schema


@dataclass(frozen=True)
class WrittenSkill:
    """書いて保存した技（すぐ試す）。"""

    name: str
    version: int
    description: str
    trial_args: dict[str, Any]
    revised_because: str = ""  # 直したとき、見つけた原因（なければ前の失敗）


@dataclass(frozen=True)
class NotWritten:
    """書けなかった理由（道具の結果として配信者に返す）。"""

    why: str


def order_skills(skills: Sequence[SkillInfo], goal_item: Optional[str]) -> list[SkillInfo]:
    """今の小目標に合う技（expects の品目が同じ）を先に、次に成功の多いもの。最大 SKILLS_SHOWN。"""

    def key(s: SkillInfo) -> tuple:
        fits = goal_item is not None and s.expects.get("item") == goal_item
        return (not fits, not s.verified, -s.successes, s.name)

    return sorted(skills, key=key)[:SKILLS_SHOWN]


class SkillWriter:
    """Gemini に技を書かせて（直させて）、ブリッジに保存する。"""

    def __init__(
        self,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        bridge: IMinecraftBridge,
        rewrites_per_hour: int = 3,
        max_attempts: int = 3,
        clock: Callable[[], float] = time.monotonic,
        raw_bot: bool = False,
    ) -> None:
        """
        Args:
            text_generator: 技を書くモデル
            prompt_builder: 技を書くプロンプト（システム指示と本文）
            bridge: 技の保存（形と構文を確かめる）と、直すときに前のコードを読む
            rewrites_per_hour: 1 つの技を書ける回数（1 時間あたり。新しく書くのも 1 回）
            max_attempts: ブリッジが受け取らなかったときに書き直させる回数
            clock: 時計（テストで差し替える）
            raw_bot: 生の bot を触る技（raw: true）を書かせる（docs/design/168。ブリッジも BRIDGE_RAW_BOT=1）
        """
        self._generator = text_generator
        self._prompts = prompt_builder
        self._bridge = bridge
        self._per_hour = rewrites_per_hour
        self._attempts = max_attempts
        self._clock = clock
        self._raw_bot = raw_bot
        self._written: dict[str, deque[float]] = defaultdict(deque)

    async def write(
        self,
        name: str,
        what: str,
        activity: Activity,
        recent_tools: Sequence[ToolOutcome],
        skills: Sequence[SkillInfo],
        last_failure: str = "",
    ) -> WrittenSkill | NotWritten:
        """技を書いて保存する。直すとき（同じ名前がある）は前のコードと失敗を見せる。"""
        now = self._clock()
        times = self._written[name]
        while times and now - times[0] > HOUR:
            times.popleft()
        if len(times) >= self._per_hour:
            return NotWritten(
                f"{name} was written {len(times)} times in the last hour (at most "
                f"{self._per_hour}); use the tools directly for now"
            )
        times.append(now)
        previous = await self._bridge.skill(name)
        error = ""
        for attempt in range(1, self._attempts + 1):
            prompt = self._prompts.build_skill_prompt(
                name=name,
                what=what,
                activity=activity,
                recent_tools=recent_tools,
                skills=[s for s in skills if s.name != name],
                previous=previous,
                last_failure=last_failure,
                previous_error=error,
            )
            try:
                data = await self._generator.generate_json(
                    prompt,
                    skill_schema(self._raw_bot),
                    system_instruction=self._prompts.build_skill_system(),
                    purpose="skill_write",
                )
                draft, trial = _parse(data)
                if previous is not None:
                    _check_fix(data, draft, previous)
                version = await self._bridge.save_skill(name, draft)
            except (ValueError, SkillRejectedError) as e:
                error = str(e)
                logger.warning(f"技 {name} の書き直し {attempt}: {error}")
                continue
            except TextGenerationError as e:
                return NotWritten(f"could not write the skill: {e}")
            logger.info(f"技を書いた: {name} v{version}「{draft.description}」")
            return WrittenSkill(
                name=name,
                version=version,
                description=draft.description,
                trial_args=trial,
                revised_because=(
                    str(data.get("diagnosis") or "").strip() or last_failure
                    if previous is not None
                    else ""
                ),
            )
        return NotWritten(
            f"the bridge did not accept the skill after {self._attempts} tries: {error}"
        )


MIN_DIAGNOSIS = 20  # 原因の文の最小の長さ（「直す」だけで通さない）


def _check_fix(data: dict[str, Any], draft: SkillDraft, previous: SkillInfo) -> None:
    """
    失敗した技を直す書き込みが、原因を調べて直したものか。原因を書かない、前と同じコードは差し戻す
    （2026-10-08: 同じ形の技を書き直しても、失敗の理由を読まないまま同じ失敗を繰り返した）。
    """
    if len(str(data.get("diagnosis") or "").strip()) < MIN_DIAGNOSIS:
        raise ValueError(
            "this fixes a skill that failed: write the cause in diagnosis first (which call failed "
            "or was refused in the failure record, its message, and why), then change the code for "
            "that cause"
        )
    if draft.code.strip() == previous.code.strip():
        raise ValueError(
            f"the code is the same as v{previous.version}: a fix must change the code for the cause "
            "in diagnosis. If the cause is not in the code, do not rewrite the skill"
        )


def _parse(data: dict[str, Any]) -> tuple[SkillDraft, dict[str, Any]]:
    """モデルの出力を技の下書きと試しの引数にする。形が悪ければ ValueError。"""
    try:
        params = json.loads(data.get("params_json") or "{}")
        trial = json.loads(data.get("trial_args_json") or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"params_json and trial_args_json must be JSON: {e}") from e
    if not isinstance(params, dict) or not isinstance(trial, dict):
        raise ValueError("params_json and trial_args_json must be JSON objects")
    expects = {k: v for k, v in dict(data.get("expects") or {}).items() if v not in (None, "")}
    count = expects.get("count")
    if isinstance(count, str) and count.strip().isdigit():
        expects["count"] = int(count)
    draft = SkillDraft(
        description=str(data.get("description", "")).strip(),
        params=params or {"type": "object", "properties": {}},
        expects=expects,
        code=str(data.get("code", "")),
        raw=data.get("raw") is True,
    )
    return draft, trial
