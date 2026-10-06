"""
ローカル LLM のモデルごとに、失敗の多さと速さを比べる（モデルを切り替えたあとの A/B 用）。

メインのログ（data/logs/ailoveshen-dev.log と、その zip）の `Local <model>（thinking …）: …ms, purpose=…` の行から、
モデルが同じ間の区間を作り（間が 30 分あいたら別の区間）、区間ごとに同じ指標を数える。

  python tools/model_compare.py                     # 呼び出しが 150 回以上の区間を並べる
  python tools/model_compare.py --merge             # モデルごとに全区間を合わせる
  python tools/model_compare.py --since "2026-10-04 13:04"   # その時刻から（再起動の時刻。それ以前のログは使わない）
  python tools/model_compare.py --min-calls 50
  python tools/model_compare.py --exclude "2026-10-04 12:16" "2026-10-04 12:55"   # 止まっていた時間を除く（何度でも）

LLM につながらなかった時間（ConnectTimeout など）は、自動で除く。コードの修正は再起動で反映されるので、
比べるときは「最後の修正を入れた再起動」以降で区切る。

数えるもの（すべてメインのログから。区間の長さがちがうので、手 100 回あたり / 呼び出し 100 回あたりにそろえる）:
  - 手の失敗率: `_play_steps` の行の 成功 / 失敗（`invalid call` は別に数える）
  - モデルの書き損じ: スキーマに合わず書き直させた回数、最後まで合わなかった回数
  - 差し戻し: 目標の決定、計画（コードの規則に合わず、書き直しに回した回数）
  - 手を決めたのが LLM か Jev か
  - 速さ: 目的ごとの平均応答時間、手と手の間隔の中央値
  - 結果: logs/goal_ends の達成・失敗・死亡（コードの版もちがうので、参考）
モデルの失敗ではないもの（接続切れ、ブリッジが落ちた）は「環境」として別に数え、率には入れない。
"""

from __future__ import annotations

import argparse
import re
import statistics
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
GOAL_ENDS_DIR = ROOT / "logs" / "goal_ends"
RUN_GAP = timedelta(minutes=30)
JST = timedelta(hours=9)

USAGE = re.compile(r"^(\S+ \S+) \|.*?Local ([^（:]*)（thinking (?:off|on)）: (\d+)ms, purpose=(\S+)")
STEP = re.compile(r"^(\S+ \S+) \|.*_play_steps:\d+ - \[\d+\] .*?（確信度 [\d.]+）: (成功|失敗)(.*)$")
STEP_SECONDS = re.compile(r"（([\d.]+)秒）\s*$")
DECIDED = re.compile(r"^(\S+ \S+) \|.*_jev_step:\d+ - 1 手は (Jev が選んだ|Gemini が決める)")
REWRITE = re.compile(r"^(\S+ \S+) \|.*ローカルの JSON を書き直させる（(\S+?)）")
SCHEMA_FAIL = re.compile(r"^(\S+ \S+) \|.*ローカルの JSON がまだスキーマに合わない（(\S+?)）")
GOAL_BACK = re.compile(r"^(\S+ \S+) \|.*_decide_goal:\d+ - 目標の決定 \d+ を差し戻した")
PLAN_BACK = re.compile(r"^(\S+ \S+) \|.*job_planner:plan:\d+ - 計画 .* の試行 \d+ を差し戻した")
STEP_ERROR = re.compile(r"^(\S+ \S+) \|.*_back_off:\d+ - ステップが失敗した（(.*)")
WATCH_DROP = re.compile(r"^(\S+ \S+) \|.*見張りの質問を捨てた")

# 環境の失敗: モデルの出来とは関係がない
ENVIRONMENT = ("ConnectTimeout", "ReadError", "ConnectError", "bridge unreachable", "GameBridgeError")
GOAL_PURPOSES = ("goal", "goal_after_failure")
STEP_PURPOSES = ("tool", "tool_after_failure", "tool_stuck")


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def timestamp(line: str) -> datetime | None:
    if len(line) < 19 or not line[:4].isdigit():
        return None
    try:
        return parse_time(line[:19])
    except ValueError:
        return None


def find_outages(lines: list[str]) -> list[tuple[datetime, datetime]]:
    """LLM やブリッジにつながらなかった時間: 環境のエラーが続いた間（間が 10 分あいたら別）から、次に LLM が答えた行まで。"""
    errors = [t for line in lines if any(k in line for k in ENVIRONMENT) and "WARNING" in line or "ERROR" in line and any(k in line for k in ENVIRONMENT)
              if (t := timestamp(line))]
    usage = [t for line in lines if USAGE.match(line) and (t := timestamp(line))]
    outages: list[tuple[datetime, datetime]] = []
    for t in errors:
        if outages and t - outages[-1][1] < timedelta(minutes=10):
            outages[-1] = (outages[-1][0], t)
        else:
            outages.append((t, t))
    closed = []
    for start, last in outages:
        after = next((u for u in usage if u > last), last)
        closed.append((start, after))
    return closed


