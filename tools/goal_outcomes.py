#!/usr/bin/env python3
"""
小目標の成績を集計する（達成率、死亡、速さ）。人が画面を見て成否を決めなくてよいように、
ブリッジが世界から判定した終わり方（docs/design/82）をそのまま数える。

    python tools/goal_outcomes.py                       # logs/goal_ends を日ごとに
    python tools/goal_outcomes.py --by run              # 実行（commit、設定、モデル）ごとに
    python tools/goal_outcomes.py --by kind --since "2026-10-02 00:00"
    python tools/goal_outcomes.py --from-log data/logs/ailoveshen-dev.log data/logs/*.zip

記録の元:
- `logs/goal_ends/*.jsonl`: 小目標が終わるたびに 1 件（歩みの数えつき）。
- `logs/runs/*.jsonl`: プレイの始まりごとの条件。`--by run` で使う。
- `--from-log`: 記録が始まる前の分を、メインログの「目標: …（…）（前の目標の終わり）」行から補う。
  ステップ数は分からない（秒数は次の目標までの間隔）。記録のある期間は二重に数えない。

成功率は 達成 ÷ (達成 + 失敗 + 死亡)。切った（時間帯の変化、夜明け、考え直し、大目標の終わり）は
成功でも失敗でもないので別に数える。目標の難しさが日によって違うので、数字の差は「効いた」の証拠ではない
（変えた前後を、同じ種類の目標で比べる: --by kind）。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from ailoveshen.application.use_cases.goal_ends import CUT, DIED, FAILED, MET, end_category

# 1 つの小目標の所要時間として信じる上限。実測の最大は 52 分。これを超えるのは、再起動をまたいだ
# 古い記録（`resumed` を書く前の版は、止まっていた間を数えた）なので、秒数を使わない
MAX_GOAL_SECONDS = 3 * 3600
NO_RUN = "(実行の記録なし)"
NO_GOAL = "no goal yet"
START_LINE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| \w+ +\| [\w.]+:_start_goal:\d+ - 目標: (?P<body>.*)$"
)
SERVES_END = "のため）（"
KIND = re.compile(r"^([a-z_]+)\(\s*([^,)\s]*)")


def kind_of(goal: str) -> str:
    """目標の文から種類（数を無視した鍵）。have(food, 16) → have(food)。"""
    m = KIND.match(goal)
    if not m:
        return goal
    # at_home(dig_depth=0) のような名前つきの引数は、種類に入れない
    return f"{m.group(1)}({m.group(2)})" if m.group(2) and "=" not in m.group(2) else m.group(1)


def _local(text: str) -> datetime:
    """ISO の時刻を、この機械の時刻帯で（時刻帯がなければ、この機械のものとみなす）。"""
    return datetime.fromisoformat(text).astimezone()


def read_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """JSON Lines のファイルを全部読む（壊れた行は飛ばす）。"""
    rows: list[dict[str, Any]] = []
    for path in sorted(paths):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_entries(directory: Path) -> list[dict[str, Any]]:
    """`logs/goal_ends` の小目標の終わり。"""
    if not directory.is_dir():
        return []
    rows = read_jsonl(directory.glob("*.jsonl"))
    return [r for r in rows if r.get("event") == "goal_end"]


def load_runs(directory: Path) -> list[dict[str, Any]]:
    """`logs/runs` の実行の始まり（古い順）。"""
    if not directory.is_dir():
        return []
    rows = [
        r for r in read_jsonl(directory.glob("*.jsonl")) if r.get("event") == "run" and r.get("at")
    ]
    return sorted(rows, key=lambda r: _local(r["at"]))


def run_label(run: dict[str, Any]) -> str:
    """実行の見出し: 開始時刻、commit、未コミットの変更の指紋、モデル。"""
    git = run.get("git") or {}
    commit = (git.get("commit") or "????????")[:7]
    diff = f"+{git['diff']}" if git.get("dirty") and git.get("diff") else ""
    started = _local(run["at"]).strftime("%m-%d %H:%M")
    local = (run.get("local_llm") or {}).get("model")
    return f"{started} {commit}{diff} {run.get('control', '?')}/{run.get('jev_model', '?')}" + (
        f"/{local}" if local else ""
    )


def run_of(entry: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    """終わった時刻より前で一番新しい実行の見出し。"""
    ended = _local(entry["ended_at"])
    label = NO_RUN
    for run in runs:
        if _local(run["at"]) <= ended:
            label = run_label(run)
        else:
            break
    return label


def parse_log(text: str) -> list[dict[str, Any]]:
    """
    メインログの `_start_goal` の行から、終わった小目標を作る。

    1 行は「目標: <新しい目標>（<大目標>のため）（<前の小目標の終わり>） - …」。前の小目標の目標の文は
    前の行から、始まりの時刻も前の行から取る。
    """
    entries: list[dict[str, Any]] = []
    previous: tuple[datetime, str] | None = None
    for line in text.splitlines():
        m = START_LINE.match(line)
        if not m:
            continue
        at = datetime.strptime(m["time"], "%Y-%m-%d %H:%M:%S").astimezone()
        body = m["body"]
        end = body.find("） - ")
        prefix = body[:end] if end >= 0 else body
        goal = prefix.split("（", 1)[0]
        split = prefix.find(SERVES_END)
        reason = prefix[split + len(SERVES_END) :] if split >= 0 else ""
        if previous is not None and reason and reason != NO_GOAL:
            category = end_category(reason)
            entries.append(
                {
                    "event": "goal_end",
                    "source": "log",
                    "goal": previous[1],
                    "kind": kind_of(previous[1]),
                    "category": category,
                    "reason": reason,
                    "started_at": previous[0].isoformat(),
                    "ended_at": at.isoformat(),
                    "seconds": round((at - previous[0]).total_seconds(), 1),
                    "steps": None,
                    "deaths": 1 if category == DIED else 0,
                }
            )
        previous = (at, goal)
    return entries


def read_log(path: Path) -> str:
    """メインログ（.log、または回転した .zip の中の 1 つめ）。"""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            return z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    return path.read_text(encoding="utf-8", errors="replace")


def merge_log_entries(
    entries: list[dict[str, Any]], from_log: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """記録のある期間は記録を使い、それより前のログ由来の分だけ足す。"""
    if not entries:
        return sorted(from_log, key=lambda r: r["ended_at"])
    first = min(_local(e["started_at"]) for e in entries)
    older = [e for e in from_log if _local(e["ended_at"]) < first]
    return sorted([*older, *entries], key=lambda r: _local(r["ended_at"]))


def group_key(entry: dict[str, Any], by: str, runs: list[dict[str, Any]]) -> str:
    """集計の見出し。"""
    if by == "day":
        return _local(entry["ended_at"]).strftime("%Y-%m-%d")
    if by == "hour":
        return _local(entry["ended_at"]).strftime("%m-%d %H時")
    if by == "run":
        return run_of(entry, runs)
    if by == "kind":
        return entry.get("kind") or entry.get("predicate") or "?"
    if by == "predicate":
        return entry.get("predicate") or kind_of(entry.get("goal", "")).split("(")[0]
    if by == "source":
        return "生存" if entry.get("survival") else "大目標"
    return "全部"


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """1 つの見出しの数。"""
    count = {c: sum(1 for e in entries if e.get("category") == c) for c in (MET, FAILED, DIED, CUT)}
    judged = count[MET] + count[FAILED] + count[DIED]
    seconds = [
        e["seconds"]
        for e in entries
        if isinstance(e.get("seconds"), (int, float)) and 0 <= e["seconds"] <= MAX_GOAL_SECONDS
    ]
    steps = [e["steps"] for e in entries if isinstance(e.get("steps"), int)]
    hours = sum(seconds) / 3600
    deaths = sum(e["deaths"] for e in entries if isinstance(e.get("deaths"), int))
    return {
        "n": len(entries),
        **count,
        "success_rate": count[MET] / judged if judged else None,
        "met_per_hour": count[MET] / hours if hours else None,
        "deaths": deaths,
        "deaths_per_hour": deaths / hours if hours else None,
        "median_steps": statistics.median(steps) if steps else None,
        "median_seconds": statistics.median(seconds) if seconds else None,
        "hours": hours,
    }


def _fmt(value: float | None, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def render(groups: dict[str, dict[str, Any]]) -> str:
    """表にする。"""
    header = [
        "見出し",
        "件",
        "達成",
        "失敗",
        "死亡",
        "切",
        "成功率",
        "達成/時",
        "死亡/時",
        "歩(中央)",
        "秒(中央)",
    ]
    rows = [header]
    for name, s in groups.items():
        rows.append(
            [
                name,
                str(s["n"]),
                str(s[MET]),
                str(s[FAILED]),
                str(s[DIED]),
                str(s[CUT]),
                _fmt(None if s["success_rate"] is None else s["success_rate"] * 100, ".0f") + "%"
                if s["success_rate"] is not None
                else "-",
                _fmt(s["met_per_hour"], ".1f"),
                _fmt(s["deaths_per_hour"], ".2f"),
                _fmt(s["median_steps"], ".0f"),
                _fmt(s["median_seconds"], ".0f"),
            ]
        )
    widths = [max(_width(r[i]) for r in rows) for i in range(len(header))]
    lines = []
    for r in rows:
        lines.append("  ".join(cell + " " * (widths[i] - _width(cell)) for i, cell in enumerate(r)))
    return "\n".join(lines)


def _width(text: str) -> int:
    """表示幅（全角は 2）。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in text)


