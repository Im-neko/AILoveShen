"""ゲームのプロンプト組み立ての出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ailoveshen.domain.value_objects import (
    Activity,
    Candidate,
    CharacterProfile,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalPredicate,
    HouseBlueprint,
    Mission,
    ToolOutcome,
    TownDefinition,
    TownSite,
    TownStage,
)


class IGamePromptBuilder(ABC):
    """
    ゲームのエージェントを方向づけるプロンプトを組み立てる出力ポート。

    このインターフェースはアプリケーション層で定義する。
    インフラ層のアダプターがこれを実装する。
    """

    @abstractmethod
    def build_build_design_prompt(
        self,
        character: CharacterProfile,
        name: str,
        brief: str,
        home_note: str,
        builds_note: str = "",
        map_shown: bool = False,
        previous_error: str = "",
    ) -> str:
        """
        名前付きの建物（増築、倉庫、塔…）の設計を頼むプロンプトを組み立てる
        （docs/design/25_builds.md）。

        Args:
            character: 配信者
            name: 建物の名前（中目標の条件の built(name)）
            brief: 何のための建物か（中目標の題名と理由、視聴者の頼み）
            home_note: 家の大きさとドアの向き
            builds_note: ほかの建物（名前、置き場所、大きさ）
            map_shown: 地図の画像を添えるか（添えるなら、マス目で置き場所を選べる）
            previous_error: 前回の設計が使えなかった理由（あれば）
        """
        ...

    @abstractmethod
    def build_house_design_prompt(
        self, character: CharacterProfile, site_note: str = "", previous_error: str = ""
    ) -> str:
        """
        LLM に小さな家の設計を頼むプロンプトを組み立てる。

        Args:
            character: 配信者。設計にはその性格を反映する
            site_note: 建てる場所の説明（引っ越し先の家。最初の家では空）
            previous_error: 前回の設計が拒否された理由（あれば）
        """
        ...

    @abstractmethod
    def build_site_prompt(
        self,
        character: CharacterProfile,
        mission: Mission,
        sites: Sequence[dict[str, Any]],
        previous_error: str = "",
    ) -> str:
        """
        調べた候補地の表から、街の場所を 1 か所選ばせるプロンプトを組み立てる。

        Args:
            character: 配信者
            mission: 街を作る大目標
            sites: ブリッジが測った候補地の数字（1 行が 1 か所）
            previous_error: 前回の選択が拒否された理由（あれば）
        """
        ...

    @abstractmethod
    def describe_site(self, site: TownSite, facts: dict[str, Any]) -> str:
        """選んだ街の場所の説明（決めたことと、そこの数字）。家の設計に添える。"""
        ...

    @abstractmethod
    def build_town_prompt(
        self,
        character: CharacterProfile,
        mission: Mission,
        site: TownSite,
        facts: dict[str, Any],
        previous_error: str = "",
    ) -> str:
        """
        大目標の街がどんなものかを、段階に分けて LLM に尋ねるプロンプトを組み立てる。

        Args:
            character: 配信者
            mission: 街を定める大目標
            site: 選んだ街の場所（街はそこに合わせて定める）
            facts: その場所の、ブリッジが測った数字
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
    def build_tool_prompt(
        self,
        activity: Activity,
        state: dict[str, Any],
        suggestions: Sequence[Candidate],
        recent_tools: Sequence[ToolOutcome],
    ) -> str:
        """
        配信者に道具を 1 つ選ばせるプロンプトを組み立てる（設計書 21 §6）。

        Args:
            activity: 配信者が今していること（小目標と進み具合を含む）
            state: ブリッジの共通の状態（まわりの形、モブと id、欲求）
            suggestions: ソルバーの提案（参考。従わなくてよい）
            recent_tools: 直近の道具の呼び出しと結果（古い順）
        """
        ...

    @abstractmethod
    def build_screen_review_prompt(self, activity: Activity) -> str:
        """
        配信の画面（添えた画像）と、配信者が今していることを見比べさせるプロンプト
        （docs/design/23 §2 の定期の見直し）。
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
