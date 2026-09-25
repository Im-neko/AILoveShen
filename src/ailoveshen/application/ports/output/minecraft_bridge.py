"""Minecraft ブリッジの出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ailoveshen.domain.value_objects import (
    ActionResult,
    ConditionStatus,
    GameObservation,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    TownSite,
)


class IMinecraftBridge(ABC):
    """
    Minecraft を遊ぶプロセス（Mineflayer のサイドカー）の出力ポート。

    実際の世界が要ることはすべてブリッジが持つ: 目標を世界から判定すること、
    それを今できること（候補）に分解すること、候補を 1 つ実行すること、建築プラン。
    このインターフェースはアプリケーション層で定義する。
    """

    @abstractmethod
    async def observe(self) -> GameObservation:
        """
        ゲームのスナップショットを取る: 目標の状態と候補。

        Raises:
            GameBridgeError: ブリッジに届かないか、ブリッジが世界にいないとき
        """
        ...

    @abstractmethod
    async def set_goal(self, spec: GoalSpec, keep: Sequence[GoalSpec] = ()) -> GoalStatus:
        """
        候補を具体化する対象の目標を設定する。

        Args:
            spec: 小目標
            keep: 中目標の stored() 条件。チェストがそのために取っておくものは、この目標の
                ために取り出さない（食料は飢えているときだけ取り出す）

        Raises:
            GoalRejectedError: ブリッジが目標を拒否したとき（理由つき）
            GameBridgeError: ブリッジに届かないとき
        """
        ...

    @abstractmethod
    async def check(self, specs: Sequence[GoalSpec]) -> list[ConditionStatus]:
        """
        目標を設定せずに、中目標の条件を世界から判定する。

        Raises:
            GoalRejectedError: spec が条件になれないとき（メッセージに理由がある）
            GameBridgeError: ブリッジに届かないとき
        """
        ...

    @abstractmethod
    async def act(self, action_id: str) -> ActionResult:
        """
        候補を 1 つ、終わる（または失敗する）まで実行し、結果を返す。

        Raises:
            GameBridgeError: ブリッジに届かないか、塞がっているとき
        """
        ...

    @abstractmethod
    async def set_build_plan(self, blueprint: HouseBlueprint, site: TownSite | None = None) -> None:
        """
        建てる家をブリッジに渡す: 置く順に並べたブロックと、設計そのもの。設計は、
        ブリッジが建った家と一緒に持っておく（観測の home design）。`site` があれば
        そこに建てる（引っ越し先。建ち終わると家になり、前の家は残る）。なければ
        ボットのいる所のまわりに建てる。

        Raises:
            GameBridgeError: ブリッジがプランを拒否したとき
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """クライアントが持つリソースを解放する。"""
        ...