def restrict(lines: list[str], since: datetime | None, skipped: list[tuple[datetime, datetime]]) -> list[str]:
    """since より前と、除く時間のログ行を落とす（時刻のない行は、直前の行についていく）。"""
    out, keep = [], True
    for line in lines:
        t = timestamp(line)
        if t is not None:
            keep = (since is None or t >= since) and not any(a <= t <= b for a, b in skipped)
        if keep:
            out.append(line)
    return out


def read_logs() -> list[str]:
    """zip になった古いログから順に、今のログまで。"""
    lines: list[str] = []
    for z in sorted(LOG_DIR.glob("*.zip")):
        with zipfile.ZipFile(z) as f:
            for name in f.namelist():
                lines.extend(f.read(name).decode("utf8", "replace").splitlines())
    current = LOG_DIR / "ailoveshen-dev.log"
    if current.exists():
        lines.extend(current.read_text(encoding="utf8", errors="replace").splitlines())
    return lines


@dataclass
class Run:
    model: str
    start: datetime
    end: datetime
    calls: int = 0
    latency: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def contains(self, t: datetime) -> bool:
        return self.start <= t <= self.end


def build_runs(lines: list[str]) -> list[Run]:
    runs: list[Run] = []
    cur: Run | None = None
    for line in lines:
        m = USAGE.match(line)
        if not m:
            continue
        t, model, ms, purpose = parse_time(m.group(1)), m.group(2).strip(), int(m.group(3)), m.group(4)
        if cur and cur.model == model and t - cur.end < RUN_GAP:
            cur.end = t
        else:
            cur = Run(model=model, start=t, end=t)
            runs.append(cur)
        cur.calls += 1
        cur.latency[purpose].append(ms / 1000)
    return runs


def merge(runs: list[Run]) -> list[Run]:
    """モデルごとに全区間をひとつにする（区間の外の時間は数えない: contains は使わず、区間のリストを持つ）。"""
    by: dict[str, list[Run]] = defaultdict(list)
    for r in runs:
        by[r.model].append(r)
    out = []
    for model, rs in by.items():
        total = Run(model=model, start=min(r.start for r in rs), end=max(r.end for r in rs))
        total.calls = sum(r.calls for r in rs)
        for r in rs:
            for p, v in r.latency.items():
                total.latency[p].extend(v)
        total.parts = rs  # type: ignore[attr-defined]
        out.append(total)
    return out


