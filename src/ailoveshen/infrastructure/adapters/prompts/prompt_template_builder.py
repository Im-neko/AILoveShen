"""Prompt template builder adapter."""

from __future__ import annotations

from string import Template

from ailoveshen.application.ports.output.prompt_builder import IPromptBuilder
from ailoveshen.domain.value_objects import (
    CharacterProfile,
    ConversationMessage,
    EmotionState,
    GenerationContext,
    MessageRole,
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
## 現在のゲーム状況
$game_state

## 最近のイベント
$recent_events

## 最近の会話
$recent_messages

## あなたの感情状態
$emotion

## タスク
上記の状況を踏まえて、配信者として自然な実況・独り言・考えを1-2文で述べてください。
- ゲームの状況に即した内容
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

## 最近の会話
$recent_messages

## あなたの感情状態
$emotion

## タスク
このコメントに対して、配信者として自然に返答してください。
- 視聴者の名前を呼んで親しみを込める
- 短く簡潔に（1-2文）
- キャラクターらしい話し方

## 出力
返答テキストのみを出力してください。
""")

NO_INFORMATION = "特になし"


class PromptTemplateBuilder(IPromptBuilder):
    """
    Infrastructure adapter for prompt building.

    Implements IPromptBuilder output port with string templates.
    Prompts are model-independent, so this adapter is shared by all LLMs.
    """

    def build_system_prompt(self, character: CharacterProfile) -> str:
        """Build the system instruction describing the character."""
        return CHARACTER_SYSTEM_TEMPLATE.substitute(
            name=character.name,
            description=character.description.strip(),
            personality_traits="、".join(character.personality_traits) or NO_INFORMATION,
            speech_style=character.speech_style.strip(),
            first_person=character.first_person,
            sentence_endings="、".join(character.sentence_endings),
        )

    def build_commentary_prompt(self, context: GenerationContext) -> str:
        """Build the prompt for game commentary."""
        return COMMENTARY_TEMPLATE.substitute(
            game_state=context.game_state_summary or "不明",
            recent_events=_format_events(context.recent_events),
            recent_messages=_format_messages(context.recent_messages),
            emotion=_format_emotion(context.emotion_state),
        )

    def build_chat_response_prompt(
        self,
        user_name: str,
        message: str,
        context: GenerationContext,
    ) -> str:
        """Build the prompt for replying to a viewer's chat."""
        return CHAT_RESPONSE_TEMPLATE.substitute(
            user_name=user_name,
            message=message,
            recent_messages=_format_messages(context.recent_messages),
            emotion=_format_emotion(context.emotion_state),
        )


def _format_events(events: tuple[str, ...]) -> str:
    """Format recent game events as a bullet list."""
    if not events:
        return NO_INFORMATION
    return "\n".join(f"- {event}" for event in events)


def _format_messages(messages: tuple[ConversationMessage, ...]) -> str:
    """Format conversation history, one message per line."""
    if not messages:
        return NO_INFORMATION
    lines = []
    for message in messages:
        speaker = message.speaker_name if message.role == MessageRole.VIEWER else "あなた"
        lines.append(f"{speaker}: {message.content}")
    return "\n".join(lines)


def _format_emotion(emotion: EmotionState) -> str:
    """Format emotion state for the prompt."""
    return f"{emotion.primary.value}（強度: {emotion.intensity:.1f}）"
