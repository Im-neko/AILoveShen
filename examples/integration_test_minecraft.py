#!/usr/bin/env python3
"""結合テスト: Gemini が家を設計して目標を決め、Jev が行動を選び、ブリッジがプレイする。

ボットには何も与えない。木を集め、クラフトし、家を建て、夜を越すまでを自分で行う。
目標の達成はブリッジが世界から判定する。

配信者も話す: 目標が変わると実況し、台本（--comments）の視聴者コメントにはプレイしながら
返事をする。頼みを引き受けた返事は、その頼みを中目標に足す（今進めている中目標の後ろ）。
話したことは表示する（[say]、[reply]）。ミッションと中目標は設定（minecraft.mission）から
読み、実行をまたいで引き継ぐ（data/mission.json）。

--control tools を付けると、候補から Jev が選ぶ代わりに、Gemini が道具を呼んで操作し、Jev は
道具の実行中に Gemini が添えた質問（見張り）に答える（docs/design/21_tool_control.md）。
見張りの記録は logs/watch/ に残る。

--board-port を付けると、配信のオーバーレイ用に目標を HTTP で出す:
http://127.0.0.1:<port>/overlay（ailoveshen[stream] が要る）。

前提:
1. Minecraft サーバーが動いている（docker/docker-compose.minecraft.yml）
2. ブリッジが動いている: cd minecraft-bridge && npm install && npm start
   （ボットの視点を見るには、1.21.4 のクライアントで 127.0.0.1:25578 に接続する）
3. 環境変数 GEMINI_API_KEY と TYPESAFE_API_KEY を設定してある
4. 依存をインストールしてある: pip install "ailoveshen[llm,game]"

使い方:
    python examples/integration_test_minecraft.py [--max-steps 300] [--comments comments.json]
        [--board-port 8765] [--control candidates|tools]

comments.json: [{"after_seconds": 60, "user": "neko", "message": "ベッド作って！"}, ...]
（after_seconds はプレイ開始からの秒数）
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# プロジェクトの src をパスに足す
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
    """台本のコメントを決まった時刻に送り、返事を表示する。"""
    started = time.monotonic()
    for c in sorted(comments, key=lambda c: c["after_seconds"]):
        await asyncio.sleep(max(0.0, c["after_seconds"] - (time.monotonic() - started)))
        session = game.session
        goal_before = session.goal.spec.describe() if session and session.goal else "-"
        print(f"[chat] {c['user']}: {c['message']} (今の目標: {goal_before})", flush=True)
        t = time.monotonic()
        reply = await llm.generate_response(c["user"], c["message"], session=session)
        print(f"[reply] ({time.monotonic() - t:.1f}s) {reply}", flush=True)


async def run(
    max_steps: int, comments: list[dict], board_port: int | None, control: str | None = None
) -> bool:
    """プレイして、家が完成したかを返す。"""
    settings = load_settings(config_dir=CONFIG_DIR)
    if control:
        settings.minecraft.control = control
    print(f"[control] {settings.minecraft.control}", flush=True)
    event_bus = AsyncEventBus()
    conversation = Conversation()

    async def on_designed(event: HouseDesignedEvent) -> None:
        print(f"[design] {event.name}: {event.concept}", flush=True)

    async def on_goal(event: GoalSetEvent) -> None:
        serves = f" [{event.mid_goal} のため]" if event.mid_goal else " [生存]"
        print(f"[goal] {event.goal}{serves}: {event.reason}", flush=True)

    async def on_mid_added(event: MidGoalAddedEvent) -> None:
        who = f" [{event.requested_by} の頼み]" if event.requested_by else ""
        print(f"[mid+] #{event.position} {event.title}{who}: {event.reason}", flush=True)

    async def on_mid_done(event: MidGoalCompletedEvent) -> None:
        print(f"[mid✓] {event.title}", flush=True)

    async def on_mid_dropped(event: MidGoalDroppedEvent) -> None:
        print(f"[mid×] {event.title}: {event.reason}", flush=True)

    async def on_completed(event: HouseCompletedEvent) -> None:
        print(f"[done] {event.name} が完成", flush=True)

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
    if b is None:
        print("家: 前の実行で建てた拠点")
    else:
        print(f"家: {b.name} {b.width}x{b.depth}x{b.wall_height}（{len(b.blocks())} ブロック）")
    print(f"完成: {outcome.house_complete}（{outcome.steps} ステップ）")
    return outcome.house_complete


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--comments", type=Path, help="台本の視聴者コメント（JSON）")
    parser.add_argument("--board-port", type=int, help="目標ボード（オーバーレイ）を出すポート")
    parser.add_argument(
        "--control",
        choices=["candidates", "tools"],
        help="行動の決め方（既定は設定の minecraft.agent.control）",
    )
    args = parser.parse_args()
    comments = json.loads(args.comments.read_text()) if args.comments else []
    sys.exit(0 if asyncio.run(run(args.max_steps, comments, args.board_port, args.control)) else 1)


if __name__ == "__main__":
    main()
