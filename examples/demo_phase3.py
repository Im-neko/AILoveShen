#!/usr/bin/env python3
"""
Phase 3 Demo: LLM Conversation

This demo showcases the LLM conversation pipeline implemented in Phase 3.
It demonstrates:
- Domain layer: Conversation, ConversationMessage, CharacterProfile, events
- Application layer: GenerateCommentaryUseCase, GenerateResponseUseCase
- Infrastructure layer: PromptTemplateBuilder (real), text generator (fake)
- Presentation layer: LLMService

Note: This demo uses a fake text generator and needs no Gemini API key.
For the real Gemini API, see examples/integration_test_llm.py.
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
    """Print a formatted section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


class FakeTextGenerator(ITextGenerator):
    """Text generator that returns canned replies and remembers the last prompt."""

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

    async def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        self.last_prompt = prompt
        self.last_system_instruction = system_instruction
        return next(self._replies, "")

    async def generate_json(
        self, prompt: str, schema: dict[str, Any], system_instruction: Optional[str] = None
    ) -> dict[str, Any]:
        raise NotImplementedError("the conversation demo uses plain text only")

    async def close(self) -> None:
        pass


async def main() -> None:
    """Run the demo."""
    print("\n" + "=" * 60)
    print("  AILoveShen Phase 3: LLM Conversation Demo")
    print("=" * 60)

    event_bus = AsyncEventBus()
    events: list[str] = []

    async def on_commentary(event: CommentaryGeneratedEvent) -> None:
        events.append(f"CommentaryGeneratedEvent: {event.text}")

    async def on_response(event: ChatResponseGeneratedEvent) -> None:
        events.append(f"ChatResponseGeneratedEvent: {event.user_name} <- {event.text}")

    event_bus.subscribe(CommentaryGeneratedEvent, on_commentary)
    event_bus.subscribe(ChatResponseGeneratedEvent, on_response)

    # Wire the same way as factories/llm.py, but with a fake generator
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

    print_header("Commentary (Main Loop)")
    service.update_emotion(EmotionState(EmotionType.EXCITED, 0.8))
    text = await service.generate_commentary(recent_events=["洞窟を見つけた"])
    print(f"Commentary: {text}")

    print_header("Chat Response (Sub Loop)")
    text = await service.generate_response("neko", "がんばれー！", user_id="42")
    print(f"Response: {text}")

    print_header("Commentary with History")
    service.update_emotion(EmotionState(EmotionType.SCARED, 0.6))
    text = await service.generate_commentary(recent_events=["ゾンビに遭遇", "ゾンビを倒した"])
    print(f"Commentary: {text}")
    print("\nPrompt sent to the model:")
    print(generator.last_prompt)

    print_header("System Instruction")
    print(generator.last_system_instruction)

    await asyncio.sleep(0.1)
    print_header("Events Received")
    for event in events:
        print(f"  - {event}")

    print_header("Conversation History")
    for message in conversation:
        speaker = message.speaker_name or "streamer"
        print(f"  [{message.message_type.value}] {speaker}: {message.content}")

    await service.close()
    print_header("Demo Complete")


if __name__ == "__main__":
    asyncio.run(main())
