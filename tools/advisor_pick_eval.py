#!/usr/bin/env python3
"""
記録した行き詰まりのプロンプトを、実際のローカル LLM に投げ直して、ask_advisor を選ぶ割合を測る
（docs/design/143 §6）。選ぶかどうかだけを測る。助言が役に立ったかは測らない。

    python tools/advisor_pick_eval.py --label before                      # 記録のまま（今のコードの道具の定義）
    python tools/advisor_pick_eval.py --label after --rewrite             # 決まり・行き詰まりの節を今の文に書き換える
    python tools/advisor_pick_eval.py --since 2026-10-09T09:23 --limit 40 --concurrency 3

道具の定義（`tool_specs`）は今のコードから作る。ログには道具の名前しかないので、名前から
can_look / skills / experiment / advisor の組を決める。プロンプトは記録された文のまま送る
（`--rewrite` では、`REWRITES` の古い行を新しい行に置き換える。1 件で 1 回置き換わらなければ数えて知らせる）。
モデルへの呼び出しは配信と同じ生成器（用途ごとの考える深さ、tool_choice: required）を使う。
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ailoveshen.application.use_cases.tool_catalog import SKILL_TOOLS, tool_specs  # noqa: E402
from ailoveshen.domain.exceptions import TextGenerationError  # noqa: E402
from ailoveshen.factories.text import create_text_generator  # noqa: E402
from ailoveshen.infrastructure.config import load_settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PURPOSES = ("tool_stuck", "tool_after_failure")
ADVISOR = "ask_advisor"

# (記録された文 → 今の文)。`--rewrite` のとき、記録のプロンプトにこの古い文があれば置き換える
REWRITES: list[tuple[str, str]] = [
    (
        "- 同じ失敗を繰り返さない。断られた・失敗した理由を読んで、別の手を選ぶ\n",
        "- 同じ失敗を繰り返さない。断られた・失敗した理由を読んで、別の手を選ぶ。2 回続けて通らなければ、\n"
        "  ask_advisor（出ているとき）で原因と別の手を聞く\n",
    ),
    (
        "- 何をすればよいか分からなければ、動く前に search_abilities / item_info / search_facility で調べる\n",
        "- 何をすればよいか分からなければ、動く前に search_abilities / item_info / search_facility で調べる\n"
        "- 原因が分からない・別の手が思いつかないときは、実験や小目標の変更より先に、ask_advisor（出ているとき）で\n"
        "  聞く。試したことと失敗の理由を question に書く。答えは次の選択に出る\n",
    ),
    (
        "- 小目標そのものが今は進められないと考えたなら、家に戻って安全に別のことをする（小目標は自分で決め直される）\n",
        "- 小目標そのものが今は進められないと考えたなら、ask_advisor で聞いてから、家に戻って安全に別のことをする\n"
        "  （小目標は自分で決め直される）\n",
    ),
    (
        "  search_abilities / item_info で調べる\n",
        "  search_abilities / item_info で調べるか、ask_advisor（出ているとき）で聞く\n",
    ),
]


def entries(paths: list[Path], since: str):
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("purpose") in PURPOSES and d.get("at", "") >= since and not d.get("images"):
                    yield d


def flags_of(names: set[str]) -> dict[str, bool]:
    return {
        "can_look": "look_screen" in names,
        "skills": any(n in names for n in SKILL_TOOLS),
        "write_skill": "write_skill" in names,
        "advisor": ADVISOR in names,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="run")
    ap.add_argument("--since", default="2026-10-09T09:23", help="この時刻（UTC）以降の記録。ask_advisor が出ていた実行から")
    ap.add_argument("--limit", type=int, default=0, help="用途ごとの上限（0: 全部）")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--rewrite", action="store_true", help="REWRITES の古い文を新しい文に置き換える")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    settings = load_settings()
    generator = create_text_generator(settings.llm)
    files = sorted((ROOT / "logs" / "generations").glob("gen-*.jsonl"))
    by_purpose: dict[str, list[dict]] = {p: [] for p in PURPOSES}
    for d in entries(files, args.since):
        names = set(ast.literal_eval(d["tools"])) if isinstance(d.get("tools"), str) else set(d.get("tools") or [])
        if ADVISOR not in names:
            continue
        by_purpose[d["purpose"]].append({**d, "_names": names})
    sample = []
    for purpose, rows in by_purpose.items():
        step = max(1, len(rows) // args.limit) if args.limit else 1
        sample += rows[::step][: args.limit or None]
    print(f"{len(sample)} 件（{ {p: len(r) for p, r in by_purpose.items()} } から）")

    results: list[dict] = []
    skipped = Counter()
    sem = asyncio.Semaphore(args.concurrency)

    async def one(d: dict) -> None:
        # その手番で出さなかった道具（pickup など）は、記録の名前に合わせて外す
        specs = [s for s in tool_specs(**flags_of(d["_names"])) if s.name in d["_names"]]
        if {s.name for s in specs} != d["_names"]:
            skipped["道具の名前が今のコードと違う"] += 1
            return
        prompt = d["prompt"]
        applied = []
        if args.rewrite:
            for old, new in REWRITES:
                if old in prompt:
                    prompt = prompt.replace(old, new)
                    applied.append(old[:20])
        async with sem:
            try:
                choice = await generator.choose_tool(prompt, specs, purpose=d["purpose"])
            except TextGenerationError as e:
                skipped[f"生成に失敗: {str(e)[:40]}"] += 1
                return
        results.append({"purpose": d["purpose"], "at": d["at"], "tool": choice.name, "applied": applied})

    await asyncio.gather(*(one(d) for d in sample))

    print(f"\n== {args.label}: {len(results)} 件")
    for purpose in PURPOSES:
        rows = [r for r in results if r["purpose"] == purpose]
        if not rows:
            continue
        picks = sum(r["tool"] == ADVISOR for r in rows)
        print(f"{purpose}: {len(rows)} 件中 ask_advisor {picks} 件（{picks / len(rows):.0%}）")
        print("  ", dict(Counter(r["tool"] for r in rows).most_common(8)))
    if args.rewrite:
        print("書き換えが当たった数:", dict(Counter(a for r in results for a in r["applied"])))
    if skipped:
        print("数えなかった:", dict(skipped))
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
