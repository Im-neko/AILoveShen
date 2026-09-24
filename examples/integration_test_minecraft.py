#!/usr/bin/env python3
"""Integration test: Gemini designs a house and sets goals, Jev plays, the bridge builds it.

Nothing is given to the bot: it gathers wood, crafts and builds on its own.

Prerequisites:
1. Minecraft server running (docker/docker-compose.minecraft.yml)
2. Bridge running: cd minecraft-bridge && npm install && npm start
   (watch the bot's view: connect a 1.21.4 client to 127.0.0.1:25578)
3. GEMINI_API_KEY and TYPESAFE_API_KEY environment variables set
4. Dependencies installed: pip install "ailoveshen[llm,game]"

Usage:
    python examples/integration_test_minecraft.py [--max-steps 300]
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ailoveshen.domain.events import GoalSetEvent, HouseCompletedEvent, HouseDesignedEvent
from ailoveshen.factories.game import create_game_service
from ailoveshen.infrastructure.config import load_settings
from ailoveshen.infrastructure.events import AsyncEventBus

CONFIG_DIR = Path(__file__).parent.parent / "config"


async def run(max_steps: int) -> bool:
    """Build a house and report whether every block is in place."""
    settings = load_settings(config_dir=CONFIG_DIR)
    event_bus = AsyncEventBus()

    async def on_designed(event: HouseDesignedEvent) -> None:
        print(f"[design] {event.name}: {event.concept}", flush=True)

    async def on_goal(event: GoalSetEvent) -> None:
        print(f"[goal] {event.goal_type}: {event.reason}", flush=True)

    async def on_completed(event: HouseCompletedEvent) -> None:
        print(f"[done] {event.name} is complete", flush=True)

    event_bus.subscribe(HouseDesignedEvent, on_designed)
    event_bus.subscribe(GoalSetEvent, on_goal)
    event_bus.subscribe(HouseCompletedEvent, on_completed)

    game = create_game_service(
        gemini=settings.gemini,
        jev=settings.jev,
        minecraft=settings.minecraft,
        character=settings.character,
        event_publisher=event_bus,
    )
    try:
        outcome = await game.build_house(max_steps=max_steps)
    finally:
        await game.close()

    b = outcome.project.blueprint
    print("=" * 60)
    print(f"House: {b.name} {b.width}x{b.depth}x{b.wall_height} ({len(b.blocks())} blocks)")
    print(f"Complete: {outcome.complete} after {outcome.steps} steps")
    return outcome.complete


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-steps", type=int, default=300)
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(run(args.max_steps)) else 1)


if __name__ == "__main__":
    main()
