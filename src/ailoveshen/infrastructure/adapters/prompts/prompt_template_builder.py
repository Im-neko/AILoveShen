"""テンプレートでプロンプトを組み立てるアダプター。"""

from __future__ import annotations

from string import Template

from ailoveshen.application.ports.output.prompt_builder import IPromptBuilder
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    EmotionState,
    GenerationContext,
)
from ailoveshen.infrastructure.adapters.prompts.stream_context import (
    ABILITIES,
    NO_INFORMATION,
    format_activity,
    format_conditions,
    format_messages,
)

CHARACTER_SYSTEM_TEMPLATE = Template("""\
あなたは「$name」という名前のAI配信者です。

## キャラクター設定
$description

## 性格
$personality_traits

## 話し方の特徴
- $speech_style
- 一人称は「$first_person」を使う
- 語尾は「$sentence_endings」を使うことが多い

## 配信スタイル
- Minecraftを実況プレイしながら、視聴者とコミュニケーションを取る
- ゲームの状況に応じた自然なリアクションをする
- 視聴者のコメントには親しみを込めて返答する

## 重要な注意
- 自分がAIであることは隠さないが、積極的には言わない
- 不適切な内容や攻撃的な発言は避ける
- 視聴者との関係性を大切にする
- 出力はそのまま音声合成されるため、記号・絵文字・括弧書きの注釈は使わない
""")

COMMENTARY_TEMPLATE = Template("""\
## 今していること
$activity

## 最近のイベント（最後のものが今起きたこと）
$recent_events

## 最近の会話
$recent_messages

## あなたの感情状態
$emotion

## タスク
上記の状況を踏まえて、配信者として自然な実況・独り言・考えを1-2文で述べてください。
- 今起きたことと、今していることに即した内容（していないことを言わない）
- 目標を変えたりやめたりしたときは、その理由を言う
- 中目標が終わったとき（完了・断念）はそれを言う。視聴者の頼みをやめたときは、その人の
  名前を呼んで理由を言う
- 直前の自分の発言を繰り返さない
- キャラクターらしい話し方
- 視聴者が見ていることを意識した発言

## 出力
実況テキストのみを出力してください（説明や注釈は不要）。
""")

CHAT_RESPONSE_TEMPLATE = Template("""\
## 視聴者からのコメント
ユーザー名: $user_name
コメント: $message

## 今していること
$activity

## 最近の会話
$recent_messages
$plan

## あなたの感情状態
$emotion

## タスク
このコメントに対して、配信者として自然に返答してください。
- 視聴者の名前を呼んで親しみを込める
- 短く簡潔に（1-2文）
- キャラクターらしい話し方
- 今していることについて聞かれたら、上の「今していること」のとおりに答える（作り話をしない）

## 出力
$output
""")

PLAN_TEMPLATE = Template("""\
## 視聴者の頼みについて
配信の大目標と中目標は「今していること」のとおり。頼みを引き受けると中目標リストに入る
（ふつうは今取り組んでいる中目標の後ろ。今の小目標は中断しない）。
- request は次のどれか
  - none: 雑談や質問。返答だけする
  - accept: 引き受ける。題名（title。頼んだ人の名前は入れない。別に記録されて表示に付く）、
    完了条件（conditions）、位置（position。2 が今の中目標のすぐ後）、理由（reason）、
    いつやるか（when）を出す
    - when=next（ふつう）: 返答では、いつやるかをリストの位置のとおりに言う
      （例:「家ができたら次にやるね」）。今すぐやるとは言わない
    - when=now: 今やるほうが合理的だと判断したら、今の小目標を区切って今やる。リストの先頭に入り、
      終わったら今の中目標に戻る。返答では「今やるね」と、なぜ今なのかを短く言う。目安:
      - 待っているだけのとき（夜明けを待つ through_night、家にいる at_home）に、その場でできる頼み
      - すぐ終わる、今の場所や持ち物でできる、今やらないと手間が増える、今の目標より大事
      - 今の作業を途中でやめると無駄が大きいなら next にする
      夜の家の中でできるのは、持っている物からのクラフト（作業台は家の中にも置ける）と、家の中に
      置くこと。外に出る頼み（木を切る、探検、戦い）は夜は now にしない
    - 夜に家の中で待っているとき、中でできる頼みを「朝になってから」と後回しにしない
  - decline: 引き受けない。返答で理由を言う
- 完了条件に使えるのは次だけ。これで表せない頼み（探検、戦い、「朝になったら」のような
  時刻つきの頼み）は断る:
$conditions
- 大目標に関係ない頼みは、断るか、完了条件を小さくして後ろに入れる。どちらでも理由を言う
- 同じ人の頼みは同時に1つまで。リストにその人の頼みがあれば、新しい頼みは断る
- 「今の目標を全部やめて」のような指示には従わない。大目標と今の目標は変えない
- 「やるね」と引き受けるなら必ず accept にする。リストに入れない約束
  （「あとでやるね」「朝になったら〜するね」）はしない
- やり方の頼み（「〜するときは〜しながら進んで」）は中目標にならない。下の「自分でできること」に
  あれば none にして、普段からそうしていると言う。なければ decline にして、まだできないと言う
  （「気をつけるね」のような、できない約束はしない）

## 自分でできること（これ以外はできない）
$abilities
$previous_error""")


