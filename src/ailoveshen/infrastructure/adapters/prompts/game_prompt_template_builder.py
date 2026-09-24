"""Game prompt template builder adapter."""

from __future__ import annotations

import json
from collections.abc import Sequence
from string import Template

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.domain.value_objects import (
    GOAL_ACTIONS,
    CharacterProfile,
    GameObservation,
    Goal,
    GoalType,
    HouseBlueprint,
    MaterialNeeds,
)

HOUSE_DESIGN_TEMPLATE = Template("""\
あなたは「$name」というAI配信者です。性格: $personality_traits
これから Minecraft のサバイバルで、自分の最初の家を建てます。建てる家を設計してください。

## 建てられる家の条件
- 1部屋の四角い家。壁と平らな屋根は木の板材でできる
- 幅（x方向）と奥行き（z方向）は $min_side〜$max_side ブロック
- 壁の高さは $min_height〜$max_height ブロック
- ドアは1つ。door_side の壁の door_offset の位置に置く
  （offset は壁の端から数えた位置で、角は選べない）
- 窓は壁に空ける1ブロックの穴（ガラスはない）。最大4つ。ドアと同じ位置には置けない
- corner_pillars を true にすると四隅の柱が原木になる
  （見た目のアクセント。原木が4本×壁の高さぶん余分に必要）
- 材料はすべて自分で木を切って集める。大きい家ほど時間がかかる

## ヒント
- 最初の家なので、配信で完成まで見せられる大きさが良い
- あなたらしさが伝わる名前とコンセプトにする
$previous_error
## 出力
指定された JSON だけを出力してください。
""")

GOAL_TEMPLATE = Template("""\
あなたは Minecraft で家を建てているAIエージェントの方針を決めます。
細かい操作は別の高速なモデルが担当するので、あなたは「今どのゴールに取り組むか」だけを選びます。

## 建てる家
$blueprint

## 建築の進み具合
$build

## まだ足りないもの
$needs

## 今の状況
$situation

## これまでのゴール（古い順）
$recent_goals

## ゴールを選び直す理由
$reason

## 今選べるゴール
$goals

## 出力
次に取り組むゴールと、その理由（短い1文）を指定の JSON で出力してください。
""")

GOAL_DESCRIPTIONS: dict[GoalType, str] = {
    GoalType.GATHER_WOOD: "木を切って原木を集める（落ちた原木も拾う）",
    GoalType.CRAFT: "原木を板材にし、作業台とドアを作る",
    GoalType.BUILD_SHELTER: "設計図どおりにブロックを置く（手元の材料の分だけ進む）",
    GoalType.EXPLORE: "周辺を歩き回って、木や平らな建設地を探す",
}

ACTION_INSTRUCTIONS = Template("""\
You control a Minecraft survival player who is building a small house ("$house", $width x $depth, \
walls $height high). Current goal: $goal - $goal_description. Reason: $reason. \
Choose the single best next action. \
Priorities: 1) stay alive, 2) make progress on the current goal.""")

GOAL_DESCRIPTIONS_EN: dict[GoalType, str] = {
    GoalType.GATHER_WOOD: "chop trees and pick up logs",
    GoalType.CRAFT: "turn logs into planks and craft a crafting table and a door",
    GoalType.BUILD_SHELTER: "place the house blocks",
    GoalType.EXPLORE: "look around for trees and flat land",
}


