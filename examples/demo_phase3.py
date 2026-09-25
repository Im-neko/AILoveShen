#!/usr/bin/env python3
"""
Phase 3 デモ: LLM の会話

Phase 3 で実装した LLM の会話パイプラインを動かす。
見せるもの:
- ドメイン層: Conversation、ConversationMessage、CharacterProfile、イベント
- アプリケーション層: GenerateCommentaryUseCase、GenerateResponseUseCase
- インフラ層: PromptTemplateBuilder（本物）、テキスト生成（偽物）
- プレゼンテーション層: LLMService

注: テキスト生成は偽物を使うので、Gemini の API キーは要らない。
本物の Gemini API は examples/integration_test_llm.py を使う。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.generate_commentary import GenerateCommentaryUseCase
from ailoveshen.application.use_cases.generate_response import GenerateResponseUseCase
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import ChatResponseGeneratedEvent, CommentaryGeneratedEvent
from ailoveshen.domain.value_objects import CharacterProfile, EmotionState, EmotionType
from ailoveshen.infrastructure.adapters.prompts.prompt_template_builder import (
    PromptTemplateBuilder,
)
from ailoveshen.infrastructure.events import AsyncEventBus
from ailoveshen.presentation.services.llm_service import LLMService


def print_header(title: str) -> None:
    """整形したセクション見出しを表示する。"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


class FakeTextGenerator(ITextGenerator):
    """決まった返事を返し、最後のプロンプトを覚えておくテキスト生成。"""

    def __init__(self) -> None:
        self.last_prompt = ""
        self.last_system_instruction: Optional[str] = None
        self._replies = iter(
            [
                "わー、洞窟の入り口を見つけたよ！ちょっと覗いてみようかな！",
                "nekoさん、応援ありがとう！がんばって奥まで行ってみるね！",
                "うわっ、ゾンビだ！でも倒せたよ、やったね！",
            ]
        )

    async def generate(
        self, prompt: str, system_instruction: Optional[str] = None, purpose: Optional[str] = None
    ) -> str:
        self.last_prompt = prompt
        self.last_system_instruction = system_instruction
        return next(self._replies, "")

    async def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_instruction: Optional[str] = None,
        purpose: Optional[str] = None,
        images=(),
    ) -> dict[str, Any]:
        raise NotImplementedError("会話のデモはプレーンテキストしか使わない")

    async def choose_tool(self, prompt, tools, system_instruction=None, purpose=None, images=()):
        raise NotImplementedError("会話のデモは道具を使わない")

    async def close(self) -> None:
        pass


async def main() -> None:
    """デモを実行する。"""
    print("\n" + "=" * 60)
    print("  AILoveShen Phase 3: LLM の会話のデモ")
    print("=" * 60)

    event_bus = AsyncEventBus()
    events: list[str] = []

    async def on_commentary(event: CommentaryGeneratedEvent) -> None:
        events.append(f"CommentaryGeneratedEvent: {event.text}")

    async def on_response(event: ChatResponseGeneratedEvent) -> None:
        events.append(f"ChatResponseGeneratedEvent: {event.user_name} <- {event.text}")

    event_bus.subscribe(CommentaryGeneratedEvent, on_commentary)
    event_bus.subscribe(ChatResponseGeneratedEvent, on_response)

    # factories/llm.py と同じように組み立てる（テキスト生成だけ偽物）
    generator = FakeTextGenerator()
    prompt_builder = PromptTemplateBuilder()
    conversation = Conversation()
    character = CharacterProfile()
    service = LLMService(
        generate_commentary_use_case=GenerateCommentaryUseCase(
            generator, prompt_builder, event_bus, conversation, character
        ),
        generate_response_use_case=GenerateResponseUseCase(
            generator, prompt_builder, event_bus, conversation, character
        ),
        text_generator=generator,
    )

    print_header("実況（メインループ）")
    service.update_emotion(EmotionState(EmotionType.EXCITED, 0.8))
    text = await service.generate_commentary(recent_events=["洞窟を見つけた"])
    print(f"実況: {text}")

    print_header("チャットへの返事（サブループ）")
    text = await service.generate_response("neko", "がんばれー！", user_id="42")
    print(f"返事: {text}")

    print_header("履歴のある実況")
    service.update_emotion(EmotionState(EmotionType.SCARED, 0.6))
    text = await service.generate_commentary(recent_events=["ゾンビに遭遇", "ゾンビを倒した"])
    print(f"実況: {text}")
    print("\nモデルに送ったプロンプト:")
    print(generator.last_prompt)

    print_header("システム指示")
    print(generator.last_system_instruction)

    await asyncio.sleep(0.1)
    print_header("受け取ったイベント")
    for event in events:
        print(f"  - {event}")

    print_header("会話の履歴")
    for message in conversation:
        speaker = message.speaker_name or "配信者"
        print(f"  [{message.message_type.value}] {speaker}: {message.content}")

    await service.close()
    print_header("デモ完了")


if __name__ == "__main__":
    asyncio.run(main())
