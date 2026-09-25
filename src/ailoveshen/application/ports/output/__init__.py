"""出力ポート（被駆動アダプターのインターフェース）。"""

from __future__ import annotations

from ailoveshen.application.ports.output.action_selector import IActionSelector
from ailoveshen.application.ports.output.audio_player import IAudioPlayer
from ailoveshen.application.ports.output.event_publisher import IEventPublisher, IEventSubscriber
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.mission_store import IMissionStore, SavedPlan
from ailoveshen.application.ports.output.prompt_builder import IPromptBuilder
from ailoveshen.application.ports.output.speech_synthesizer import ISpeechSynthesizer
from ailoveshen.application.ports.output.text_generator import ITextGenerator

__all__ = [
    "IActionSelector",
    "IAudioPlayer",
    "IEventPublisher",
    "IEventSubscriber",
    "IGamePromptBuilder",
    "IMinecraftBridge",
    "IMissionStore",
    "IPromptBuilder",
    "ISpeechSynthesizer",
    "ITextGenerator",
    "SavedPlan",
]
