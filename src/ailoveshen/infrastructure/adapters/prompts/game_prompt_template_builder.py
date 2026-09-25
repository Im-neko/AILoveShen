"""テンプレートでゲームのプロンプトを組み立てるアダプター。"""

from __future__ import annotations

from collections.abc import Sequence
from string import Template
from typing import Any

from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.use_cases.goal_vocabulary import MAX_TOWN_STAGES
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
    TownSite,
    TownStage,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import (
    ABILITIES,
    format_activity,
    format_conditions,
    format_messages,
    format_predicates,
    format_site_choice,
    format_site_facts,
    format_time_en,
)

HOUSE_DESIGN_TEMPLATE = Template("""\
あなたは「$name」というAI配信者です。性格: $personality_traits
これから Minecraft のサバイバルで、自分の家を建てます。建てる家を設計してください。
$site_note
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
- 配信で完成まで見せられる大きさが良い
- あなたらしさが伝わる名前とコンセプトにする
$previous_error
## 出力
指定された JSON だけを出力してください。
""")

SITE_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者「$name」（性格: $personality_traits）です。
配信の大目標は「$mission」です。街を作る場所を決めるため、最初の家のまわりの候補地を
見て回り、地形を数えました。この中から街の場所を 1 か所選び、街の名前を付けてください。
場所は一度決めたら変えません。最初の家の場所以外を選ぶと、そこに家を建てて引っ越します
（最初の家は残ります）。

## 調べた候補地（数えた数字。候補地のまわり半径 32 ブロック）
$sites

## 数字の意味
- 家を建てられる平らな区画: 重ならない 9x9 の区画のうち、乾いた地面で高低差が 1 以内のもの。
  街には建物を何軒も置くので多いほど良い
- 水・急な段差: 地面のうちの割合。多いと建てられる所が減る（水は景色や将来の畑には良い）
- 地表の石・石炭・鉄: 空気に面していて、掘りに行けるもの。今は地面の下は掘り進めないので、
  石がないと石の道具・かまど・鉄の道具が作れない
- 原木: 木材（家、道具、松明の木炭）。動物: 食料と羊毛（ベッド）
- 溶岩: 危ない。家からの距離: 遠いほど引っ越しと行き来に時間がかかる
- 読み込めた範囲: 数えられた地面の割合（低いと数字が少なめに出ている）

## 選び方
- 数字で選ぶ。見た目の想像（「きれいそう」など）で選ばない
- 理由は配信でそのまま話す。どの数字が決め手かを 1〜2 文で言う
$previous_error
## 出力
指定の JSON で出力してください。
""")

TOWN_TEMPLATE = Template("""\
あなたは Minecraft のサバイバルで暮らす AI 配信者「$name」の方針を決めます。
配信の大目標は「$mission」です。この「街」とは何か、いつ完成したと言えるかを定義してください。
定義は一度決めたら変えず、配信の最後まで、この段階を順に進めます。

## 街の場所
$site

## 今の状況
- 家（木の家）が 1 軒ある。街の場所が最初の家の場所でなければ、そこに家を建てて引っ越す
  （段階はその家から始まる）。サバイバルで、夜は敵が湧く
- 視聴者はコメントで話しかけてくる。配信で完成まで見せられる規模にする

## 今できること
$abilities

## 今ゲームの状態から判定できる条件
$conditions

## 定義の決まり
- 段階（先に作るもの → 後で作るもの）に分ける。$max_stages 段階まで。前の段階が後の準備になるように
- 段階ごとの完了条件（conditions）は、上の判定できる条件だけで書く。1 段階 3 つまで
- 上の条件で書けないもの（2 軒目の建物、柵、道など）は unresolved に文で書き、何ができれば
  判定・実行できるかを添える。判定できるふりをしない（今の built() は最初の家のこと）
- 「にぎやか」「きれい」のような判定できない言葉は、数や配置に言い換える
- 手に入らない物（今できることで作れない物）を条件にしない
- 場所の数字に合わせる（例: 石が多いなら石を使う、動物が多いなら食料の備蓄から）
$previous_error
## 出力
指定の JSON で出力してください。
""")

STAGE_TEMPLATE = Template("""\
配信の大目標の「街」は、次のように定義してある:
$text

その段階「$title」（$why）には、まだ判定できない部分がある:
$unresolved

今は次の条件で判定できるようになった。この段階の意味を変えずに、書けるものを conditions に
書き直してください。まだ書けないものは unresolved に残してください（何ができれば書けるかを添えて）。
今の条件（すでにある conditions も含めて 3 つまで）:
$conditions

