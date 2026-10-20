"""
LLM のリクエストとレスポンスの健康診断（監視用）。

`logs/generations/*.jsonl`（呼び出しの記録。設計書 90）と `logs/stream.log` から、用途ごとに数える:

  - 呼び出しの数、応答時間（平均・95 パーセンタイル・最大）、遅い呼び出し
  - プロンプトのトークン数、キャッシュに当たった割合（cached / prompt）、書き込み
  - 出力のトークン数、思考を使い切った・途切れた呼び出し（finish_reason が length）
  - 答えが空（道具も文も返らない）の呼び出し、エラーの記録
  - ログ側: スキーマに合わず書き直させた回数、response_format を断られた回数、LLM のエラー

  python tools/llm_health.py                       # 直近 60 分
  python tools/llm_health.py --minutes 180
  python tools/llm_health.py --since "2026-10-17 22:41"   # ローカル時刻（JST）。再起動の時刻から
  python tools/llm_health.py --slow 20             # この秒数を超えた呼び出しを個別に出す
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATIONS = ROOT / "logs" / "generations"
STREAM_LOG = ROOT / "logs" / "stream.log"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
JST = timezone(timedelta(hours=9))
LOG_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def read_generations(since: datetime) -> list[dict]:
    rows = []
    for path in sorted(GENERATIONS.glob("gen-*.jsonl")):
        stamp = path.stem.removeprefix("gen-")
        try:
            started = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if started < since - timedelta(days=1):
            continue
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    at = datetime.fromisoformat(row["at"])
                except (ValueError, KeyError):
                    continue
                if at >= since:
                    row["_at"] = at
                    rows.append(row)
    return rows


def summarize(rows: list[dict], slow_seconds: float) -> None:
    by = defaultdict(list)
    for r in rows:
        by[r.get("purpose") or "default"].append(r)
    print(f"{'purpose':22} {'n':>5} {'avg s':>6} {'p95 s':>6} {'max s':>6} {'prompt':>7} {'cache%':>6} {'out':>5} {'length':>6} {'empty':>5} {'err':>4}")
    total_prompt = total_cached = 0
    slow = []
    for purpose, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        secs = [r.get("elapsed_ms", 0) / 1000 for r in items]
        tokens = [r.get("tokens") or {} for r in items]
        prompt = [t.get("prompt") or 0 for t in tokens]
        cached = [t.get("cached") or 0 for t in tokens]
        out = [t.get("output") or 0 for t in tokens]
        length = sum(1 for r in items if r.get("finish_reason") == "length")
        empty = sum(1 for r in items if not r.get("output") and not r.get("tool_calls") and not r.get("error"))
        errors = sum(1 for r in items if r.get("error"))
        total_prompt += sum(prompt)
        total_cached += sum(cached)
        ratio = 100 * sum(cached) / sum(prompt) if sum(prompt) else 0
        print(
            f"{purpose:22} {len(items):>5} {statistics.mean(secs):>6.1f} {_percentile(secs, 0.95):>6.1f} {max(secs):>6.1f} "
            f"{statistics.mean(prompt):>7.0f} {ratio:>5.0f}% {statistics.mean(out):>5.0f} {length:>6} {empty:>5} {errors:>4}"
        )
        slow += [(s, purpose, r) for s, r in zip(secs, items) if s >= slow_seconds]
    if total_prompt:
        print(f"\nprompt tokens {total_prompt:,}, cache hit {100 * total_cached / total_prompt:.0f}%")
    reasons = Counter(r.get("finish_reason") for r in rows)
    print("finish_reason:", dict(reasons))
    if slow:
        print(f"\n{slow_seconds:.0f} 秒以上の呼び出し:")
        for s, purpose, r in sorted(slow, reverse=True)[:12]:
            at = r["_at"].astimezone(JST).strftime("%H:%M:%S")
            print(f"  {at} {purpose:20} {s:5.1f}s prompt={(r.get('tokens') or {}).get('prompt')} think={r.get('thinking_level')}")


def log_events(since_local: datetime) -> None:
    if not STREAM_LOG.exists():
        return
    counts: Counter[str] = Counter()
    samples: dict[str, str] = {}
    rules = [
        ("スキーマに合わず書き直し", re.compile(r"JSON を書き直させる（(\w+)）")),
        ("スキーマに最後まで合わない", re.compile(r"JSON がまだスキーマに合わない（(\w+)）")),
        ("response_format を断られた", re.compile(r"（(\w+)） response_format を受け付けない")),
        ("思考が上限に届かず聞き直し", re.compile(r"思考が上限.*（(\w+)）")),
        ("LLM エラー", re.compile(r"LLM (?:error|request failed)[^:]*")),
        ("目標の決定を差し戻し", re.compile(r"目標の決定 \d+ を差し戻した")),
        ("計画を差し戻し", re.compile(r"計画 .* を差し戻した")),
        ("ステップ失敗（LLM）", re.compile(r"ステップが失敗した（TextGenerationError")),
        ("ステップ失敗（ブリッジ）", re.compile(r"ステップが失敗した（GameBridgeError")),
    ]
    with STREAM_LOG.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = ANSI.sub("", raw)
            m = LOG_TIME.match(line)
            if not m or datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") < since_local:
                continue
            for name, pattern in rules:
                hit = pattern.search(line)
                if hit:
                    key = f"{name} {hit.group(1)}" if hit.groups() else name
                    counts[key] += 1
                    samples.setdefault(key, line.strip()[:200])
    print("\nログの出来事:")
    if not counts:
        print("  なし")
    for key, n in counts.most_common():
        print(f"  {n:>4}  {key}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--since", help='ローカル時刻 "YYYY-MM-DD HH:MM"（JST）。指定すると --minutes は使わない')
    parser.add_argument("--slow", type=float, default=20.0, help="個別に出す遅い呼び出しの秒数")
    args = parser.parse_args()
    if args.since:
        since_local = datetime.strptime(args.since, "%Y-%m-%d %H:%M")
        since = since_local.replace(tzinfo=JST).astimezone(timezone.utc)
    else:
        since = datetime.now(timezone.utc) - timedelta(minutes=args.minutes)
        since_local = since.astimezone(JST).replace(tzinfo=None)
    rows = read_generations(since)
    print(f"{since_local:%Y-%m-%d %H:%M} 以降の LLM 呼び出し {len(rows)} 件\n")
    if rows:
        summarize(rows, args.slow)
    log_events(since_local)


if __name__ == "__main__":
    main()