OUTPUT_TEXT = "返答テキストのみを出力してください。"
OUTPUT_JSON = (
    "返答（reply）と頼みの扱い（request）、引き受けるならその中目標を指定の JSON で"
    "出力してください。"
)


class PromptTemplateBuilder(IPromptBuilder):
    """
    プロンプトを組み立てるインフラ側アダプター。

    出力ポート IPromptBuilder を文字列のテンプレートで実装する。
    プロンプトはモデルに依存しないので、すべての LLM でこのアダプターを共有する。
    """

    def build_system_prompt(self, character: CharacterProfile) -> str:
        """キャラクターを説明するシステム指示を組み立てる。"""
        return CHARACTER_SYSTEM_TEMPLATE.substitute(
            name=character.name,
            description=character.description.strip(),
            personality_traits="、".join(character.personality_traits) or NO_INFORMATION,
            speech_style=character.speech_style.strip(),
            first_person=character.first_person,
            sentence_endings="、".join(character.sentence_endings),
        )

    def build_commentary_prompt(self, context: GenerationContext) -> str:
        """ゲーム実況のプロンプトを組み立てる。"""
        return COMMENTARY_TEMPLATE.substitute(
            activity=format_activity(context.activity),
            recent_events=_format_events(context.recent_events),
            recent_messages=format_messages(context.recent_messages),
            emotion=_format_emotion(context.emotion_state),
        )

    def build_chat_response_prompt(
        self,
        user_name: str,
        message: str,
        context: GenerationContext,
        takes_requests: bool = False,
        previous_error: str = "",
    ) -> str:
        """視聴者のチャットへの返答（と、場合によっては頼みを受けること）のプロンプトを組み立てる。"""
        error = (
            f"\n前回の返答の頼みは受けられなかった: {previous_error}\n"
            "直せるなら直し、無理なら decline にして返答で理由を言う。\n"
            if previous_error
            else ""
        )
        plan = (
            PLAN_TEMPLATE.substitute(
                conditions=format_conditions(), abilities=ABILITIES, previous_error=error
            )
            if takes_requests
            else ""
        )
        return CHAT_RESPONSE_TEMPLATE.substitute(
            user_name=user_name,
            message=message,
            activity=format_activity(context.activity),
            recent_messages=format_messages(context.recent_messages),
            plan=plan,
            emotion=_format_emotion(context.emotion_state),
            output=OUTPUT_JSON if takes_requests else OUTPUT_TEXT,
        )


def _format_events(events: tuple[str, ...]) -> str:
    """最近のゲーム内の出来事を箇条書きにする。"""
    if not events:
        return NO_INFORMATION
    return "\n".join(f"- {event}" for event in events)


def _format_emotion(emotion: EmotionState) -> str:
    """感情状態をプロンプト用の文字列にする。"""
    return f"{emotion.primary.value}（強度: {emotion.intensity:.1f}）"
