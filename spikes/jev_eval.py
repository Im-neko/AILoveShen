"""Jev のスパイク: 今実行できる有界アクションから Jev に 1 つ選ばせる。

raw の状態と要約した状態を比べる。クローズドループ（観測 -> 判断 -> 実行）も回せる。

    TYPESAFE_API_KEY=... python spikes/jev_eval.py compare --repeat 3 --label night_zombie
    TYPESAFE_API_KEY=... python spikes/jev_eval.py loop --steps 8
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, Choice

BRIDGE = "http://127.0.0.1:3000"
RESULTS = Path(__file__).parent / "results"
INSTRUCTIONS = (
    "You control a Minecraft survival player. Choose the single best next action. "
    "Priorities: 1) stay alive, 2) gather resources to progress, 3) explore."
)


BOT = "AILoveShen"
HOME = "90.5 75 -43.5"
RESET = [
    "time set day",
    "kill @e[type=!player]",
    f"clear {BOT}",
    f"effect clear {BOT}",
    f"tp {BOT} {HOME}",
    f"effect give {BOT} instant_health 1 5",
    f"effect give {BOT} saturation 1 20",
    # 前のシナリオのモブを倒すと、少し遅れてドロップが出る
    "sleep 1",
    "kill @e[type=item]",
]
# 名前 -> （RESET の後の準備コマンド、待つ秒数、妥当な選択）
SCENARIOS = {
    "day_safe": ([], 2, {"collect_log", "explore"}),
    "drop_nearby": (
        [
            f'execute at {BOT} run summon item ~5 ~1 ~ {{Item:{{id:"minecraft:iron_ingot",count:3}}}}'
        ],
        2,
        {"pickup_drop"},
    ),
    "hungry_safe": (
        [
            "sleep 2",
            f"effect give {BOT} hunger 30 255",
            "sleep 10",
            f"effect clear {BOT}",
            f"give {BOT} bread 3",
        ],
        1,
        {"eat"},
    ),
    # 判断の難しい場面: アクションの説明文だけでは正解が 1 つに決まらない
    "hungry_zombie_near": (
        [
            "time set night",
            "sleep 2",
            f"effect give {BOT} hunger 30 255",
            "sleep 10",
            f"effect clear {BOT}",
            f"give {BOT} bread 3",
            f"execute at {BOT} run summon zombie ~3 ~ ~ {{NoAI:1b}}",
        ],
        1,
        {"flee_hostile", "attack_hostile"},
    ),
    "zombie_far_day": (
        [f"give {BOT} iron_sword", f"execute at {BOT} run summon zombie ~14 ~ ~ {{NoAI:1b}}"],
        2,
        {"collect_log", "attack_hostile"},
    ),
    "unarmed_full_zombie": (
        ["time set night", f"execute at {BOT} run summon zombie ~4 ~ ~ {{NoAI:1b}}"],
        2,
        {"attack_hostile", "flee_hostile"},
    ),
    "night_zombie_armed": (
        [
            "time set night",
            f"give {BOT} iron_sword",
            f"execute at {BOT} run summon zombie ~4 ~ ~ {{NoAI:1b}}",
        ],
        2,
        {"attack_hostile", "equip_weapon"},
    ),
    "night_zombie_lowhp": (
        [
            "time set night",
            f"damage {BOT} 14",
            f"execute at {BOT} run summon zombie ~3 ~ ~ {{NoAI:1b}}",
        ],
        2,
        {"flee_hostile"},
    ),
    "creeper_close": (
        [f"execute at {BOT} run summon creeper ~3 ~ ~ {{NoAI:1b}}"],
        2,
        {"flee_hostile"},
    ),
}


def rcon(cmd: str) -> str:
    return subprocess.run(
        ["docker", "exec", "ailoveshen-minecraft", "rcon-cli", cmd],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def setup(name: str) -> None:
    cmds, wait, _ = SCENARIOS[name]
    for c in RESET + cmds:
        if c.startswith("sleep "):
            time.sleep(float(c.split()[1]))
        else:
            rcon(c)
    time.sleep(wait)


def observe() -> dict:
    with urllib.request.urlopen(f"{BRIDGE}/observe", timeout=10) as r:
        return json.load(r)


def act(action_id: str) -> dict:
    req = urllib.request.Request(
        f"{BRIDGE}/act",
        data=json.dumps({"id": action_id}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


async def decide(
    client: AsyncTypeSafeClient, state: dict, actions: list[dict], key: str = "description"
) -> dict:
    criteria = {a["id"]: a[key] for a in actions}
    t = time.perf_counter()
    r = await client.system_one(
        state=state, questions={"action": Choice(instructions=INSTRUCTIONS, criteria=criteria)}
    )
    answer = r.choices["action"]
    return {
        "choice": answer.choice,
        "confidence": answer.confidence,
        "probabilities": dict(sorted(answer.probabilities.items(), key=lambda kv: -kv[1])),
        "latency_s": round(time.perf_counter() - t, 3),
        "input_tokens": r.usage.input_tokens,
        "output_tokens": r.usage.output_tokens,
        "model": r.model,
    }


# 比べる条件 -> （状態のキー、候補の説明文のキー）
VARIANTS = {
    "summary": ("summary", "description"),
    "raw": ("raw", "description"),
    "summary_generic": ("summary", "generic"),
    "raw_generic": ("raw", "generic"),
}


async def compare(repeat: int, label: str, scenario: str | None = None) -> None:
    if scenario:
        setup(scenario)
    obs = observe()
    RESULTS.mkdir(exist_ok=True)
    expected = sorted(SCENARIOS[scenario][2]) if scenario else None
    out = {
        "label": label,
        "expected": expected,
        "summary": obs["summary"],
        "actions": obs["actions"],
        "runs": {},
    }
    print(f"== {label}: {json.dumps(obs['summary'], ensure_ascii=False)[:400]}")
    print("アクション:", [a["id"] for a in obs["actions"]])
    async with AsyncTypeSafeClient() as client:
        for variant, (state_key, desc_key) in VARIANTS.items():
            runs = []
            for _ in range(repeat):
                try:
                    runs.append(await decide(client, obs[state_key], obs["actions"], desc_key))
                except (
                    Exception
                ) as e:  # API のエラー（ペイロードが大きすぎる など）も結果として記録する
                    runs.append({"error": f"{type(e).__name__}: {e}"})
            out["runs"][variant] = runs
            ok = [r for r in runs if "choice" in r]
            lat = [r["latency_s"] for r in ok]
            print(
                f"-- {variant}: choices={[r['choice'] for r in ok]} "
                f"latency median={statistics.median(lat) if lat else None} "
                f"tokens_in={ok[0]['input_tokens'] if ok else None}"
            )
            for r in runs:
                print(
                    "   ",
                    r.get("error") or {k: r[k] for k in ("choice", "confidence", "probabilities")},
                )
    (RESULTS / f"{label}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    if expected:
        print("妥当な選択:", expected)


async def loop(steps: int) -> None:
    async with AsyncTypeSafeClient() as client:
        for i in range(steps):
            obs = observe()
            d = await decide(client, obs["summary"], obs["actions"])
            s = obs["summary"]["self"]
            print(
                f"[{i}] hp={s['health']} food={s['food']} phase={obs['summary']['time']['phase']} "
                f"-> {d['choice']} (conf {d['confidence']}, {d['latency_s']}s)",
                flush=True,
            )
            res = act(d["choice"])
            print(f"     {res}", flush=True)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--repeat", type=int, default=3)
    c.add_argument("--label", required=True)
    c.add_argument("--scenario", choices=sorted(SCENARIOS))
    sc = sub.add_parser("scenarios")
    sc.add_argument("--repeat", type=int, default=3)
    sc.add_argument("--only", nargs="*", choices=sorted(SCENARIOS))
    lp = sub.add_parser("loop")
    lp.add_argument("--steps", type=int, default=8)
    a = p.parse_args()
    if a.cmd == "compare":
        asyncio.run(compare(a.repeat, a.label, a.scenario))
    elif a.cmd == "scenarios":
        for name in SCENARIOS:
            if not a.only or name in a.only:
                asyncio.run(compare(a.repeat, name, name))
    else:
        asyncio.run(loop(a.steps))


if __name__ == "__main__":
    main()
