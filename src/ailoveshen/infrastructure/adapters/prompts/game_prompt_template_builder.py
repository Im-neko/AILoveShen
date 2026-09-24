"""Game prompt template builder adapter."""

from __future__ import annotations

import json
from collections.abc import Sequence
from string import Template
from typing import Any

from ailoveshen.application.dto.game_dto import GoalOutcome
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    GameObservation,
    Goal,
    GoalPredicate,
    HouseBlueprint,
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
あなたは Minecraft のサバイバルで家を建てて暮らすAIエージェントの方針を決めます。
あなたが決めるのは「次に何を目標にするか」だけです。

## 目標の決め方
- 目標は下の「使える目標」の形で出す。達成したかはゲームの状態から自動で判定される
- 手順は自動で分解される（例: ベッドには羊毛3・板材3・作業台が要る、板材は原木から作る）。
  細かい操作（何を掘る・作る・どこへ行く）は別の高速なモデルが選ぶ
- 数分で終わる大きさの目標にする
- 夜は敵が湧いて危険。夕方になったら家に帰り、夜は家で過ごす（ベッドがあれば寝て夜を飛ばせる）
- 近くの敵への対処（逃げる・戦う）と空腹のときに食べるのは、選ばなくても行われる

## 使える目標
$predicates

## 建てる家
$blueprint

## 今の状況
$situation

## 今の目標
$current

## これまでの目標（古い順）
$recent_goals

## 目標を選び直す理由
$reason
$previous_error
## 出力
次の目標（predicate と必要な引数）と、その理由（短い1文）を指定の JSON で出力してください。
""")

PREDICATE_DESCRIPTIONS: dict[GoalPredicate, str] = {
    GoalPredicate.BUILT: "built: 設計図の家を完成させる（材料集めとクラフトも含めて進む）",
    GoalPredicate.HAVE: (
        "have(item, count): アイテムを count 個持つ。item はアイテム名かグループ"
        "（planks, log, door, bed, wool, food）。例: have(wooden_sword, 1)、have(food, 4)"
    ),
    GoalPredicate.PLACED: (
        "placed(item=bed): 家の中にベッドを置く（ベッドがなければ作るところから）"
    ),
    GoalPredicate.AT_HOME: "at_home: 家に入ってドアを閉める",
    GoalPredicate.THROUGH_NIGHT: "through_night: 家で夜を越す（ベッドがあれば寝る）",
    GoalPredicate.EXPLORED: (
        "explored(distance): 今いる場所から distance ブロック離れるまで探索する"
    ),
    GoalPredicate.CLEARED: (
        "cleared: ドアの近くで待ち構える敵を外に出て倒す（昼だけ。素手でも戦える。"
        "クリーパーは近くで爆発するので対象外）"
    ),
}

# Measured with spikes/primitive_choice_eval.py: stating needs in the state helped; a priority
# order in the instructions made the selector flee from mobs 20m away.
ACTION_INSTRUCTIONS = (
    "You control a Minecraft survival player working toward the goal in the state. "
    "Choose the single best next primitive action. Stay alive first; otherwise make progress on "
    "the goal."
)
MOBS_SHOWN = 8
RECENT_ACTIONS_SHOWN = 3


class GamePromptTemplateBuilder(IGamePromptBuilder):
    """
    Infrastructure adapter for game prompts.

    Implements IGamePromptBuilder with string templates. Prompts for the LLM
    are Japanese like the other prompts; the action selector gets English,
    which is what it was evaluated with.
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
        observation: GameObservation,
        current_goal: Goal | None,
        goal_ended_because: str,
        recent_goals: Sequence[GoalOutcome],
        predicates: Sequence[GoalPredicate],
        previous_error: str = "",
    ) -> str:
        """Build the prompt asking the LLM to set the next goal."""
        error = (
            f"\n## 前回の目標が使えなかった理由\n{previous_error}\n"
            "使える目標の形で、実行できる目標を選び直してください。\n"
            if previous_error
            else ""
        )
        return GOAL_TEMPLATE.substitute(
            predicates="\n".join(f"- {PREDICATE_DESCRIPTIONS[p]}" for p in predicates),
            blueprint=_format_blueprint(blueprint, observation),
            situation=_format_situation(observation),
            current=_format_current(current_goal, observation),
            recent_goals="\n".join(
                f"- {o.goal.spec.describe()}: {o.goal.reason}（終了: {o.ended_because}）"
                for o in recent_goals
            )
            or "なし",
            reason=goal_ended_because or "なし",
            previous_error=error,
        )

    def build_action_context(
        self, goal: Goal, observation: GameObservation
    ) -> tuple[dict[str, Any], str]:
        """Build the selector's state (goal, progress, needs, surroundings) and instructions."""
        s = observation.state
        status = observation.goal
        state = {
            "goal": goal.spec.describe(),
            "goal_reason": goal.reason,
            "progress": list(status.lines) if status else [],
            "blocked": list(status.blocked) if status else [],
            "needs": list(observation.needs),
            "self": {
                "health": observation.health,
                "food": observation.food,
                "time": _format_time_en(s.get("time", {})),
                "in_home": observation.inside_home,
                "held_item": s.get("self", {}).get("held_item"),
            },
            "inventory": s.get("inventory", {}),
            "nearby_mobs": [
                {k: m[k] for k in ("name", "hostile", "distance_m") if k in m}
                for m in s.get("mobs", [])[:MOBS_SHOWN]
            ],
            "recent_actions": s.get("recent_actions", [])[-RECENT_ACTIONS_SHOWN:],
        }
        return state, ACTION_INSTRUCTIONS


