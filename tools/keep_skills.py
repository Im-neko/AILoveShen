#!/usr/bin/env python3
"""
世界を作り直すとき、よく使われて成功率の高い技だけを、新しい世界に残す（docs/design/201 §10）。
`scripts/reset_world.sh` が、技のフォルダを取り分けたあとに呼ぶ。技は世界についての覚えとして丸ごと
取り分けてきたが、座標を持たない汎用の技（例: dig_out_of_pit）まで一から覚え直すのは無駄。

残す技: 今の版（一番新しい版）が 100 回以上使われて、成功率が 95% を超えるもの（ユーザーの指示、2026-10-20）。
技のファイル（成績つき）をそのまま写す。残さない技は、取り分けたフォルダに残る。

    python tools/keep_skills.py --from minecraft-bridge/data/skills-20261020 --to minecraft-bridge/data/skills [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

MIN_USES = 100
MIN_RATE = 0.95  # これを超えるもの（等しいだけでは残さない）


def keeps(skill: dict[str, Any]) -> bool:
    """今の版が、十分に使われて、よく成功しているか。"""
    versions = skill.get("versions") or []
    if not versions:
        return False
    now = versions[-1]
    uses = int(now.get("uses", 0))
    return uses >= MIN_USES and int(now.get("successes", 0)) / uses > MIN_RATE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path, required=True)
    parser.add_argument("--to", dest="dest", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.source.is_dir():
        print(f"（{args.source} はない。残す技はない）", file=sys.stderr)
        return 0
    kept = []
    for path in sorted(args.source.glob("*.json")):
        try:
            skill = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if keeps(skill):
            kept.append(path)
    names = ", ".join(p.stem for p in kept) or "なし"
    print(f"技 {len(list(args.source.glob('*.json')))} 件のうち {len(kept)} 件を残す（{MIN_USES} 回以上で成功率 {MIN_RATE:.0%} 超）: {names}")
    if args.dry_run:
        return 0
    if kept:
        args.dest.mkdir(parents=True, exist_ok=True)
        for path in kept:
            shutil.copy2(path, args.dest / path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
