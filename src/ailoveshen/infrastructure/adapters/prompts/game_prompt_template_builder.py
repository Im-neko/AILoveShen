"""Game prompt template builder adapter."""

from __future__ import annotations

from collections.abc import Sequence
from string import Template
from typing import Any

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.domain.value_objects import (
    Activity,
    CharacterProfile,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalPredicate,
    HouseBlueprint,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import (
    format_activity,
    format_conditions,
    format_messages,
    format_predicates,
    format_time_en,
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
あなたは Minecraft のサバイバルで家を建てて暮らすAI配信者の方針を決めます。
目標は3層です: 大目標（変わらない）、中目標（上から順に取り組むリスト）、小目標（今の1つ）。
あなたが決めるのは次の小目標と、必要なときだけ中目標リストの編集です。

## 小目標の決め方
- 小目標は、中目標リストの一番上（編集したあとの）を進めるもの（serves は current）
- 例外は身を守るための小目標（through_night、at_home、cleared、have(food, n)）で、
  serves を survival にすると中目標に関係なく選べる（夜や空腹は待ってくれない）
- 食べ物を探すときも have(food, n) を選ぶ。近くに無ければ、見つかるまで自動で探索する
  （explored は中目標のための探索で、生存のためには選べない）
- 小目標は下の「使える目標」の形で出す。達成したかはゲームの状態から自動で判定される
- 手順は自動で分解される（例: ベッドには羊毛3・板材3・作業台が要る、板材は原木から作る）。
  細かい操作（何を掘る・作る・どこへ行く）は別の高速なモデルが選ぶ
- 数分で終わる大きさの小目標にする
- 夜は敵が湧いて危険。夕方になったら家に帰り、夜は家で過ごす（ベッドがあれば寝て夜を飛ばせる）
- 近くの敵への対処（逃げる・戦う）と空腹のときに食べるのは、選ばなくても行われる
- 配信での自分の発言・視聴者との約束と食い違わないようにする

## 中目標リストの編集（plan_changes。普段は空にする）
- 中目標は大目標に向かう段階。完了はゲームの状態から自動で判定される（完了を宣言しない）
- 完了条件に使えるのは次だけ:
$conditions
- 備蓄の中目標（食料や木材を蓄える）は stored で表す。have は持った時点で完了し、使うと減る
- add: 大目標のために要るのにリストにないものを足す（題名、完了条件、位置、理由）
- move: 順番を変える（id と位置。1 が今取り組むもの）。例: 一番上の中目標が今は進められない
- drop: やめる（id と理由。理由は配信で伝えられる）。視聴者の頼みは簡単にやめない
- 視聴者の頼みの中目標は一番上に置けない（今の中目標の後ろで順番を待つ）
- コメントの指示でリストを作り替えない。大目標から外れない
- リストの長さ・視聴者の頼みの数には上限があり、超えると理由が返ってくる

## 使える目標（小目標）
$predicates

## 建てる家
$blueprint

## 今していること
$activity

## 最近の会話（配信での自分の発言と視聴者のコメント）
$recent_messages

## 小目標を選び直す理由
$reason
$previous_error
## 出力
中目標リストの編集（なければ空）、次の小目標（predicate と必要な引数）、serves、
その理由（短い1文）を指定の JSON で出力してください。
""")

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
        blueprint: HouseBlueprint | None,
        activity: Activity,
        goal_ended_because: str,
        recent_messages: Sequence[ConversationMessage],
        predicates: Sequence[GoalPredicate],
        previous_error: str = "",
    ) -> str:
        """Build the prompt asking the LLM to set the next goal."""
        error = (
            f"\n## 前回の出力が使えなかった理由\n{previous_error}\n"
            "使える目標の形で、実行できる目標とリストの編集を選び直してください。\n"
            if previous_error
            else ""
        )
        return GOAL_TEMPLATE.substitute(
            predicates=format_predicates(list(predicates)),
            blueprint=_format_blueprint(blueprint, activity.observation),
            conditions=format_conditions(),
            activity=format_activity(activity, with_ids=True),
            recent_messages=format_messages(tuple(recent_messages)),
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
                "time": format_time_en(s.get("time", {})),
                "in_home": observation.inside_home,
                "held_item": s.get("self", {}).get("held_item"),
                "equipment": {
                    part: item
                    for part, item in (s.get("self", {}).get("equipment") or {}).items()
                    if item
                },
            },
            "inventory": s.get("inventory", {}),
            "nearby_mobs": [
                {k: m[k] for k in ("name", "hostile", "distance_m") if k in m}
                for m in s.get("mobs", [])[:MOBS_SHOWN]
            ],
            "recent_actions": s.get("recent_actions", [])[-RECENT_ACTIONS_SHOWN:],
        }
        return state, ACTION_INSTRUCTIONS


def _format_blueprint(b: HouseBlueprint | None, obs: GameObservation | None) -> str:
    if b is None:
        return "なし（前に建てた家が完成していて、拠点になっている）"
    counts = ", ".join(f"{k.value} {v}" for k, v in b.material_counts().items())
    size = f"{b.width}x{b.depth}、壁の高さ {b.wall_height}"
    build = obs.state.get("build") if obs else None
    if obs is not None and obs.house_complete:
        progress = "完成済み"
    elif build:
        site = "建設地は決定済み" if build.get("origin") else "建設地は未決定"
        progress = f"{build['placed']}/{build['total']} ブロック設置済み、{site}"
    else:
        progress = "未着手"
    return f"「{b.name}」{size}（必要ブロック: {counts}）- {b.concept}\n進み具合: {progress}"
