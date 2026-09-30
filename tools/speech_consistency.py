#!/usr/bin/env python3
"""
発言と行動の一貫性を数える（docs/design/65 §2）。視聴者が混乱する 2 つを分けて数える:

- 1（言ったことと違う行動）: 予告した小目標のその後（`forecasts`）と、話した文が今の小目標・行動と
  食い違うこと（`lines` の contradicts_plan）
- 2（今の状態と違うことを言う）: 家の中・外、済んだ・済んでいない（`lines` の claims_*）

    python tools/speech_consistency.py forecasts [--logs logs] [--since 20260929]
    python tools/speech_consistency.py lines [--logs logs] [--since 20260930] [--judge jev]

`forecasts` は LLM を使わない。`logs/commentary`（話した実況の出来事）と `logs/steps`（行動）から、
「新しい小目標」を話した後、その小目標で行動が始まったか、達成で終わったか、未達成でやめたかを数える。
`lines` は `logs/spoken`（話した文と、そのときの状態。設計書 65 §2）を読み、`--judge jev` なら文を
配信中の照合（SpeechCheck）と同じ質問で Jev に聞いて、状態と食い違う文を数える。Jev を使わないときは、
照合が記録した結果（`event: check`）と、実際に読まれた数だけ。1 時間ごと（UTC）の表で出す。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

NEW_GOAL = re.compile(r"^新しい小目標[^:：]*[:：]\s*(?P<goal>.+?)（")
ENDED_GOAL = re.compile(r"^小目標 (?P<goal>.+?) が(?P<result>達成|未達成でやめた)（(?P<why>.*)）$")
ACT_MARGIN = timedelta(seconds=5)  # 実況の行は決めた直後に書かれ、最初の行動と同じ秒に並ぶ
END_MARGIN = timedelta(seconds=2)
QUICK_SECONDS = 60


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def read_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


@dataclass
class Forecast:
    """話した新しい小目標 1 つ。"""

    goal: str
    said_at: datetime
    spoken: bool
    ended_at: Optional[datetime] = None
    met: Optional[bool] = None
    why: str = ""
    steps: int = 0
    ok_steps: int = 0
    first_ok_after: Optional[float] = None
    results_recorded: bool = False  # 行動の結果の記録がある実行か（古い記録は結果の行がない）

    @property
    def abandoned(self) -> bool:
        return self.ended_at is not None and self.met is False

    @property
    def life_seconds(self) -> Optional[float]:
        return (self.ended_at - self.said_at).total_seconds() if self.ended_at else None


def forecasts_of(
    commentary: list[dict[str, Any]], steps: list[dict[str, Any]]
) -> list[Forecast]:
    """
    実況の行（`logs/commentary`）から、話した新しい小目標を拾って、その後を行動（`logs/steps`）と突き合わせる。
    話さなかった行の出来事は次の行に持ち越されるので、同じ出来事は 1 つの小目標として数える。
    """
    picks = [
        (_time(r["at"]), r["goal"])
        for r in steps
        if "goal" in r and r.get("event") in (None, "pick")
    ]
    results: list[tuple[datetime, str, bool]] = []
    current = None
    for r in steps:  # 新しい形の記録: 結果の行は直前の pick の小目標のもの
        if r.get("event") == "pick":
            current = r.get("goal")
        elif r.get("event") == "result" and current is not None:
            results.append((_time(r["at"]), current, bool(r.get("ok"))))

    found: list[Forecast] = []
    open_by_goal: dict[str, Forecast] = {}
    for row in commentary:
        at = _time(row["at"])
        speak = bool(row.get("speak"))
        for event in row.get("events", []):
            new = NEW_GOAL.match(event)
            if new:
                goal = new["goal"]
                known = open_by_goal.get(goal)
                if known is not None:
                    known.spoken = known.spoken or speak
                    continue
                forecast = Forecast(goal=goal, said_at=at, spoken=speak)
                open_by_goal[goal] = forecast
                found.append(forecast)
                continue
            ended = ENDED_GOAL.match(event)
            if ended:
                forecast = open_by_goal.pop(ended["goal"], None)
                if forecast is not None and forecast.ended_at is None:
                    forecast.ended_at = at
                    forecast.met = ended["result"] == "達成"
                    forecast.why = ended["why"]

    for f in found:
        f.results_recorded = bool(results)
        end = (f.ended_at + END_MARGIN) if f.ended_at else f.said_at + timedelta(minutes=10)
        lo = f.said_at - ACT_MARGIN
        f.steps = sum(1 for at, goal in picks if lo <= at <= end and goal == f.goal)
        ok = [at for at, goal, good in results if lo <= at <= end and goal == f.goal and good]
        f.ok_steps = len(ok)
        if ok:
            f.first_ok_after = (min(ok) - f.said_at).total_seconds()
    return found


def _percent(n: int, total: int) -> str:
    return f"{n} ({100 * n / total:.0f}%)" if total else f"{n}"


def _why_kind(why: str) -> str:
    if "time of day changed" in why:
        return "time of day changed"
    if "stalled" in why:
        return "stalled"
    if "is stuck" in why:
        return "stuck"
    if "ran for" in why:
        return "over budget"
    return why[:40]


def report_forecasts(found: list[Forecast]) -> str:
    said = [f for f in found if f.spoken]
    ended = [f for f in said if f.ended_at is not None]
    abandoned = [f for f in ended if f.abandoned]
    lines = [
        f"話した新しい小目標: {len(said)} 件（終わりが分かる: {len(ended)}）",
        f"  未達成でやめた: {_percent(len(abandoned), len(ended))}"
        f"、うち {QUICK_SECONDS} 秒以内: {_percent(sum((f.life_seconds or 0) < QUICK_SECONDS for f in abandoned), len(abandoned))}",
        f"  行動が 1 つもないまま終わった: {_percent(sum(f.steps == 0 for f in ended), len(ended))}",
    ]
    reasons = Counter(_why_kind(f.why) for f in abandoned)
    if reasons:
        lines.append("  やめた理由: " + "、".join(f"{k} {v}" for k, v in reasons.most_common(5)))
    firsts = [f.first_ok_after for f in ended if f.first_ok_after is not None]
    if firsts:
        firsts.sort()
        lines.append(
            f"  話してから最初の成功まで（結果が記録された分 {len(firsts)} 件）: "
            f"中央値 {statistics.median(firsts):.0f} 秒、90% {firsts[int(len(firsts) * 0.9)]:.0f} 秒"
        )
        recorded = [f for f in ended if f.steps and f.results_recorded]
        no_success = [f for f in recorded if f.ok_steps == 0]
        lines.append(
            f"  行動はあったが 1 つも成功しないまま終わった: {_percent(len(no_success), len(recorded))}"
        )
    by_day: dict[str, list[Forecast]] = defaultdict(list)
    for f in ended:
        by_day[f.said_at.strftime("%Y-%m-%d")].append(f)
    for day, items in sorted(by_day.items()):
        lines.append(
            f"  {day}: {len(items)} 件、未達成 {sum(f.abandoned for f in items)}"
            f"、行動なし {sum(f.steps == 0 for f in items)}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# lines: 話した文を、話したときの状態と照らす
# ---------------------------------------------------------------------------


@dataclass
class Line:
    at: datetime
    text: str
    source: str
    state: dict[str, Any]
    heard: bool = True  # 読み上げが読んだか（読み上げの記録がなければ、読んだことにする）
    type1: bool = False
    type2: bool = False
    problems: tuple[str, ...] = ()
    answers: dict[str, list[Any]] = field(default_factory=dict)


def lines_of(rows: list[dict[str, Any]]) -> list[Line]:
    """`logs/spoken` の行を、読まれた実況・一言（notice、返事、管理画面は除く）に絞ってまとめる。"""
    tts = {(r["text"], r.get("source")): r["outcome"] for r in rows if r.get("event") == "tts"}
    out = []
    for r in rows:
        if r.get("event") != "line" or r.get("source") != "commentary" or r.get("outcome") != "said":
            continue
        line = Line(at=_time(r["at"]), text=r["text"], source="commentary", state=r.get("state", {}))
        outcome = tts.get((r["text"].strip(), "commentary"))
        line.heard = outcome in (None, "played")
        out.append(line)
    return out


def verdicts_of(rows: list[dict[str, Any]]) -> Counter[str]:
    """
    配信中の照合の結果（ok / rewrite / dropped / unchecked）の数。作り直した文と言わなかった文は、
    `line` の記録を持たない（読み上げていない）ので、`check` の行から直接数える。
    """
    return Counter(r["verdict"] for r in rows if r.get("event") == "check")


TYPE2 = ("claims_inside_home", "claims_outside_home", "claims_goal_done")


def classify(line: Line, answers: dict[str, list[Any]], min_confidence: float = 0.75) -> None:
    """Jev の答えを、配信中の照合と同じ比べ方で状態と比べて、1 と 2 に分ける。"""
    from ailoveshen.application.use_cases.speech_check import problems_of

    line.answers = answers
    line.problems = problems_of(answers, line.state, min_confidence)
    line.type1 = any(p.startswith("今の小目標は") for p in line.problems)
    line.type2 = any(not p.startswith("今の小目標は") for p in line.problems)


async def judge_lines(lines: list[Line], concurrency: int = 4) -> None:
    from ailoveshen.application.use_cases.speech_check import SpeechCheck
    from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import JevFastJudge
    from ailoveshen.infrastructure.config import load_settings

    settings = load_settings()
    if not settings.jev.api_key:
        raise SystemExit("TYPESAFE_API_KEY がない（.env）: Jev で判定できない。--judge none で数える")
    judge = JevFastJudge(
        api_key=settings.jev.api_key,
        model=settings.jev.model,
        timeout_seconds=settings.jev.timeout_seconds,
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def one(line: Line) -> None:
        state = line.state
        questions = SpeechCheck.questions(
            has_home="inside_home" in state,
            has_goal="current_goal" in state,
            goal_met=bool(state.get("goal_met")),
        )
        if not questions:
            return
        async with semaphore:
            try:
                verdict = await judge.ask({"line": line.text, **state}, questions)
            except Exception as e:  # noqa: BLE001 - 1 行の失敗で数えるのを止めない
                print(f"判定できなかった: {line.text[:30]}（{e}）", file=sys.stderr)
                return
        classify(
            line,
            {
                name: [a.value, round(float(a.confidence), 3)]
                for name, a in verdict.answers.items()
            },
        )

    try:
        await asyncio.gather(*(one(line) for line in lines))
    finally:
        await judge.close()


def report_lines(
    lines: list[Line], judged: bool, verdicts: Optional[Counter[str]] = None
) -> str:
    if not lines and not verdicts:
        return "読まれた実況・一言の記録がない（logs/spoken）"
    heard = [line for line in lines if line.heard]
    out = [f"話した実況・一言: {len(lines)} 件、読まれた: {len(heard)} 件"]
    if verdicts:
        out.append("配信中の照合: " + "、".join(f"{k} {v}" for k, v in verdicts.most_common()))
    if judged:
        out.append(
            f"状態と食い違う（Jev の判定）: 1 予告と違う {_percent(sum(l.type1 for l in heard), len(heard))}"
            f"、2 今の状態と違う {_percent(sum(l.type2 for l in heard), len(heard))}"
        )
    hours: dict[str, list[Line]] = defaultdict(list)
    for line in heard:
        hours[line.at.strftime("%Y-%m-%d %H:00")].append(line)
    out.append("時間（UTC）  件数  1  2" if judged else "時間（UTC）  件数")
    for hour, items in sorted(hours.items()):
        if judged:
            out.append(f"{hour}  {len(items):4d}  {sum(l.type1 for l in items):2d}  {sum(l.type2 for l in items):2d}")
        else:
            out.append(f"{hour}  {len(items):4d}")
    if judged:
        for line in heard:
            if line.problems:
                out.append(f"  - {line.at.strftime('%H:%M:%S')} 「{line.text}」→ {'；'.join(line.problems)}")
    return "\n".join(out)


def _started(path: Path) -> datetime:
    return datetime.strptime(path.stem.removeprefix("watch-"), "%Y%m%dT%H%M%SZ")


def _sibling(directory: Path, path: Path) -> Optional[Path]:
    """同じ実行の別の記録（ファイル名の開始時刻が数秒ずれることがある）。"""
    started = _started(path)
    for other in directory.glob("watch-*.jsonl"):
        if abs((_started(other) - started).total_seconds()) <= 10:
            return other
    return None


def _files(directory: Path, since: Optional[str]) -> list[Path]:
    paths = sorted(directory.glob("watch-*.jsonl"))
    if since:
        paths = [p for p in paths if p.stem.removeprefix("watch-") >= since]
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="発言と行動の一貫性を数える")
    parser.add_argument("mode", choices=("forecasts", "lines"))
    parser.add_argument("--logs", type=Path, default=Path("logs"), help="ログのディレクトリ")
    parser.add_argument("--since", help="この日時のファイルから（例: 20260929 や 20260930T05）")
    parser.add_argument("--judge", choices=("jev", "none"), default="none", help="lines の判定")
    args = parser.parse_args()

    if args.mode == "forecasts":
        found: list[Forecast] = []
        for commentary in _files(args.logs / "commentary", args.since):
            steps = _sibling(args.logs / "steps", commentary)
            found += forecasts_of(
                read_jsonl([commentary]), read_jsonl([steps]) if steps is not None else []
            )
        print(report_forecasts(found))
        return

    rows = read_jsonl(_files(args.logs / "spoken", args.since))
    lines = lines_of(rows)
    if args.judge == "jev":
        asyncio.run(judge_lines([line for line in lines if line.heard]))
    print(report_lines(lines, judged=args.judge == "jev", verdicts=verdicts_of(rows)))


if __name__ == "__main__":
    main()
