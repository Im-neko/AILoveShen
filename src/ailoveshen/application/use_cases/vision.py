"""
配信者（Gemini）に配信の画面を見せる（docs/design/23_screen_vision.md）。

いつ見せるかだけを決める: 定期の見直し、失敗の後の考え直し、道具モードの `look_screen`。
どれもステップのループから呼ぶ（小目標を変えるのはループだけ）。画像から分かったことは
配信者の解釈で、完了の判定には使わない。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.screen_capture import IScreenCapture
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.exceptions import TextGenerationError
from ailoveshen.domain.value_objects import ScreenNote, Screenshot

MAX_NOTE_CHARS = 200

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "seen": {
            "type": "string",
            "description": "画面に見えること（1〜2 文。見えたものだけ。推測は書かない）",
        },
        "matches_goal": {
            "type": "boolean",
            "description": "画面の様子が、今の小目標と今やろうとしていることに合っているか",
        },
        "concern": {
            "type": "string",
            "description": "気になること（なければ空）。例: 穴に落ちている、夜なのに外にいる",
        },
        "rethink": {
            "type": "boolean",
            "description": "今の小目標をやめて考え直すべきか（はっきり食い違うときだけ true）",
        },
    },
    "required": ["seen", "matches_goal", "concern", "rethink"],
}


@dataclass(frozen=True)
class VisionPolicy:
    """いつ画面を見せるか（docs/design/23 §2）。"""

    review_interval_seconds: float = 240.0  # 定期の見直しの間隔
    failure_interval_seconds: float = 60.0  # 失敗の後に画像を添える最短の間隔


class ScreenReviewer:
    """配信の画面を撮り、決まった場面で配信者に見せる。"""

    def __init__(
        self,
        capture: IScreenCapture,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        policy: VisionPolicy = VisionPolicy(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            capture: 画面を撮るもの（撮れなければ None を返す）
            text_generator: 見直しに使う LLM
            prompt_builder: 見直しのプロンプトを組み立てるもの
            policy: 間隔
            clock: 時計（テストで差し替える）
        """
        self._capture = capture
        self._text_generator = text_generator
        self._prompt_builder = prompt_builder
        self._policy = policy
        self._clock = clock
        self._last_review: Optional[float] = None
        self._last_failure_shot: Optional[float] = None

    async def review_if_due(self, session: PlaySession) -> None:
        """
        前の見直しから間隔が空いていれば、画面を見せて今の目標と合っているかを聞く。
        「考え直す」なら、次の切れ目で今の小目標を終わらせる（理由つき）。目標は決めない。
        最初の呼び出しは時計を始めるだけ（開始直後は小目標を決めたばかり）。
        """
        now = self._clock()
        if self._last_review is None:
            self._last_review = now
            return
        if now - self._last_review < self._policy.review_interval_seconds:
            return
        self._last_review = now
        if session.goal is None:
            return
        shot = await self._capture.capture()
        if shot is None:
            return
        prompt = self._prompt_builder.build_screen_review_prompt(session.activity())
        try:
            data = await self._text_generator.generate_json(
                prompt, REVIEW_SCHEMA, purpose="screen_review", images=[shot]
            )
        except TextGenerationError as e:
            logger.warning(f"画面の見直しを続けられなかった: {e}")
            return
        note = ScreenNote(
            seen=str(data.get("seen", "")).strip()[:MAX_NOTE_CHARS],
            concern=str(data.get("concern", "")).strip()[:MAX_NOTE_CHARS],
            matches_goal=bool(data.get("matches_goal", True)),
            taken_at=shot.taken_at,
        )
        session.note_screen(note)
        logger.info(
            f"画面の見直し: {note.seen}"
            + (f"（気になること: {note.concern}）" if note.concern else "")
            + ("" if note.matches_goal else "（目標と合っていない）")
        )
        if data.get("rethink") is True:
            session.request_rethink(f"on screen: {note.concern or note.seen}")

    async def after_failure(self) -> Optional[Screenshot]:
        """失敗の後の考え直しに添える 1 枚。最短の間隔より早ければ撮らない。"""
        now = self._clock()
        if (
            self._last_failure_shot is not None
            and now - self._last_failure_shot < self._policy.failure_interval_seconds
        ):
            return None
        shot = await self._capture.capture()
        if shot is not None:
            self._last_failure_shot = now
        return shot

    async def look(self) -> Optional[Screenshot]:
        """配信者が自分で見るとき（道具モードの look_screen）。"""
        return await self._capture.capture()
