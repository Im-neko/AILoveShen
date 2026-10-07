"""ゲームのプロンプト組み立ての出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Optional

from ailoveshen.domain.value_objects import (
    Activity,
    Candidate,
    CharacterProfile,
    ConditionStatus,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalPredicate,
    HouseBlueprint,
    Mission,
    Objective,
    SameCause,
    SkillInfo,
    ToolOutcome,
    TownDefinition,
    TownMap,
    TownSite,
    TownStage,
    Zone,
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
        max_blocks: int = 600,
        previous_error: str = "",
    ) -> str:
        """
        名前付きの建物（増築、倉庫、塔…）の設計を頼むプロンプトを組み立てる
        （docs/design/25_builds.md）。

        Args:
            character: 配信者
            name: 建物の名前（大目標の条件の built(name)）
            brief: 何のための建物か（大目標の題名と理由、視聴者の頼み）
            home_note: 家の大きさとドアの向き
            builds_note: ほかの建物（名前、置き場所、大きさ）
            map_shown: 地図の画像を添えるか（添えるなら、マス目で置き場所を選べる）
            max_blocks: 展開したブロックの上限
            previous_error: 前回の設計が使えなかった理由（あれば）
        """
        ...

    @abstractmethod
    def build_check_prompt(
        self, name: str, brief: str, place_note: str, previous_error: str = ""
    ) -> str:
        """
        custom(name) の完了の判定（JS）を書かせるプロンプトを組み立てる
        （docs/design/42_custom_conditions.md）。

        Args:
            name: 判定の名前（大目標の条件の custom(name) の name）
            brief: 何を確かめるか（大目標の題名と理由、視聴者の頼み）
            place_note: 家と今の場所（座標の基準）
            previous_error: 前回の判定が使えなかった理由（あれば）
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
            mission: 街を作る最終目標
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
        最終目標の街がどんなものかを、段階に分けて LLM に尋ねるプロンプトを組み立てる。

        Args:
            character: 配信者
            mission: 街を定める最終目標
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
    def build_town_map_prompt(
        self,
        town: TownDefinition,
        town_stage: int,
        zones: list[dict[str, Any]],
        base: list[Zone],
        current: Optional[TownMap],
        reason: str,
        previous_error: str = "",
    ) -> str:
        """
        街の理想図（区画ごとの用途）を描かせる・描き直させるプロンプト（docs/design/47 §6）。

        Args:
            town: 街の定義と段階
            town_stage: 済んだ段階の数
            zones: ブリッジが測った区画の地形（GET /zones）
            base: コードが決めた区画（固定: 今ある施設、水・溶岩）
            current: 今の理想図（描き直しのとき）
            reason: 描く・描き直す理由
            previous_error: 前回の答えが拒否された理由（あれば）
        """
        ...

    @abstractmethod
    def build_plan_prompt(
        self,
        objective: Objective,
        activity: Activity,
        replan_reason: str = "",
        previous_error: str = "",
    ) -> str:
        """
        大目標の計画（中目標と、その小目標の並び）を書かせるプロンプト（docs/design/50 §18）。

        Args:
            objective: 計画を立てる大目標（一番上のもの）
            activity: 今していることと状況
            replan_reason: 書き直すときの理由（失敗、行き詰まり、死んだこと。初めてなら空）
            previous_error: 前回の出力が使えなかった理由（あれば）
        """
        ...

    @abstractmethod
    def build_facility_prompt(
        self,
        name: str,
        brief: str,
        town_map: Optional[TownMap],
        zones: list[dict[str, Any]],
        planned: list[str],
        previous_error: str = "",
    ) -> str:
        """
        施設（畑・植林場）を設計させるプロンプト（docs/design/47 §7）。

        Args:
            name: 施設の名前
            brief: 何のための施設か（大目標の題名と理由）
            town_map: 街の理想図（なければ家のまわりに作る）
            zones: ブリッジが測った区画の地形（GET /zones。理想図がなければ空）
            planned: 理想図でこの名前に予定した区画
            previous_error: 前回の設計が拒否された理由（あれば）
        """
        ...

    @abstractmethod
    def vocabulary_version(self) -> str:
        """
        街の段階を書くときに見せる語彙（使える条件と、できること）の版。変わったときだけ、
        未解決の段階を書き直す価値がある。
        """
        ...

    @abstractmethod
    def build_goal_system(self) -> str:
        """
        目標の決定のシステム指示（決まり、述語の説明の全部、出力の書き方）。状態では変わらない
        （暗黙のキャッシュが効くように先頭に置く。docs/design/26_steps_and_context.md §4）。
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
        failure_record: Sequence[str] = (),
        offered: Sequence[Candidate] = (),
        retry_allowed: bool = True,
        job_status: Sequence[ConditionStatus] = (),
        death_report: str = "",
    ) -> str:
        """
        LLM に次の目標を決めさせるプロンプトを組み立てる。

        Args:
            blueprint: 建てている家（None: 家は前の実行で建った）
            activity: 配信者が今していること（最終目標と大目標、終わる目標とその状態、
                最近の目標）。実況と返答が見るものと同じ
            goal_ended_because: 新しい目標が要る理由
            recent_messages: 配信で話されたこと（配信者の言葉と視聴者のチャット）
            predicates: 今意味のある述語
            previous_error: 前回の目標、または大目標の編集が使えなかった理由
            failure_record: 行き詰まった・進まなかった小目標の行動の記録（古い順。空: 失敗ではない）。
                あれば原因を分析させる（docs/design/27）
            offered: その小目標で最後に出ていた候補
            retry_allowed: 同じ小目標のやり直しを選べるか（同じ失敗が続いたら選べない）
            job_status: 一番上の大目標の中目標を今判定したもの（見直しの材料。docs/design/31）
            death_report: 死んだときの様子（死んだ直後だけ。何で・どんな状況で死んだか。docs/design/105）
        """
        ...

    @abstractmethod
    def build_skill_system(self) -> str:
        """技を書くシステム指示（API の説明と書き方。状態では変わらない。docs/design/22）。"""
        ...

    @abstractmethod
    def build_skill_prompt(
        self,
        name: str,
        what: str,
        activity: Activity,
        recent_tools: Sequence[ToolOutcome],
        skills: Sequence[SkillInfo],
        previous: SkillInfo | None = None,
        last_failure: str = "",
        previous_error: str = "",
    ) -> str:
        """
        技を書く（直す）本文。

        Args:
            name: 技の名前
            what: 技がすること（直すときは何を直すか）
            activity: 配信者が今していること
            recent_tools: 直近の道具の呼び出しと結果（手順の手本）
            skills: ほかの技（重複を避け、使い回す）
            previous: 同じ名前の一番新しい版（直すとき。コードを含む）
            last_failure: その技の前の失敗（理由、記録、最後の道具）
            previous_error: 前に書いたものをブリッジが受け取らなかった理由
        """
        ...

    @abstractmethod
    def build_tool_prompt(
        self,
        activity: Activity,
        state: dict[str, Any],
        suggestions: Sequence[Candidate],
        recent_tools: Sequence[ToolOutcome],
        skills: Sequence[SkillInfo] | None = None,
        failing: int = 0,
        stagnant_seconds: float = 0.0,
        stagnant_actions: Sequence[str] = (),
        repeated: Sequence[str] = (),
        repeated_times: int = 0,
        repeated_skill: str = "",
        same_cause: SameCause | None = None,
    ) -> str:
        """
        配信者に道具を 1 つ選ばせるプロンプトを組み立てる（設計書 21 §6）。

        Args:
            activity: 配信者が今していること（小目標と進み具合を含む）
            state: ブリッジの共通の状態（まわりの形、モブと id、欲求）
            suggestions: ソルバーの提案（参考。従わなくてよい）
            recent_tools: 直近の道具の呼び出しと結果（古い順）
            skills: 覚えた技（None: 技を使わない。docs/design/22）
            failing: 今の小目標で続けて失敗した行動の数（行き詰まっていれば、別の方法を考えさせる）
            stagnant_seconds: 位置・持ち物・小目標の進み具合が変わらない秒数（0: 動いている）
            stagnant_actions: 変わらない間にした手と結果（古い順）
            repeated: 続けて成功した同じ手の並び（技にする機会。空: 繰り返していない）
            repeated_times: その並びを続けた回数
            repeated_skill: その並びに合う、すでに覚えた技の名前（なければ空）
            same_cause: 同じ道具が同じ理由で失敗し続けているとき、その状態（なければ None）
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