すでにある conditions: $current

書き直しの決まり:
- 意味を変えて書けるものに言い換えない
  （今の built() は最初の家のことで、2 軒目や倉庫の意味にはならない）
- 今もうそろっている条件にしない（まだできないことは、今そろってはいない）

## 今できること
$abilities
$previous_error
## 出力
指定の JSON で出力してください。
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
- drop: やめる（id と理由。理由は配信で伝えられる）。視聴者の頼みは簡単にやめない。
  街の段階の中目標はやめられない（今進められないなら move で後ろに回す）
- 街の段階は、前の段階が終わると自動で一番上に入る（自分で足さない）
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

# spikes/primitive_choice_eval.py で測った: 体が必要とするもの（needs）を状態に書くと効いた。
# 指示に優先順位を書くと、選択器は 20m 先の敵からも逃げるようになった。
ACTION_INSTRUCTIONS = (
    "You control a Minecraft survival player working toward the goal in the state. "
    "Choose the single best next primitive action. Stay alive first; otherwise make progress on "
    "the goal."
)
MOBS_SHOWN = 8
RECENT_ACTIONS_SHOWN = 3


class GamePromptTemplateBuilder(IGamePromptBuilder):
    """
    ゲームのプロンプトのインフラ側アダプター。

    IGamePromptBuilder を文字列のテンプレートで実装する。LLM へのプロンプトは
    他のプロンプトと同じく日本語。行動の選択器には英語を渡す（評価したときの言語）。
    """

    def build_house_design_prompt(
        self, character: CharacterProfile, site_note: str = "", previous_error: str = ""
    ) -> str:
        """LLM に小さな家を設計させるプロンプトを組み立てる。"""
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
            site_note=f"\n## 建てる場所\n{site_note}\n" if site_note else "",
            previous_error=error,
        )

    def build_site_prompt(
        self,
        character: CharacterProfile,
        mission: Mission,
        sites: Sequence[dict[str, Any]],
        previous_error: str = "",
    ) -> str:
        """調べた候補地の表から、街の場所を 1 か所選ばせるプロンプトを組み立てる。"""
        return SITE_TEMPLATE.substitute(
            name=character.name,
            personality_traits="、".join(character.personality_traits) or "特になし",
            mission=mission.text,
            sites="\n".join(f"- {s['id']}: {format_site_facts(s)}" for s in sites),
            previous_error=_retry(previous_error, "選び直してください。"),
        )

    def describe_site(self, site: TownSite, facts: dict[str, Any]) -> str:
        """選んだ街の場所の説明（決めたことと、そこの数字）。"""
        lines = [f"街「{site.name}」の場所。{format_site_choice(site)}"]
        if facts:
            lines.append(f"そこの地形: {format_site_facts(facts)}")
        return "\n".join(lines)

    def build_town_prompt(
        self,
        character: CharacterProfile,
        mission: Mission,
        site: TownSite,
        facts: dict[str, Any],
        previous_error: str = "",
    ) -> str:
        """大目標の街とは何かを、段階に分けて答えさせるプロンプトを組み立てる。"""
        return TOWN_TEMPLATE.substitute(
            name=character.name,
            mission=mission.text,
            site=self.describe_site(site, facts),
            abilities=ABILITIES,
            conditions=format_conditions(),
            max_stages=MAX_TOWN_STAGES,
            previous_error=_retry(previous_error, "定義し直してください。"),
        )

    def build_stage_prompt(
        self, town: TownDefinition, stage: TownStage, previous_error: str = ""
    ) -> str:
        """段階のまだ判定できない部分を、今の条件で書き直させるプロンプトを組み立てる。"""
        return STAGE_TEMPLATE.substitute(
            text=town.text,
            title=stage.title,
            why=stage.why,
            unresolved="\n".join(f"- {u}" for u in stage.unresolved),
            conditions=format_conditions(),
            current=", ".join(c.describe() for c in stage.conditions) or "なし",
            abilities=ABILITIES,
            previous_error=_retry(previous_error, "書き直してください。"),
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
        """LLM に次の目標を決めさせるプロンプトを組み立てる。"""
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
        """選択器に渡す状態（目標、進み具合、体が必要とするもの、周り）と指示を組み立てる。"""
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


def _retry(previous_error: str, ask: str) -> str:
    return f"\n## 前回の答えが使えなかった理由\n{previous_error}\n{ask}\n" if previous_error else ""