def analyse(
    entries: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    by: str,
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = None,
) -> dict[str, dict[str, Any]]:
    """条件で絞って、見出しごとに数える（見出しの並びは、最初に現れた順）。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        ended = _local(e["ended_at"])
        if (since and ended < since) or (until and ended >= until):
            continue
        if kind and not (e.get("kind") or "").startswith(kind):
            continue
        grouped[group_key(e, by, runs)].append(e)
    ordered = sorted(grouped) if by in ("kind", "predicate") else list(grouped)
    return {name: summarize(grouped[name]) for name in ordered}


def main() -> None:
    parser = argparse.ArgumentParser(description="小目標の成績を集計する")
    parser.add_argument("--goal-ends", type=Path, default=Path("logs/goal_ends"))
    parser.add_argument("--runs", type=Path, default=Path("logs/runs"))
    parser.add_argument(
        "--by",
        choices=["day", "hour", "run", "kind", "predicate", "source", "all"],
        default="day",
        help="集計の見出し（既定 day。kind は have(food) のように数を無視した目標の種類）",
    )
    parser.add_argument("--since", help="この時刻から（例: 2026-10-02 00:00。この機械の時刻）")
    parser.add_argument("--until", help="この時刻の前まで")
    parser.add_argument("--kind", help="この前置きで始まる種類だけ（例: have(food）")
    parser.add_argument(
        "--from-log",
        nargs="*",
        type=Path,
        default=[],
        help="記録の前の分を補うメインログ（.log / .zip）",
    )
    parser.add_argument("--json", action="store_true", help="表の代わりに JSON")
    args = parser.parse_args()

    entries = load_entries(args.goal_ends)
    if args.from_log:
        from_log: list[dict[str, Any]] = []
        for path in args.from_log:
            from_log.extend(parse_log(read_log(path)))
        entries = merge_log_entries(entries, from_log)
    runs = load_runs(args.runs)
    since = datetime.fromisoformat(args.since).astimezone() if args.since else None
    until = datetime.fromisoformat(args.until).astimezone() if args.until else None
    groups = analyse(entries, runs, args.by, since, until, args.kind)
    if not groups:
        print(
            "該当する小目標の終わりがありません（記録は、この版で動かした実行から。過去分は --from-log）"
        )
        return
    if args.json:
        print(json.dumps(groups, ensure_ascii=False, indent=2))
    else:
        print(render(groups))
        total = summarize(entries)
        print(
            f"\n全体 {total['n']} 件。達成/時は目標の秒数の合計で割る（止まっていた時間は含まない）。"
        )


if __name__ == "__main__":
    main()