class GamePromptTemplateBuilder(IGamePromptBuilder):
    """
    Infrastructure adapter for game prompts.

    Implements IGamePromptBuilder with string templates. Prompts for the LLM
    are Japanese like the other prompts; instructions for the action selector
    are English, which is what it was evaluated with.
    """

    def build_house_design_prompt(
        self, character: CharacterProfile, previous_error: str = ""
    ) -> str:
        """Build the prompt asking the LLM to design a small house."""
        error = (
            f"\n## 前回の設計が使えなかった理由\n{previous_error}\n"
            "条件を守って設計し直してください。\n"
            if previous_error
            else ""
        )
        return HOUSE_DESIGN_TEMPLATE.substitute(
            name=character.name,
            personality_traits="、".join(character.personality_traits) or "特になし",
            min_side=HouseBlueprint.MIN_SIDE,
            max_side=HouseBlueprint.MAX_SIDE,
            min_height=HouseBlueprint.MIN_WALL_HEIGHT,
            max_height=HouseBlueprint.MAX_WALL_HEIGHT,
            previous_error=error,
        )

    def build_goal_prompt(
        self,
        blueprint: HouseBlueprint,
        needs: MaterialNeeds,
        observation: GameObservation,
        current_goal: Goal | None,
        goal_ended_because: str,
        recent_goals: Sequence[Goal],
    ) -> str:
        """Build the prompt asking the LLM to choose the next goal."""
        available = {a.action_id for a in observation.actions}
        goals = "\n".join(
            f"- {g.value}: {GOAL_DESCRIPTIONS[g]}" for g in GoalType if GOAL_ACTIONS[g] & available
        )
        return GOAL_TEMPLATE.substitute(
            blueprint=_format_blueprint(blueprint),
            build=_format_build(observation),
            needs=_format_needs(needs),
            situation=_format_situation(observation),
            recent_goals="\n".join(f"- {g.goal_type.value}: {g.reason}" for g in recent_goals)
            or "なし",
            reason=goal_ended_because or "なし",
            goals=goals,
        )

    def build_action_instructions(self, goal: Goal, blueprint: HouseBlueprint) -> str:
        """Build the instructions given to the action selector for the current goal."""
        return ACTION_INSTRUCTIONS.substitute(
            house=blueprint.name,
            width=blueprint.width,
            depth=blueprint.depth,
            height=blueprint.wall_height,
            goal=goal.goal_type.value,
            goal_description=GOAL_DESCRIPTIONS_EN[goal.goal_type],
            reason=goal.reason or "none given",
        )


def _format_blueprint(b: HouseBlueprint) -> str:
    counts = ", ".join(f"{k.value} {v}" for k, v in b.material_counts().items())
    size = f"{b.width}x{b.depth}、壁の高さ {b.wall_height}"
    return f"「{b.name}」{size}（必要ブロック: {counts}）- {b.concept}"


def _format_build(obs: GameObservation) -> str:
    if obs.build is None:
        return "未着手"
    site = (
        "建設地は決定済み"
        if obs.build.site_chosen
        else "建設地は未決定（建築を始めると近くの平らな場所を探す）"
    )
    return f"{obs.build.placed}/{obs.build.total} ブロック設置済み。{site}"


def _format_needs(n: MaterialNeeds) -> str:
    lines = [
        f"- 原木があと {n.logs_short} 本",
        f"- 板材があと {n.planks_short} 枚（手持ちの原木を板材にすればその分減る）",
    ]
    if n.door_needed:
        lines.append("- ドア（板材6枚と作業台が必要）")
    if n.table_needed:
        lines.append("- 作業台（板材4枚）")
    return "\n".join(lines)


def _format_log(state: dict) -> str:
    log = state.get("resources", {}).get("nearest_reachable_log")
    return f"あり（{log['distance_m']}m 先）" if log else "なし（探しに行く必要がある）"


def _format_situation(obs: GameObservation) -> str:
    s = obs.state
    threats = [m for m in s.get("mobs", []) if m.get("hostile") and m.get("visible")]
    lines = [
        f"- 時間帯: {s.get('time', {}).get('phase', '不明')}",
        f"- 体力 {obs.health}/20、満腹度 {obs.food}/20",
        f"- 持ち物: {json.dumps(obs.inventory, ensure_ascii=False)}",
        f"- 見えている敵: {len(threats)} 体",
        f"- 作業台が近くにある: {'はい' if obs.crafting_table_nearby else 'いいえ'}",
        f"- 近くに切れる木: {_format_log(s)}",
    ]
    recent = s.get("recent_actions", [])
    if recent:
        lines.append(
            "- 直近の行動: "
            + " / ".join(
                f"{a['action']}=成功" if a["ok"] else f"{a['action']}=失敗（{a.get('result', '')}）"
                for a in recent
            )
        )
    return "\n".join(lines)
