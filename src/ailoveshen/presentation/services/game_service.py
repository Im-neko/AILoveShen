"""プレゼンテーション層のゲームサービス。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger

from ailoveshen.application.ports.input.play import IAdvancePlay, IStartPlay
from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.mid_goals import MidGoalKeeper
from ailoveshen.domain.entities import PlaySession


@dataclass(frozen=True)
class PlayOutcome:
    """プレイセッションの結果。"""

    session: PlaySession
    steps: int
    house_complete: bool


class GameService:
    """
    ゲームのエージェントを動かすプレゼンテーション層のサービス。

    LLM が家を設計して目標を決め、ブリッジが目標を判定して候補を具体化し、
    行動の選択器が候補を 1つずつ選ぶ。家が完成した後も（夜、食料など）、
    ステップの予算を使い切るまでセッションは続く。
    """

    WAIT_SECONDS = 1.0  # ブリッジの反射が動いている間は待つ

    def __init__(
        self,
        start_play: IStartPlay,
        advance_play: IAdvancePlay,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        action_selector: IActionSelector,
        mid_goals: MidGoalKeeper,
    ) -> None:
        """
        ゲームサービスを初期化する。

        Args:
            start_play: 家を設計してセッションを始めるユースケース
            advance_play: 1ステップ進めるユースケース
            bridge: Minecraft ブリッジ。サービスと一緒に閉じる
            text_generator: LLM。サービスと一緒に閉じる
            action_selector: 行動の選択器。サービスと一緒に閉じる
            mid_goals: 中目標を持つ（チャットへの返答は、これを通して視聴者の頼みを加える）
        """
        self._start = start_play
        self._advance = advance_play
        self._bridge = bridge
        self._text_generator = text_generator
        self._action_selector = action_selector
        self._mid_goals = mid_goals
        self._session: PlaySession | None = None

    @property
    def session(self) -> PlaySession | None:
        """プレイ中のセッション（実況とチャットへの返答が見るもの）。play() の前は None。"""
        return self._session

    @property
    def mid_goals(self) -> MidGoalKeeper:
        """中目標を持つ。チャットへの返答は、これを通して視聴者の頼みを受ける。"""
        return self._mid_goals

    async def play(self, max_steps: int = 200) -> PlayOutcome:
        """
        家を設計し、max_steps 回行動するまでプレイする。

        Args:
            max_steps: 行う行動の数（ブリッジの反射を待ったステップは数えない）

        Returns:
            セッション、行った行動の数、家が完成したか
        """
        session = await self._start.execute()
        self._session = session
        steps = 0
        house_complete = False
        while steps < max_steps:
            report = await self._advance.execute(session)
            house_complete = report.house_complete
            if report.waiting:
                await asyncio.sleep(self.WAIT_SECONDS)
                continue
            steps += 1
            if report.result is not None and report.decision is not None:
                goal = report.goal.spec.describe() if report.goal else "-"
                remaining = report.status.remaining if report.status else "-"
                logger.info(
                    f"[{steps}] {goal}（残り {remaining}）-> {report.decision.action_id} "
                    f"（確信度 {report.decision.confidence:.2f}）: "
                    f"{'成功' if report.result.ok else '失敗'} {report.result.result} "
                    f"（{report.result.seconds}秒）"
                )
        house = f"家「{session.blueprint.name}」" if session.blueprint else "前に建てた家"
        logger.info(f"{steps} ステップ遊んだ。{house}は{'完成' if house_complete else '未完成'}")
        return PlayOutcome(session=session, steps=steps, house_complete=house_complete)

    async def close(self) -> None:
        """ブリッジのクライアントとモデルのクライアントを閉じる。"""
        await self._bridge.close()
        await self._text_generator.close()
        await self._action_selector.close()
