#!/usr/bin/env python3
"""Integration test for the LLM pipeline with the real Gemini API.

Prerequisites:
1. GEMINI_API_KEY environment variable set
2. Dependencies installed: pip install "ailoveshen[llm]"
3. (--speak only) Style-Bert-VITS2 server running and "ailoveshen[tts]" installed

Usage:
    python examples/integration_test_llm.py
    python examples/integration_test_llm.py --speak   # also speak via TTS
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ailoveshen.domain.value_objects import EmotionState, EmotionType
from ailoveshen.factories.llm import create_llm_service
from ailoveshen.infrastructure.config import load_settings, load_yaml_file
from ailoveshen.infrastructure.events import AsyncEventBus

CONFIG_DIR = Path(__file__).parent.parent / "config"


async def run(speak: bool) -> bool:
    """Generate commentary and a chat reply, optionally speaking them."""
    settings = load_settings(config_dir=CONFIG_DIR)
    event_bus = AsyncEventBus()

    print("=" * 60)
    print(f"Model: {settings.gemini.main_model} "
          f"(thinking_level={settings.gemini.main_thinking_level})")
    print("=" * 60)

    llm = create_llm_service(
        gemini=settings.gemini,
        character=settings.character,
        event_publisher=event_bus,
    )

    tts = None
    if speak:
        from ailoveshen.factories.tts import create_and_connect_tts_service

        # create_tts_service takes the raw tts section (voice/synthesis/queue/audio)
        tts_config = load_yaml_file(CONFIG_DIR / "default.yaml").get("tts", {})
        tts = await create_and_connect_tts_service(
            config=tts_config,
            event_publisher=event_bus,
            get_current_emotion=llm.get_current_emotion,
        )

    ok = True
    try:
        steps = [
            ("Commentary", lambda: llm.generate_commentary(
                recent_events=["洞窟の入り口を見つけた"],
            )),
            ("Chat response", lambda: llm.generate_response(
                "neko", "がんばれー！何を探してるの？",
            )),
            ("Commentary (history)", lambda: llm.generate_commentary(
                recent_events=["ゾンビに遭遇", "ゾンビを倒した"],
                game_state_summary="体力: 14/20, 空腹度: 18/20, 手持ち: 石の剣",
            )),
        ]
        for i, (label, step) in enumerate(steps):
            if i == 2:
                llm.update_emotion(EmotionState(EmotionType.EXCITED, 0.8))
            started = time.monotonic()
            text = await step()
            elapsed = time.monotonic() - started
            print(f"\n[{label}] {elapsed:.2f}s")
            print(f"  {text or '(empty)'}")
            ok = ok and bool(text)
            if tts and text:
                await tts.speak(text, source=label)

        if tts:
            print("\nWaiting for speech to finish...")
            while tts.get_queue_size() > 0:
                await asyncio.sleep(0.5)
            await asyncio.sleep(5)
    finally:
        await llm.close()
        if tts:
            await tts.stop()

    print("\n" + ("All generations returned text" if ok else "Some generations were empty"))
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--speak", action="store_true", help="speak the results via TTS")
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(run(args.speak)) else 1)


if __name__ == "__main__":
    main()
