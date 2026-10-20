#!/usr/bin/env python3
"""
世界を作り直すとき、一度も効かなかった失敗由来の教訓を、作業用の教訓帳から取り分ける（docs/design/201 §8 項目 7 の (b)）。
`scripts/reset_world.sh` が呼ぶ。教訓は世界によらない前提で残してきたが、場所や地形の名前を含む未確認の教訓は、
新しい世界で誤って効く。

取り分ける教訓: 出どころが `failure`（自分の失敗から書いた）で、思い出して（`used > 0`）一度も効かなかった（`helped == 0`）もの。
残す教訓: 視聴者・運営に教わったもの、一度でも効いたもの、まだ一度も思い出していないもの（試していないので、間違いとは言えない）。取り分けたものは消さず、別のファイルに書く（戻せる）。

    python tools/set_aside_lessons.py --lessons data/lessons.json --out data/lessons-unproven-20261020.json [--dry-run]

配信（教訓帳をメモリに持って上書きする）が動いていない間に使う。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

FAILURE = "failure"


def is_unproven(lesson: dict[str, Any]) -> bool:
    """失敗から書いた教訓で、思い出して試したのに一度も効かなかったか（思い出していないものは、まだ分からない）。"""
    return lesson.get("source") == FAILURE and int(lesson.get("used", 0)) > 0 and int(lesson.get("helped", 0)) == 0


def split(lessons: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(残す教訓, 取り分ける教訓)。順番は元のまま。"""
    kept = [n for n in lessons if not is_unproven(n)]
    aside = [n for n in lessons if is_unproven(n)]
    return kept, aside


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lessons", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="取り分けた教訓を書くファイル（あれば断る）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.lessons.exists():
        print(f"（{args.lessons} はない。取り分けるものはない）", file=sys.stderr)
        return 0
    data = json.loads(args.lessons.read_text(encoding="utf-8"))
    kept, aside = split(data["lessons"])
    print(f"教訓 {len(data['lessons'])} 件のうち {len(aside)} 件を取り分ける（思い出して一度も効かなかった）。残す {len(kept)} 件")
    if args.dry_run or not aside:
        return 0
    if args.out.exists():
        print(f"エラー: {args.out} がもうある", file=sys.stderr)
        return 1
    args.out.write_text(
        json.dumps({"lessons": aside, "next_id": data["next_id"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.lessons.write_text(
        json.dumps({**data, "lessons": kept}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