def goal_end_rows() -> list[tuple[datetime, dict]]:
    import json

    rows = []
    for f in sorted(GOAL_ENDS_DIR.glob("*.jsonl")):
        for line in f.read_text(encoding="utf8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                rows.append((parse_time(r["at"]).replace(tzinfo=None) + JST, r))
            except (ValueError, KeyError):
                continue
    return rows


def stats_for(run: Run, lines: list[str], ends: list[tuple[datetime, dict]]) -> dict:
    parts = getattr(run, "parts", [run])
    inside = lambda t: any(p.contains(t) for p in parts)  # noqa: E731
    s: dict = Counter()
    minutes: set[str] = set()
    gaps: list[float] = []
    last_step: datetime | None = None
    for line in lines:
        if len(line) < 20 or not line[:4].isdigit():
            continue
        try:
            t = parse_time(line[:19])
        except ValueError:
            continue
        if not inside(t):
            last_step = None
            continue
        minutes.add(line[:16])
        if m := STEP.match(line):
            s["steps"] += 1
            tail = m.group(3)
            if m.group(2) == "失敗":
                s["step_failed"] += 1
                if "invalid call" in tail:
                    s["invalid_call"] += 1
            if last_step is not None:
                gap = (t - last_step).total_seconds()
                if gap < 300:
                    gaps.append(gap)
            last_step = t
        elif DECIDED.match(line):
            s["by_jev" if "Jev が選んだ" in line else "by_llm"] += 1
        elif m := REWRITE.match(line):
            s["rewrite"] += 1
        elif SCHEMA_FAIL.match(line):
            s["schema_fail"] += 1
        elif GOAL_BACK.match(line):
            s["goal_back"] += 1
        elif PLAN_BACK.match(line):
            s["plan_back"] += 1
        elif WATCH_DROP.match(line):
            s["watch_drop"] += 1
        elif m := STEP_ERROR.match(line):
            if any(k in m.group(2) for k in ENVIRONMENT):
                s["environment"] += 1
            elif "no acceptable goal" in m.group(2):
                s["no_goal"] += 1
            else:
                s["step_error"] += 1
    outcomes = Counter(r.get("category") for t, r in ends if inside(t))
    hours = len(minutes) / 60  # ログ行のある分の数（除いた時間はログから落ちているので入らない）
    s["outcomes"] = outcomes
    s["hours"] = hours
    s["gap_median"] = statistics.median(gaps) if gaps else 0.0
    return s


def per(n: float, d: float) -> str:
    return f"{100 * n / d:5.1f}" if d else "    -"


def show(runs: list[Run], lines: list[str], ends: list[tuple[datetime, dict]]) -> None:
    for run in runs:
        s = stats_for(run, lines, ends)
        steps, calls = s["steps"], run.calls
        goal_calls = sum(len(run.latency.get(p, [])) for p in GOAL_PURPOSES)
        plan_calls = len(run.latency.get("plan", []))
        decided = s["by_jev"] + s["by_llm"]
        o = s["outcomes"]
        done = o["met"] + o["failed"] + o["died"]
        print(f"\n■ {run.model}  {run.start:%m-%d %H:%M} → {run.end:%m-%d %H:%M}  （{s['hours']:.1f} 時間分、LLM {calls} 回、手 {steps} 回）")
        print(f"  手の失敗率              {per(s['step_failed'], steps)} %   （うち invalid call {s['invalid_call']} 回 = 手 100 回あたり {per(s['invalid_call'], steps).strip()}）")
        print(f"  スキーマの書き直し       {per(s['rewrite'], calls)} /100 呼び出し   （最後まで合わず {s['schema_fail']} 回）")
        print(f"  目標の決定の差し戻し     {per(s['goal_back'], goal_calls)} /100 決定呼び出し （{s['goal_back']} 回 / {goal_calls} 回）  決められず失敗した手 {s['no_goal']} 回")
        print(f"  計画の差し戻し           {per(s['plan_back'], plan_calls)} /100 計画呼び出し （{s['plan_back']} 回 / {plan_calls} 回）")
        print(f"  見張りの質問の捨て       {s['watch_drop']} 回")
        print(f"  手を LLM が決めた割合    {per(s['by_llm'], decided)} %   （Jev が選んだ {s['by_jev']}、LLM {s['by_llm']}）")
        speed = "  ".join(f"{p} {statistics.mean(run.latency[p]):.1f}s" for p in (*STEP_PURPOSES, "plan", *GOAL_PURPOSES) if run.latency.get(p))
        print(f"  応答時間（平均）         {speed}")
        print(f"  手と手の間隔（中央値）    {s['gap_median']:.1f} 秒")
        if done:
            print(f"  ゴールの結果（参考）      達成 {o['met']} / 失敗 {o['failed']} / 死亡 {o['died']} / 中断 {o['cut']}   成功率 {per(o['met'], done).strip()} %   死亡 {o['died'] / s['hours']:.2f} /時")
        print(f"  環境の失敗（率に入れない）  {s['environment']} 回    手の失敗（その他の例外）{s['step_error']} 回")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-calls", type=int, default=150, help="これより呼び出しが少ない区間は出さない")
    parser.add_argument("--merge", action="store_true", help="モデルごとに全区間を合わせる")
    parser.add_argument("--since", help="この日時以降のログだけ（例 2026-10-03、\"2026-10-04 13:04\"）")
    parser.add_argument("--exclude", nargs=2, action="append", metavar=("FROM", "TO"), default=[], help="この間のログを使わない（何度でも）")
    parser.add_argument("--keep-outages", action="store_true", help="LLM につながらなかった時間を除かない")
    args = parser.parse_args()

    lines = read_logs()
    skipped = [(parse_time(a), parse_time(b)) for a, b in args.exclude]
    if not args.keep_outages:
        outages = find_outages(lines)
        skipped += outages
        for a, b in outages:
            if not args.since or b >= parse_time(args.since):
                print(f"除いた（LLM かブリッジが応答しなかった）: {a:%m-%d %H:%M} → {b:%H:%M}（{(b - a).total_seconds() / 60:.0f} 分）")
    lines = restrict(lines, parse_time(args.since) if args.since else None, skipped)
    runs = build_runs(lines)
    runs = [r for r in runs if r.calls >= args.min_calls]
    if args.merge:
        runs = merge(runs)
    show(runs, lines, goal_end_rows())
    print("\n注意: コードの修正は再起動で反映されるので、区間の途中で版が変わる。比べるときは --since で、最後の修正を入れた再起動以降に区切る。"
          "区間が短いと（手が 300 回に満たないと）率は動く。")


if __name__ == "__main__":
    main()
