"""
襲われたときの反射を Jev に判断させる（docs/design/28_jev_reflex.md）。

きっかけ（近くの敵、ダメージ、向かってくる敵）を見つけて動き始めるのはブリッジ（コードの規則:
戦うか逃げる）。この見張りは、ブリッジの危険（`GET /danger`）を短い間隔で読み、危険が出たら 1 回、
Jev に選択肢（ブリッジが今できるものだけ）から選ばせ、規則と違えば `POST /reflex` で切り替える。
Jev の答えが遅い・確信度が低い・失敗したときは、規則のまま。安全の決まり（夜に家の中なら外に出ない
など）はブリッジが選択肢で守る。ステップの間も、Gemini が考えている間も動く（別のタスク）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.domain.exceptions import AILoveShenError
from ailoveshen.domain.value_objects import FastQuestion, FastQuestionKind

QUESTION = "reflex"
# 選択肢の説明は中立に書く（spike: 距離や優先の言葉を入れると判断が偏った）
CHOICES = {
    "fight": "fight the target: attack it until it is gone",
    "flee": "run away from the target, about 16 m",
    "go_home": "go into the home and close the door",
    "keep_distance": "move away to about 10 m from the target and watch it",
    "ignore": "ignore it and go on with the current work",
}
TRIGGERS = {
    "close": "a hostile mob is within reach",
    "hurt": "the streamer just took damage",
    "approaching": "a hostile mob is walking toward the streamer",
}


@dataclass(frozen=True)
class ReflexPolicy:
    """見張りの既定値（docs/design/28 §5）。"""

    poll_seconds: float = 0.25
    timeout_seconds: float = 0.8
    min_confidence: float = 0.5


def reflex_question(danger: dict[str, Any]) -> FastQuestion:
    """危険の選択肢から 1 つ選ばせる質問。"""
    target = danger.get("target") or {}
    return FastQuestion(
        name=QUESTION,
        kind=FastQuestionKind.CHOICE,
        instructions=(
            f"{TRIGGERS.get(danger.get('trigger'), 'danger')}: {target.get('name', 'a mob')}. "
            "Choose what the streamer does right now, from the state (health, weapon, the mobs, "
            "the home, the time)."
        ),
        criteria={c: CHOICES.get(c, c) for c in danger.get("options", [])},
    )


class DangerWatcher:
    """反射が動いている間、Jev にどう対処するかを選ばせる。"""

    def __init__(
        self,
        bridge: IMinecraftBridge,
        judge: IFastJudge,
        recorder: Optional[IWatchRecorder] = None,
        policy: ReflexPolicy = ReflexPolicy(),
    ) -> None:
        """
        Args:
            bridge: 危険の読み取りと切り替え
            judge: Jev（1 回の system_one で Choice）
            recorder: 判断の記録（logs/reflex/*.jsonl: 規則と Jev を比べる）
            policy: 間隔、待つ時間、確信度のしきい値
        """
        self._bridge = bridge
        self._judge = judge
        self._recorder = recorder
        self._policy = policy
        self._judged: Optional[int] = None  # 判断した危険の id（1 つの危険には 1 回だけ聞く）

    async def run(self) -> None:
        """キャンセルされるまで見張る。ブリッジや Jev の失敗では止まらない。"""
        while True:
            await self.check_once()
            await asyncio.sleep(self._policy.poll_seconds)

    async def check_once(self) -> Optional[str]:
        """危険があれば 1 回判断する。切り替えたら選んだもの。"""
        try:
            state = await self._bridge.danger()
        except AILoveShenError as e:
            logger.debug(f"危険を読めなかった: {e}")
            return None
        danger = (state or {}).get("danger")
        if not danger or danger.get("id") == self._judged:
            return None
        self._judged = danger["id"]
        return await self._decide(state, danger)

    async def _decide(self, state: dict[str, Any], danger: dict[str, Any]) -> Optional[str]:
        question = reflex_question(danger)
        record: dict[str, Any] = {
            "id": danger["id"],
            "trigger": danger.get("trigger"),
            "target": danger.get("target"),
            "options": danger.get("options"),
            "rule": danger.get("rule"),
            "state": state,
        }
        try:
            verdict = await asyncio.wait_for(
                self._judge.ask(state, (question,)), timeout=self._policy.timeout_seconds
            )
        except (AILoveShenError, TimeoutError) as e:
            self._record({**record, "jev": None, "why": type(e).__name__})
            return None
        answer = verdict.answers.get(QUESTION)
        choice = answer.value if answer else None
        confidence = answer.confidence if answer else 0.0
        record.update(jev=choice, confidence=round(confidence, 3), elapsed_ms=verdict.elapsed_ms)
        if choice not in danger.get("options", []) or confidence < self._policy.min_confidence:
            self._record({**record, "steered": False, "why": "unsure or not an option"})
            return None
        try:
            accepted = await self._bridge.steer_reflex(danger["id"], choice, confidence)
        except AILoveShenError as e:
            self._record({**record, "steered": False, "why": str(e)})
            return None
        self._record({**record, "steered": accepted})
        if accepted and choice != danger.get("rule"):
            logger.info(
                f"反射を Jev が選んだ: {danger.get('rule')} → {choice}（確信度 {confidence:.2f}、"
                f"{danger.get('trigger')} {(danger.get('target') or {}).get('name')}）"
            )
        return choice if accepted else None

    def _record(self, entry: dict[str, Any]) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.record(entry)
        except Exception as e:  # noqa: BLE001 - 記録の失敗で反射を止めない
            logger.warning(f"反射の記録に失敗した: {e}")
