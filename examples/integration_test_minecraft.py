#!/usr/bin/env python3
"""Integration test: Gemini designs a house and sets goals, Jev picks actions, the bridge plays.

Nothing is given to the bot: it gathers wood, crafts, builds and gets through
the night on its own. Goals are judged by the bridge from the world.

The streamer talks too: goal changes are narrated, and viewers' comments from
a script (--comments) are answered while playing; a reply that takes a
request becomes the next goal. What is said is printed ([say], [reply]).

Prerequisites:
1. Minecraft server running (docker/docker-compose.minecraft.yml)
2. Bridge running: cd minecraft-bridge && npm install && npm start
   (watch the bot's view: connect a 1.21.4 client to 127.0.0.1:25578)
3. GEMINI_API_KEY and TYPESAFE_API_KEY environment variables set
4. Dependencies installed: pip install "ailoveshen[llm,game]"

Usage:
    python examples/integration_test_minecraft.py [--max-steps 300] [--comments comments.json]

comments.json: [{"after_seconds": 60, "user": "neko", "message": "ベッド作って！"}, ...]
(seconds from the start of play)
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.events import GoalSetEvent, HouseCompletedEvent, HouseDesignedEvent
from ailoveshen.factories.game import create_game_service
from ailoveshen.factories.llm import create_llm_service
from ailoveshen.infrastructure.config import load_settings
from ailoveshen.infrastructure.events import AsyncEventBus
from ailoveshen.presentation.services import GameService, LLMService, Narrator

CONFIG_DIR = Path(__file__).parent.parent / "config"


async def feed_comments(comments: list[dict], game: GameService, llm: LLMService) -> None:
    """Send the scripted comments at their times and print the replies."""
    started = time.monotonic()
    for c in sorted(comments, key=lambda c: c["after_seconds"]):
        await asyncio.sleep(max(0.0, c["after_seconds"] - (time.monotonic() - started)))
        session = game.session
        goal_before = session.goal.spec.describe() if session and session.goal else "-"
        print(f"[chat] {c['user']}: {c['message']} (goal now: {goal_before})", flush=True)
        t = time.monotonic()
        reply = await llm.generate_response(c["user"], c["message"], session=session)
        request = session.request if session else None
        taken = (
            f" -> request {request.goal.spec.describe()}"
            if request and request.user_name == c["user"]
            else ""
        )
        print(f"[reply] ({time.monotonic() - t:.1f}s){taken} {reply}", flush=True)


async def run(max_steps: int, comments: list[dict]) -> bool:
    """Play, and report whether the house is complete."""
    settings = load_settings(config_dir=CONFIG_DIR)
    event_bus = AsyncEventBus()
    conversation = Conversation()

    async def on_designed(event: HouseDesignedEvent) -> None:
        print(f"[design] {event.name}: {event.concept}", flush=True)

    async def on_goal(event: GoalSetEvent) -> None:
        who = f" [requested by {event.requested_by}]" if event.requested_by else ""
        print(f"[goal] {event.goal}{who}: {event.reason}", flush=True)

    async def on_completed(event: HouseCompletedEvent) -> None:
        print(f"[done] {event.name} is complete", flush=True)

    async def say(text: str) -> None:
        print(f"[say] {text}", flush=True)

    event_bus.subscribe(HouseDesignedEvent, on_designed)
    event_bus.subscribe(GoalSetEvent, on_goal)
    event_bus.subscribe(HouseCompletedEvent, on_completed)

    game = create_game_service(
        gemini=settings.gemini,
        jev=settings.jev,
        minecraft=settings.minecraft,
        character=settings.character,
        event_publisher=event_bus,
        conversation=conversation,
    )
    llm = create_llm_service(
        gemini=settings.gemini,
        character=settings.character,
        event_publisher=event_bus,
        conversation=conversation,
    )
    narrator = Narrator(
        llm, activity=lambda: game.session.activity() if game.session else None, say=say
    )
    narrator.subscribe(event_bus)
    chat = asyncio.create_task(feed_comments(comments, game, llm)) if comments else None
    try:
        outcome = await game.play(max_steps=max_steps)
        await narrator.drain()
    finally:
        if chat:
            chat.cancel()
        await game.close()
        await llm.close()

    b = outcome.session.blueprint
    print("=" * 60)
    print(f"House: {b.name} {b.width}x{b.depth}x{b.wall_height} ({len(b.blocks())} blocks)")
    print(f"Complete: {outcome.house_complete} after {outcome.steps} steps")
    return outcome.house_complete


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--comments", type=Path, help="scripted viewer comments (JSON)")
    args = parser.parse_args()
    comments = json.loads(args.comments.read_text()) if args.comments else []
    sys.exit(0 if asyncio.run(run(args.max_steps, comments)) else 1)


if __name__ == "__main__":
    main()
