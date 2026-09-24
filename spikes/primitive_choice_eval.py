"""Primitive-action spike: can Jev pick the right grounded primitive among many candidates?

Candidates are concrete primitive instances (dig this block, craft that item, attack that mob), as
the bridge would enumerate them. Each scenario has the goal (an item/state predicate with its
subgoals, as a dependency solver would derive them), the bot's state, a few relevant candidates
and the acceptable choices; unrelated but executable candidates pad the list to N.

Compared, on the same shuffled candidates:
- jev: descriptions without hints
- jev_hint (results/primitives/full.json only): descriptions say which subgoal a candidate advances
- jev_needs: the state also lists needs (critical health, urgent hunger, night coming, threats)
- jev_prio: the instructions give a general priority order
- rule: safety rules, then the nearest candidate advancing the first open subgoal (a hand baseline)

    TYPESAFE_API_KEY=... python spikes/primitive_choice_eval.py --sizes 10 20 50 --repeat 3
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, Choice

RESULTS = Path(__file__).parent / "results" / "primitives"
INSTRUCTIONS = (
    "You control a Minecraft survival player working toward the goal in the state. "
    "Choose the single best next primitive action. Stay alive first; otherwise make progress on the goal."
)
PRIORITY_INSTRUCTIONS = (
    "You control a Minecraft survival player. Choose the single best next primitive action. "
    "Priorities, highest first: 1) get away from danger you cannot win (low health, creepers, no weapon); "
    "2) fix urgent needs of the body (low food: eat); 3) do not stay outside at night (be home, sleep if you can); "
    "4) otherwise make progress on the goal in the state."
)


def needs(state: dict) -> list[str]:
    """Generic needs from the numbers, stated without naming actions (what the bridge could compute)."""
    s, out = state["self"], []
    if s["health"] <= 8:
        out.append(f"health critical ({s['health']}/20)")
    if s["food"] <= 6:
        out.append(f"hunger urgent ({s['food']}/20): healing stops below 18 and sprinting below 7")
    if s["time"].startswith("dusk"):
        out.append("night is coming: hostile mobs spawn outside in the dark")
    for m in state["nearby"]:
        if m.get("hostile"):
            out.append(f"hostile {m['mob']} {m['distance']}m away")
    return out or ["none"]


def c(key, verb, target=None, advances=None, **info):
    """A grounded candidate: key, description (verb, target, extra facts) and the subgoal it advances."""
    desc = {"verb": verb}
    if target:
        desc["target"] = target
    desc.update(info)
    return {"key": key, "desc": desc, "advances": advances}


BASE_SELF = {"health": 20, "food": 20, "time": "day (7 minutes until dusk)", "weapon": None}


def scenario(goal, subgoals, self_=None, inventory=None, nearby=None, relevant=(), accept=(), note=""):
    return {
        "state": {
            "goal": goal,
            "subgoals": subgoals,
            "self": {**BASE_SELF, **(self_ or {})},
            "inventory": inventory or {},
            "nearby": nearby or [],
        },
        "relevant": list(relevant),
        "accept": set(accept),
        "note": note,
    }


SCENARIOS = {
    "logs_empty": scenario(
        "have 4 oak_planks", ["have 1 oak_log (0/1)", "craft oak_planks"],
        relevant=[
            c("dig oak_log at 3m", "dig", "oak_log", "have 1 oak_log", distance=3),
            c("dig oak_log at 9m", "dig", "oak_log", "have 1 oak_log", distance=9),
        ],
        accept={"dig oak_log at 3m"}),
    "craft_planks": scenario(
        "have 4 oak_planks", ["have 1 oak_log (1/1) done", "craft oak_planks"], inventory={"oak_log": 1},
        relevant=[
            c("craft oak_planks x4", "craft", "oak_planks", "craft oak_planks", uses={"oak_log": 1}),
            c("dig oak_log at 4m", "dig", "oak_log", None, distance=4),
        ],
        accept={"craft oak_planks x4"}),
    "drop_on_ground": scenario(
        "have 3 oak_log", ["have 3 oak_log (1/3)"], inventory={"oak_log": 1},
        nearby=[{"item_on_ground": "oak_log x2", "distance": 2}],
        relevant=[
            c("pick up oak_log x2 at 2m", "pickup", "oak_log x2", "have 3 oak_log", distance=2),
            c("dig oak_log at 6m", "dig", "oak_log", "have 3 oak_log", distance=6),
        ],
        accept={"pick up oak_log x2 at 2m"}),
    "zombie_unarmed": scenario(
        "have 3 oak_log", ["have 3 oak_log (0/3)"], self_={"time": "night"},
        nearby=[{"mob": "zombie", "hostile": True, "distance": 4}],
        relevant=[
            c("flee from zombie", "flee", "zombie", None, distance=4),
            c("attack zombie", "attack", "zombie", None, distance=4, weapon="none (fist)"),
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3),
        ],
        accept={"flee from zombie"}),
    "zombie_armed": scenario(
        "have 3 oak_log", ["have 3 oak_log (0/3)"], self_={"time": "night", "weapon": "wooden_sword (in hand)"},
        inventory={"wooden_sword": 1}, nearby=[{"mob": "zombie", "hostile": True, "distance": 4}],
        relevant=[
            c("flee from zombie", "flee", "zombie", None, distance=4),
            c("attack zombie", "attack", "zombie", None, distance=4, weapon="wooden_sword"),
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3),
        ],
        accept={"attack zombie", "flee from zombie"}),
    "creeper_armed": scenario(
        "have 3 oak_log", ["have 3 oak_log (0/3)"], self_={"weapon": "wooden_sword (in hand)"},
        inventory={"wooden_sword": 1}, nearby=[{"mob": "creeper", "hostile": True, "distance": 5}],
        relevant=[
            c("flee from creeper", "flee", "creeper", None, distance=5),
            c("attack creeper", "attack", "creeper", None, distance=5, weapon="wooden_sword"),
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3),
        ],
        accept={"flee from creeper"}),
    "hungry": scenario(
        "have 3 oak_log", ["have 3 oak_log (0/3)"], self_={"food": 5}, inventory={"cooked_beef": 2},
        relevant=[
            c("eat cooked_beef", "eat", "cooked_beef", None),
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3),
        ],
        accept={"eat cooked_beef"}),
    "wool_sheep_vs_cow": scenario(
        "have 1 white_bed", ["have 3 white_wool (0/3)", "have 3 oak_planks (3/3) done", "craft white_bed at a crafting_table"],
        inventory={"oak_planks": 3, "wooden_sword": 1}, self_={"weapon": "wooden_sword (in hand)"},
        relevant=[
            c("attack sheep at 10m", "attack", "sheep", "have 3 white_wool", distance=10, drops="white_wool, mutton"),
            c("attack cow at 5m", "attack", "cow", None, distance=5, drops="beef, leather"),
            c("attack pig at 6m", "attack", "pig", None, distance=6, drops="porkchop"),
        ],
        accept={"attack sheep at 10m"}),
    "place_table": scenario(
        "have 1 wooden_pickaxe", ["have 3 oak_planks (3/3) done", "have 2 stick (2/2) done",
                                  "a crafting_table within reach (none)", "craft wooden_pickaxe"],
        inventory={"oak_planks": 3, "stick": 2, "crafting_table": 1},
        relevant=[
            c("place crafting_table at 2m", "place", "crafting_table", "a crafting_table within reach", distance=2),
            c("craft oak_button", "craft", "oak_button", None, uses={"oak_planks": 1}),
            c("craft stick x4", "craft", "stick", None, uses={"oak_planks": 2}),
        ],
        accept={"place crafting_table at 2m"}),
    "craft_bed_at_table": scenario(
        "have 1 white_bed", ["have 3 white_wool (3/3) done", "have 3 oak_planks (3/3) done",
                             "a crafting_table within reach (2m) done", "craft white_bed"],
        inventory={"white_wool": 3, "oak_planks": 3},
        relevant=[
            c("craft white_bed at crafting_table", "craft", "white_bed", "craft white_bed",
              uses={"white_wool": 3, "oak_planks": 3}, station="crafting_table 2m"),
            c("craft oak_pressure_plate", "craft", "oak_pressure_plate", None, uses={"oak_planks": 2}),
        ],
        accept={"craft white_bed at crafting_table"}),
    "night_sleep": scenario(
        "have 12 oak_log", ["have 12 oak_log (4/12)"], self_={"time": "night (just began)", "in_home": True},
        nearby=[{"block": "white_bed", "distance": 2}],
        relevant=[
            c("sleep in white_bed", "use", "white_bed", None, distance=2, effect="skips the night"),
            c("open oak_door", "use", "oak_door", None, distance=1),
            c("dig oak_log at 12m", "dig", "oak_log", "have 12 oak_log", distance=12, outside=True),
        ],
        accept={"sleep in white_bed"}),
    "low_hp_zombie_far": scenario(
        "have 12 oak_log", ["have 12 oak_log (4/12)"], self_={"health": 5, "time": "night",
                                                              "weapon": "wooden_sword (in hand)"},
        inventory={"wooden_sword": 1}, nearby=[{"mob": "zombie", "hostile": True, "distance": 9}],
        relevant=[
            c("flee from zombie", "flee", "zombie", None, distance=9),
            c("attack zombie", "attack", "zombie", None, distance=9, weapon="wooden_sword"),
            c("dig oak_log at 4m", "dig", "oak_log", "have 12 oak_log", distance=4),
        ],
        accept={"flee from zombie"}),
    "dusk_far_home": scenario(
        "have 12 oak_log", ["have 12 oak_log (9/12)"], self_={"time": "dusk (1 minute until night)",
                                                              "home": "58m away, door closes"},
        relevant=[
            c("go to home (58m)", "goto", "home", None, distance=58),
            c("dig oak_log at 3m", "dig", "oak_log", "have 12 oak_log", distance=3),
        ],
        accept={"go to home (58m)"}),
}

# Held out: written after the needs/priority variants were first measured
SCENARIOS.update({
    "h_hungry_zombie_close": scenario(
        "have 3 oak_log", ["have 3 oak_log (0/3)"], self_={"food": 5, "time": "night"}, inventory={"cooked_beef": 2},
        nearby=[{"mob": "zombie", "hostile": True, "distance": 3}],
        relevant=[
            c("eat cooked_beef", "eat", "cooked_beef", None),
            c("flee from zombie", "flee", "zombie", None, distance=3),
            c("attack zombie", "attack", "zombie", None, distance=3, weapon="none (fist)"),
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3),
        ],
        accept={"flee from zombie"}),
    "h_food_ok": scenario(
        "have 3 oak_log", ["have 3 oak_log (0/3)"], self_={"food": 14}, inventory={"cooked_beef": 2},
        relevant=[
            c("eat cooked_beef", "eat", "cooked_beef", None),
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3),
        ],
        accept={"dig oak_log at 3m"}),
    "h_zombie_far_day": scenario(
        "have 3 oak_log", ["have 3 oak_log (0/3)"], self_={"weapon": "wooden_sword (in hand)"},
        inventory={"wooden_sword": 1}, nearby=[{"mob": "zombie", "hostile": True, "distance": 22}],
        relevant=[
            c("flee from zombie", "flee", "zombie", None, distance=22),
            c("attack zombie", "attack", "zombie", None, distance=22, weapon="wooden_sword"),
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3),
        ],
        accept={"dig oak_log at 3m"}),
    "h_creeper_far": scenario(
        "have 3 oak_log", ["have 3 oak_log (0/3)"], nearby=[{"mob": "creeper", "hostile": True, "distance": 18}],
        relevant=[
            c("flee from creeper", "flee", "creeper", None, distance=18),
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3, direction="away from the creeper"),
        ],
        accept={"dig oak_log at 3m"}),
    "h_low_hp_no_threat": scenario(
        "have 3 oak_log", ["have 3 oak_log (1/3)"], self_={"health": 7}, inventory={"oak_log": 1},
        relevant=[
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3),
            c("flee", "flee", "nothing nearby", None),
        ],
        accept={"dig oak_log at 3m"}),
    "h_low_hp_hungry": scenario(
        "have 3 oak_log", ["have 3 oak_log (1/3)"], self_={"health": 6, "food": 4}, inventory={"oak_log": 1, "bread": 3},
        relevant=[
            c("eat bread", "eat", "bread", None),
            c("dig oak_log at 3m", "dig", "oak_log", "have 3 oak_log", distance=3),
        ],
        accept={"eat bread"}),
    "h_night_home_no_bed": scenario(
        "have 12 oak_log", ["have 12 oak_log (4/12)"], self_={"time": "night (just began)", "in_home": True},
        relevant=[
            c("wait inside", "wait", "inside the house", None, duration="10s"),
            c("open oak_door", "use", "oak_door", None, distance=1),
            c("dig oak_log at 12m", "dig", "oak_log", "have 12 oak_log", distance=12, outside=True),
        ],
        accept={"wait inside"}),
})

# Executable but unrelated to every scenario's goal (padding)
DISTRACTORS = [
    c("dig dirt at 1m", "dig", "dirt", None, distance=1),
    c("dig grass_block at 2m", "dig", "grass_block", None, distance=2),
    c("dig stone at 5m", "dig", "stone", None, distance=5, needs="a pickaxe to drop anything"),
    c("dig sand at 7m", "dig", "sand", None, distance=7),
    c("dig birch_leaves at 4m", "dig", "birch_leaves", None, distance=4),
    c("dig short_grass at 2m", "dig", "short_grass", None, distance=2),
    c("dig dandelion at 3m", "dig", "dandelion", None, distance=3),
    c("dig gravel at 8m", "dig", "gravel", None, distance=8),
    c("attack chicken at 7m", "attack", "chicken", None, distance=7, drops="chicken, feather"),
    c("attack rabbit at 12m", "attack", "rabbit", None, distance=12, drops="rabbit"),
    c("goto (+10, 0, +4)", "goto", "position 11m east", None, distance=11),
    c("goto (-8, 0, -12)", "goto", "position 14m north-west", None, distance=14),
    c("goto (+20, 3, -3)", "goto", "hill 20m east", None, distance=20),
    c("goto river (15m)", "goto", "river", None, distance=15),
    c("place dirt at 1m", "place", "dirt", None, distance=1),
    c("look around", "look", "around", None),
    c("pick up wheat_seeds at 9m", "pickup", "wheat_seeds x1", None, distance=9),
    c("pick up poppy at 11m", "pickup", "poppy x1", None, distance=11),
    c("dig spruce_log at 30m", "dig", "spruce_log", None, distance=30),
    c("dig clay at 18m", "dig", "clay", None, distance=18),
    c("dig sugar_cane at 16m", "dig", "sugar_cane", None, distance=16),
    c("dig pumpkin at 25m", "dig", "pumpkin", None, distance=25),
    c("attack squid at 17m", "attack", "squid", None, distance=17, drops="ink_sac"),
    c("attack bat at 6m", "attack", "bat", None, distance=6),
    c("goto (0, 0, +30)", "goto", "position 30m south", None, distance=30),
    c("goto cave entrance (22m)", "goto", "cave entrance", None, distance=22),
    c("goto village (120m)", "goto", "village", None, distance=120),
    c("goto (-40, 0, 5)", "goto", "position 40m west", None, distance=40),
    c("dig tall_grass at 5m", "dig", "tall_grass", None, distance=5),
    c("dig azure_bluet at 6m", "dig", "azure_bluet", None, distance=6),
    c("dig coarse_dirt at 9m", "dig", "coarse_dirt", None, distance=9),
    c("dig mossy_cobblestone at 21m", "dig", "mossy_cobblestone", None, distance=21, needs="a pickaxe"),
    c("dig coal_ore at 14m", "dig", "coal_ore", None, distance=14, needs="a pickaxe"),
    c("dig iron_ore at 26m", "dig", "iron_ore", None, distance=26, needs="a stone pickaxe"),
    c("place dirt at 2m (tower up)", "place", "dirt", None, distance=0),
    c("jump", "jump", None, None),
    c("sneak", "sneak", None, None),
    c("swap to empty hand", "equip", "empty hand", None),
    c("drop dirt", "drop", "dirt", None),
    c("look at the sky", "look", "sky", None),
    c("goto (+5, -2, +5)", "goto", "hollow 7m south-east", None, distance=7),
    c("goto (-3, 0, +9)", "goto", "position 9m south", None, distance=9),
    c("attack salmon at 15m", "attack", "salmon", None, distance=15, drops="salmon"),
    c("dig oxeye_daisy at 8m", "dig", "oxeye_daisy", None, distance=8),
    c("dig red_mushroom at 13m", "dig", "red_mushroom", None, distance=13),
    c("dig snow at 40m", "dig", "snow", None, distance=40),
    c("goto (+60, 0, 0)", "goto", "position 60m east", None, distance=60),
    c("pick up bone at 19m", "pickup", "bone x1", None, distance=19),
]


# variant -> ask() options
VARIANTS = {
    "jev": {"hint": False},
    "jev_needs": {"hint": False, "with_needs": True},
    "jev_prio": {"hint": False, "instructions": PRIORITY_INSTRUCTIONS},
    "jev_needs_prio": {"hint": False, "with_needs": True, "instructions": PRIORITY_INSTRUCTIONS},
}


def candidates(sc: dict, n: int, rng: random.Random) -> list[dict]:
    keys = {x["key"] for x in sc["relevant"]}
    pad = [d for d in DISTRACTORS if d["key"] not in keys]
    cands = sc["relevant"] + rng.sample(pad, max(0, n - len(sc["relevant"])))
    rng.shuffle(cands)
    return cands


def criteria(cands: list[dict], hint: bool) -> dict:
    out = {}
    for x in cands:
        d = dict(x["desc"])
        if hint:
            d["advances"] = x["advances"] or "no subgoal"
        out[x["key"]] = d
    return out


def rule(sc: dict, cands: list[dict]) -> str:
    """Hand baseline: flee a close/unfightable threat, eat when hungry, else nearest subgoal step."""
    s = sc["state"]
    by_verb = {}
    for x in cands:
        by_verb.setdefault(x["desc"]["verb"], []).append(x)
    threats = [m for m in s["nearby"] if m.get("hostile")]
    if threats:
        t = min(threats, key=lambda m: m["distance"])
        armed = bool(s["self"]["weapon"])
        if t["distance"] <= 6 or s["self"]["health"] <= 8:
            want = "attack" if armed and t["mob"] != "creeper" and s["self"]["health"] > 8 else "flee"
            for x in by_verb.get(want, []):
                if x["desc"].get("target") == t["mob"]:
                    return x["key"]
    if s["self"]["food"] <= 6 and "eat" in by_verb:
        return by_verb["eat"][0]["key"]
    open_goals = [g for g in s["subgoals"] if not g.endswith("done")]
    for g in open_goals:
        steps = [x for x in cands if x["advances"] and g.startswith(x["advances"])]
        if steps:
            return min(steps, key=lambda x: x["desc"].get("distance", 0))["key"]
    return cands[0]["key"]


async def ask(client, sc, cands, hint, with_needs=False, instructions=INSTRUCTIONS):
    state = {**sc["state"], "needs": needs(sc["state"])} if with_needs else sc["state"]
    t = time.perf_counter()
    r = await client.system_one(
        state=state, questions={"action": Choice(instructions=instructions, criteria=criteria(cands, hint))}
    )
    a = r.choices["action"]
    return {"choice": a.choice, "confidence": round(a.confidence, 3), "latency_s": round(time.perf_counter() - t, 3),
            "input_tokens": r.usage.input_tokens}


async def run(sizes, repeat, only, seed):
    rng = random.Random(seed)
    rows = []
    async with AsyncTypeSafeClient() as client:
        for name, sc in SCENARIOS.items():
            if only and name not in only:
                continue
            for n in sizes:
                for rep in range(repeat):
                    cands = candidates(sc, n, rng)
                    row = {"scenario": name, "n": n, "rep": rep, "accept": sorted(sc["accept"])}
                    row["rule"] = {"choice": rule(sc, cands)}
                    for variant, kw in VARIANTS.items():
                        try:
                            row[variant] = await ask(client, sc, cands, **kw)
                        except Exception as e:  # record API errors (payload limits) as results
                            row[variant] = {"error": f"{type(e).__name__}: {e}"[:300]}
                    for v in ("rule", *VARIANTS):
                        row[v]["ok"] = row[v].get("choice") in sc["accept"]
                    rows.append(row)
                    print(f"{name:20} n={n:2} " + " ".join(
                        f"{v}={'o' if row[v]['ok'] else 'x'}:{row[v].get('choice') or row[v].get('error')}"
                        for v in ("rule", *VARIANTS)), flush=True)
    return rows


def summarize(rows, sizes):
    names = ("rule", *VARIANTS)
    print(f"\n== accuracy by candidates ({' / '.join(names)}), latency median, input tokens median")
    for n in sizes:
        rs = [r for r in rows if r["n"] == n]
        acc = "  ".join(f"{v} {sum(r[v]['ok'] for r in rs) / len(rs):.0%}" for v in names)
        lat = [r[v]["latency_s"] for r in rs for v in VARIANTS if "latency_s" in r[v]]
        tok = [r[v]["input_tokens"] for r in rs for v in VARIANTS if "input_tokens" in r[v]]
        print(f"n={n:2}: {acc}  latency {statistics.median(lat) if lat else '-'}s  "
              f"tokens {statistics.median(tok) if tok else '-'}")
    print(f"\n== per scenario (all sizes): {' / '.join(names)}")
    for name in dict.fromkeys(r["scenario"] for r in rows):
        rs = [r for r in rows if r["scenario"] == name]
        print(f"{name:20} " + " ".join(f"{sum(r[v]['ok'] for r in rs)}/{len(rs)}" for v in names))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sizes", type=int, nargs="+", default=[10, 20, 50])
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--only", nargs="*", choices=sorted(SCENARIOS))
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--label", default="run")
    a = p.parse_args()
    rows = asyncio.run(run(a.sizes, a.repeat, a.only, a.seed))
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{a.label}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    summarize(rows, a.sizes)


if __name__ == "__main__":
    main()
