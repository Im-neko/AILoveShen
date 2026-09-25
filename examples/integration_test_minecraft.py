#!/usr/bin/env python3
"""Integration test: Gemini designs a house and sets goals, Jev picks actions, the bridge plays.

Nothing is given to the bot: it gathers wood, crafts, builds and gets through
the night on its own. Goals are judged by the bridge from the world.

The streamer talks too: goal changes are narrated, and viewers' comments from
a script (--comments) are answered while playing; a reply that accepts a
request adds it to the mid goals (behind the one worked on now). What is said
is printed ([say], [reply]). The mission and the mid goals come from
config (minecraft.mission) and carry on across runs (data/mission.json).

With --board-port the goals are served for the stream overlay:
http://127.0.0.1:<port>/overlay (needs ailoveshen[stream]).

Prerequisites:
1. Minecraft server running (docker/docker-compose.minecraft.yml)
2. Bridge running: cd minecraft-bridge && npm install && npm start
   (watch the bot's view: connect a 1.21.4 client to 127.0.0.1:25578)
3. GEMINI_API_KEY and TYPESAFE_API_KEY environment variables set
4. Dependencies installed: pip install "ailoveshen[llm,game]"

Usage:
    python examples/integration_test_minecraft.py [--max-steps 300] [--comments comments.json]
        [--board-port 8765]

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
from ailoveshen.domain.events import (
    GoalSetEvent,
    HouseCompletedEvent,
    HouseDesignedEvent,
    MidGoalAddedEvent,
    MidGoalCompletedEvent,
    MidGoalDroppedEvent,
)
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
        print(f"[reply] ({time.monotonic() - t:.1f}s) {reply}", flush=True)


async def run(max_steps: int, comments: list[dict], board_port: int | None) -> bool:
    """Play, and report whether the house is complete."""
    settings = load_settings(config_dir=CONFIG_DIR)
    event_bus = AsyncEventBus()
    conversation = Conversation()

    async def on_designed(event: HouseDesignedEvent) -> None:
        print(f"[design] {event.name}: {event.concept}", flush=True)

    async def on_goal(event: GoalSetEvent) -> None:
        serves = f" [for {event.mid_goal}]" if event.mid_goal else " [survival]"
        print(f"[goal] {event.goal}{serves}: {event.reason}", flush=True)

    async def on_mid_added(event: MidGoalAddedEvent) -> None:
        who = f" [requested by {event.requested_by}]" if event.requested_by else ""
        print(f"[mid+] #{event.position} {event.title}{who}: {event.reason}", flush=True)

    async def on_mid_done(event: MidGoalCompletedEvent) -> None:
        print(f"[mid✓] {event.title}", flush=True)

    async def on_mid_dropped(event: MidGoalDroppedEvent) -> None:
        print(f"[mid×] {event.title}: {event.reason}", flush=True)

    async def on_completed(event: HouseCompletedEvent) -> None:
        print(f"[done] {event.name} is complete", flush=True)

    async def say(text: str) -> None:
        print(f"[say] {text}", flush=True)

    event_bus.subscribe(HouseDesignedEvent, on_designed)
    event_bus.subscribe(GoalSetEvent, on_goal)
    event_bus.subscribe(HouseCompletedEvent, on_completed)
    event_bus.subscribe(MidGoalAddedEvent, on_mid_added)
    event_bus.subscribe(MidGoalCompletedEvent, on_mid_done)
    event_bus.subscribe(MidGoalDroppedEvent, on_mid_dropped)

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
        mid_goals=game.mid_goals,
    )

    def activity():
        return game.session.activity() if game.session else None

    narrator = Narrator(llm, activity=activity, say=say)
    narrator.subscribe(event_bus)
    board = None
    if board_port is not None:
        from ailoveshen.presentation.web.goal_board import GoalBoard

        goal_board = GoalBoard(activity)
        goal_board.subscribe(event_bus)
        board = asyncio.create_task(goal_board.serve(port=board_port))
    chat = asyncio.create_task(feed_comments(comments, game, llm)) if comments else None
    try:
        outcome = await game.play(max_steps=max_steps)
        await narrator.drain()
    finally:
        for task in (chat, board):
            if task:
                task.cancel()
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
    parser.add_argument("--board-port", type=int, help="serve the goal board (overlay) here")
    args = parser.parse_args()
    comments = json.loads(args.comments.read_text()) if args.comments else []
    sys.exit(0 if asyncio.run(run(args.max_steps, comments, args.board_port)) else 1)


if __name__ == "__main__":
    main()
