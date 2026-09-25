"""Live check of coherent replies: real Gemini, real bridge observation, scripted comments.

For each comment: the reply, whether it took a goal, the latency. Then one play step with the
request pending, to see the promised goal become the goal the bot pursues.
"""

import asyncio
import subprocess
import time

import httpx

from ailoveshen.domain.entities import Conversation, PlaySession
from ailoveshen.domain.value_objects import Goal, GoalPredicate, GoalSpec, HouseBlueprint, Side
from ailoveshen.factories.game import create_game_service
from ailoveshen.factories.llm import create_llm_service
from ailoveshen.infrastructure.config import load_settings
from ailoveshen.infrastructure.events import AsyncEventBus
from ailoveshen.presentation.services import Narrator

DAY = [
    ("neko", "ベッド作ってほしい！夜寝られるように"),
    ("inu", "今なにしてるの？"),
    ("usagi", "シェンちゃんかわいい〜"),
    ("tori", "ちょっと探検してきてよ"),
    ("kuma", "朝になったら羊探してね"),
    ("neko", "ダイヤ掘ってきて！"),
]
NIGHT = [
    ("inu", "外でゾンビ倒してきて！"),
    ("usagi", "今なにしてるの？"),
    ("kuma", "木を取ってきて！"),
    ("tori", "ちょっと探検してきて"),
]
# A request is pending: replies must not claim the goal about to end comes first
PENDING = [
    ("neko", "ベッド作ってほしい！夜寝られるように"),
    ("tori", "ちょっと探検してきてよ"),
    ("inu", "今なにしてるの？"),
]


def rcon(cmd: str) -> None:
    subprocess.run(["docker", "exec", "ailoveshen-minecraft", "rcon-cli", cmd], capture_output=True)


async def main() -> None:
    s = load_settings()
    bus = AsyncEventBus()
    conversation = Conversation()
    llm = create_llm_service(s.gemini, s.character, bus, conversation=conversation)
    game = create_game_service(s.gemini, s.jev, s.minecraft, s.character, bus, conversation)
    bridge = game._bridge  # the same client the play loop uses
    said: list[str] = []

    async def say(text: str) -> None:
        said.append(text)
        print(f"  [say] {text}")

    session = PlaySession(blueprint=HouseBlueprint("ぽかぽかログハウス", "木の家", 5, 5, 3, Side.SOUTH, 2))
    session.completion_announced = True  # the house was finished in an earlier run
    narrator = Narrator(llm, activity=session.activity, say=say)
    narrator.subscribe(bus)

    async def reset(tod: int) -> None:
        rcon("tp AILoveShen 408.5 72 -270.5")
        rcon(f"time set {tod}")
        if tod < 12000:
            spec, why = GoalSpec(GoalPredicate.HAVE, item="log", count=3), "木の剣を作るための原木を集める"
        else:
            spec, why = GoalSpec(GoalPredicate.THROUGH_NIGHT), "夜は危ないので家で朝を待つ"
        httpx.put(f"http://{s.minecraft.bridge_host}:{s.minecraft.bridge_port}/goal", json=spec.to_dict())
        session.set_goal(Goal(spec, reason=why), "day" if tod < 12000 else "night")
        session.request = None
        session.observe(await bridge.observe())

    async def ask(user: str, message: str) -> None:
        before = session.request
        t = time.monotonic()
        reply = await llm.generate_response(user, message, session=session)
        dt = time.monotonic() - t
        took = session.request if session.request is not before else None
        goal = f" -> {took.goal.spec.describe()} ({took.goal.reason})" if took else ""
        print(f"[{user}] {message}\n  ({dt:.1f}s){goal}\n  {reply}")

    import sys

    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    if only in ("all", "day"):
        print("=== day (goal: have(log, 3)) ===")
        await reset(3000)
        for user, message in DAY:
            await ask(user, message)
    if only in ("all", "pending"):
        for round_ in (1, 2):
            print(f"\n=== a request pending, round {round_} ===")
            await reset(3000)
            for user, message in PENDING:
                await ask(user, message)
    if only in ("all", "step"):
        print("\n=== one step with the pending request ===")
        pending = session.request
        who = pending.user_name if pending else "-"
        print(f"pending: {pending.goal.spec.describe() if pending else None} by {who}")
        report = await game._advance.execute(session)
        print(
            f"goal now: {session.goal.spec.describe()} requested_by={session.goal.requested_by} "
            f"changed={report.goal_changed}"
        )
    if only in ("all", "night"):
        for round_ in (1, 2):
            print(f"\n=== night, round {round_} (goal: through_night) ===")
            await reset(14000)
            for user, message in NIGHT:
                await ask(user, message)
    await narrator.drain()
    rcon("time set 1000")
    await llm.close()
    await game.close()


asyncio.run(main())
