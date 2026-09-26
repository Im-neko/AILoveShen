"""Minecraft ブリッジの出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ailoveshen.domain.value_objects import (
    ActionResult,
    BuildDesign,
    ConditionStatus,
    GameObservation,
    GoalSpec,
    GoalStatus,
    HouseBlueprint,
    SkillDraft,
    SkillInfo,
    SkillRun,
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
    async def run_tool(self, name: str, args: dict[str, Any]) -> tuple[bool, str, float, bool]:
        """
        道具を 1 つ呼ぶ（設計書 21）。行動の道具は終わる（または失敗する、止められる）まで待つ。

        Returns:
            (ok, 結果の文（調べものなら JSON）, 秒数, ブリッジが断ったか)

        Raises:
            GameBridgeError: ブリッジに届かないとき
        """
        ...

    @abstractmethod
    async def state(self) -> dict[str, Any]:
        """
        共通の状態（実行中の行動の進み具合、まわりの形、モブ、欲求）。見張りの質問と道具の選択が見る。

        Raises:
            GameBridgeError: ブリッジに届かないとき
        """
        ...

    @abstractmethod
    async def abort(self, reason: str) -> bool:
        """
        実行中の行動を理由をつけて止める。止めたら True、もう終わっていたら False。

        Raises:
            GameBridgeError: ブリッジに届かないとき
        """
        ...

    @abstractmethod
    async def set_build(self, design: BuildDesign) -> None:
        """
        名前付きの建物を登録する（docs/design/25_builds.md）。ブリッジはアンカーから原点を決め、
        守るもの（家のドア、ベッド、チェスト、室内と床、ほかの建物）に掛かっていないか確かめる。

        Raises:
            GoalRejectedError: 置けないとき（メッセージに理由がある。設計を直させる）
        """
        ...

    @abstractmethod
    async def map(self) -> dict[str, Any]:
        """
        家のまわりの真上から見た地図のデータ: {center, radius, base_y, cells, home, builds}。
        cells[z][x] は [種類, 家の床の層からの高さ] か None（読み込まれていない）。
        """
        ...

    @abstractmethod
    async def builds(self) -> list[dict[str, Any]]:
        """登録した建物: [{name, purpose, anchor, placed, total, complete}]。"""
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
    async def skills(self) -> list[SkillInfo]:
        """覚えた技の一覧（docs/design/22_skills.md）。"""
        raise NotImplementedError

    @abstractmethod
    async def skill(self, name: str) -> SkillInfo | None:
        """技の一番新しい版（コードを含む。直すときに読む）。なければ None。"""
        raise NotImplementedError

    @abstractmethod
    async def save_skill(self, name: str, draft: SkillDraft) -> int:
        """
        技を新しい版として保存し、版の番号を返す。

        Raises:
            SkillRejectedError: 形・構文・expects が正しくないとき（理由つき）
        """
        raise NotImplementedError

    @abstractmethod
    async def run_skill(
        self, name: str, args: dict[str, Any], version: int | None = None
    ) -> SkillRun:
        """技を 1 回実行する（サンドボックス。成功は expects を世界で判定）。"""
        raise NotImplementedError

    @abstractmethod
    async def answer_judge(self, judge_id: int, answer: Any, confidence: float) -> None:
        """技の judge() の質問（状態の pending_judge）に答える。"""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """クライアントが持つリソースを解放する。"""
        ...