def _format_blueprint(b: HouseBlueprint, obs: GameObservation) -> str:
    counts = ", ".join(f"{k.value} {v}" for k, v in b.material_counts().items())
    size = f"{b.width}x{b.depth}、壁の高さ {b.wall_height}"
    build = obs.state.get("build")
    if obs.house_complete:
        progress = "完成済み"
    elif build:
        site = "建設地は決定済み" if build.get("origin") else "建設地は未決定"
        progress = f"{build['placed']}/{build['total']} ブロック設置済み、{site}"
    else:
        progress = "未着手"
    return f"「{b.name}」{size}（必要ブロック: {counts}）- {b.concept}\n進み具合: {progress}"


def _format_current(goal: Goal | None, obs: GameObservation) -> str:
    if goal is None or obs.goal is None:
        return "なし"
    lines = [f"{goal.spec.describe()}: {goal.reason}", *obs.goal.lines]
    if obs.goal.blocked:
        lines.append("進められない理由: " + "; ".join(obs.goal.blocked))
    return "\n".join(lines)


TICKS_PER_MINUTE = 20 * 60
DUSK_TICK = 12000
MORNING_TICK = 24000
PHASE_NAMES = {"day": "昼", "dusk": "夕方", "night": "夜", "dawn": "明け方"}


def _format_time(time: dict) -> str:
    phase = time.get("phase", "")
    name = PHASE_NAMES.get(phase, "不明")
    tick = time.get("time_of_day")
    if tick is None:
        return name
    if phase == "day":
        return f"{name}（日暮れまで約 {(DUSK_TICK - tick) / TICKS_PER_MINUTE:.0f} 分）"
    return f"{name}（朝まで約 {(MORNING_TICK - tick) / TICKS_PER_MINUTE:.0f} 分）"


def _format_time_en(time: dict) -> str:
    phase = time.get("phase", "")
    tick = time.get("time_of_day")
    if tick is None:
        return phase
    if phase == "day":
        return f"day ({(DUSK_TICK - tick) / TICKS_PER_MINUTE:.0f} minutes until dusk)"
    return f"{phase} ({(MORNING_TICK - tick) / TICKS_PER_MINUTE:.0f} minutes until morning)"


def _format_home(obs: GameObservation) -> str:
    if not obs.has_home:
        return "まだない（夜までに建てる必要がある）"
    where = "家の中にいる" if obs.inside_home else "完成している（外にいる）"
    bed = "ベッドあり" if obs.bed_in_home else "ベッドなし"
    return f"{where}、{bed}"


def _format_situation(obs: GameObservation) -> str:
    s = obs.state
    lines = [
        f"- 時間帯: {_format_time(s.get('time', {}))}",
        f"- 家: {_format_home(obs)}",
        f"- 体力 {obs.health}/20、満腹度 {obs.food}/20",
        f"- 持ち物: {json.dumps(s.get('inventory', {}), ensure_ascii=False)}",
        f"- 気をつけること: {'、'.join(n for n in obs.needs if n != 'none') or 'なし'}",
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
