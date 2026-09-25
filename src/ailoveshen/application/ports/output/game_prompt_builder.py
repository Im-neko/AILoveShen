"""ゲームのプロンプト組み立ての出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ailoveshen.domain.value_objects import (
    Activity,
    CharacterProfile,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalPredicate,
    HouseBlueprint,
    Mission,
    TownDefinition,
    TownStage,
)


class IGamePromptBuilder(ABC):
    """
    ゲームのエージェントを方向づけるプロンプトを組み立てる出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def build_house_design_prompt(
        self, character: CharacterProfile, previous_error: str = ""
    ) -> str:
        """
        LLM に小さな家の設計を頼むプロンプトを組み立てる。

        Args:
            character: 配信者。設計にはその性格を反映する
            previous_error: 前回の設計が拒否された理由（あれば）
        """
        ...

    @abstractmethod
    def build_town_prompt(
        self, character: CharacterProfile, mission: Mission, previous_error: str = ""
    ) -> str:
        """
        大目標の街がどんなものかを、段階に分けて LLM に尋ねるプロンプトを組み立てる。

        Args:
            character: 配信者
            mission: 街を定める大目標
            previous_error: 前回の定義が拒否された理由（あれば）
        """
        ...

    @abstractmethod
    def build_stage_prompt(
        self, town: TownDefinition, stage: TownStage, previous_error: str = ""
    ) -> str:
        """
        段階のうち未解決の部分を、今使える条件で書くよう LLM に頼むプロンプトを組み立てる
        （段階の題名と意味は変えない）。

        Args:
            town: その段階が属する街
            stage: まだ解決していない部分がある段階
            previous_error: 前回の答えが拒否された理由（あれば）
        """
        ...

    @abstractmethod
    def build_goal_prompt(
        self,
        blueprint: HouseBlueprint | None,
        activity: Activity,
        goal_ended_because: str,
        recent_messages: Sequence[ConversationMessage],
        predicates: Sequence[GoalPredicate],
        previous_error: str = "",
    ) -> str:
        """
        LLM に次の目標を決めさせるプロンプトを組み立てる。

        Args:
            blueprint: 建てている家（None: 家は前の実行で建った）
            activity: 配信者が今していること（大目標と中目標、終わる目標とその状態、
                最近の目標）。実況と返答が見るものと同じ
            goal_ended_because: 新しい目標が要る理由
            recent_messages: 配信で話されたこと（配信者の言葉と視聴者のチャット）
            predicates: 今意味のある述語
            previous_error: 前回の目標、または中目標の編集が使えなかった理由
        """
        ...

    @abstractmethod
    def build_action_context(
        self, goal: Goal, observation: GameObservation
    ) -> tuple[dict[str, Any], str]:
        """
        行動選択器が見るもの（状態と指示）を組み立てる。

        状態は体の必要（数値と深刻さ）を示すが、どの行動を取るかは示さない。指示は
        優先順位をつけない（どちらも計測した: spikes/primitive_choice_eval.py）。
        """
        ...
