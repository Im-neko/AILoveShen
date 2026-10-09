#!/usr/bin/env python3
"""
再現できる課題で、変更が効いたかを測る（docs/design/83）。成否はブリッジが世界から判定する。

配信とは別のサーバー・ブリッジ・保存先で走る（配信のワールドや state.json に触れない）。毎回
新しいワールド（同じシード = 同じ地形）で、素手から（課題によっては材料だけ渡して）、課題 1 つだけを大目標にして、上限の時間まで
プレイさせる。**配信（python -m ailoveshen.stream）を止めてから走らせる**: ローカルの LLM を共有する
と、時間の測定が意味をなさない。

    python tools/benchmark.py run                       # 全課題 x 3 回（config/benchmark.yaml）
    python tools/benchmark.py run --tasks food --repeats 1 --cap-minutes 5
    python tools/benchmark.py run --dry-run             # やることだけ出す
    python tools/benchmark.py report logs/bench/20261003T010000Z.jsonl
    python tools/benchmark.py compare 前.jsonl 後.jsonl  # 同じ課題を並べて比べる

`play` は内部用（run が実行ごとに別のプロセスとして呼ぶ）。
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_fixtures  # noqa: E402

CONFIG = REPO / "config" / "benchmark.yaml"
COMPOSE_FILE = REPO / "docker" / "docker-compose.bench.yml"
COMPOSE_PROJECT = "ailoveshen-bench"
CONTAINER = "ailoveshen-bench"
BRIDGE_DIR = REPO / "minecraft-bridge"
DEFAULT_SEED = "ailoveshen-bench"
SERVER_WAIT_SECONDS = 300
BRIDGE_WAIT_SECONDS = 180
PLAY_GRACE_SECONDS = 300  # 上限のあとの片づけと、プロセスの起動に見込む時間
DAY_RULES = (
    "gamerule doDaylightCycle false",
    "gamerule doWeatherCycle false",
    "time set 1000",
    "weather clear",
)

SUCCESS = "success"
TIMEOUT = "timeout"
ERROR = "error"  # 環境の不具合（成否に数えない）


@dataclass(frozen=True)
class Task:
    """測る課題: 大目標 1 つ（条件は /check で世界から判定する）。"""

    name: str
    title: str
    conditions: tuple[dict[str, Any], ...]
    reason: str = ""
    give: tuple[tuple[str, int], ...] = ()  # 始まる前に渡す持ち物（アイテム名, 数）。空なら素手
    setup: tuple[str, ...] = ()  # ボットが入ったあとに RCON で行う、状況づくりのコマンド（SETUP_VERBS だけ）
    freeze_day: Optional[bool] = None  # False: この課題は時間が進むことを求める（夜）。None なら全体の設定に従う
    fixture: Optional[str] = None  # 場面づくり（tools/bench_fixtures.py の名前）: 世界とブリッジの状態を作る

    def objective(self) -> dict[str, Any]:
        """`minecraft.mission.objectives` の 1 件。"""
        return {
            "title": self.title,
            "conditions": [dict(c) for c in self.conditions],
            "reason": self.reason,
        }


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    """`config/benchmark.yaml` の benchmark 節。"""
    return yaml.safe_load(path.read_text(encoding="utf-8"))["benchmark"]


def load_tasks(config: dict[str, Any], names: Optional[list[str]] = None) -> list[Task]:
    """課題の一覧。names があればその順に（なければ全部）。知らない名前は ValueError。"""
    # 既定で走るのは tasks だけ。scenarios（起きた事故の再現）は名前で指定したときだけ走る
    known = {**config["tasks"], **(config.get("scenarios") or {})}
    chosen = names or list(config["tasks"])
    missing = [n for n in chosen if n not in known]
    if missing:
        raise ValueError(f"知らない課題: {', '.join(missing)}（{', '.join(known)}）")
    return [
        Task(
            name=n,
            title=known[n]["title"],
            conditions=tuple(known[n]["conditions"]),
            reason=known[n].get("reason", ""),
            give=tuple(
                (item, int(count)) for item, count in (known[n].get("give") or {}).items()
            ),
            setup=tuple(known[n].get("setup") or ()),
            freeze_day=known[n].get("freeze_day"),
            fixture=known[n].get("fixture"),
        )
        for n in chosen
    ]


def default_seed(env_file: Path = REPO / ".env") -> str:
    """配信と同じシード（.env の MC_SEED）。なければ固定の文字列。"""
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^MC_SEED=(.*)$", line.strip())
            if m and m.group(1).strip():
                return m.group(1).strip().strip("\"'")
    return DEFAULT_SEED


def isolate(obj: Any, run_dir: Path, prefix: str = "") -> list[str]:
    """
    設定の保存先（*_path）と記録先（*record_dir）を、実行ごとのディレクトリに付け替える。
    付け替えた欄の名前を返す。配信の data/ や logs/ に書かないための要。入れ子の欄は
    `watch_record_dir` のように前の名前をつける（`watch.record_dir` と `reflex.record_dir` が
    同じディレクトリにならないように）。
    """
    changed: list[str] = []
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        if dataclasses.is_dataclass(value):
            changed.extend(isolate(value, run_dir, f"{prefix}{f.name}_"))
        elif isinstance(value, str) and value:
            if f.name.endswith("record_dir"):
                setattr(obj, f.name, str(run_dir / "logs" / f"{prefix}{f.name}"))
                changed.append(f"{prefix}{f.name}")
            elif f.name.endswith("_path"):
                setattr(obj, f.name, str(run_dir / "data" / Path(value).name))
                changed.append(f"{prefix}{f.name}")
    return changed


def bridge_env(run_dir: Path, config: dict[str, Any], base: dict[str, str]) -> dict[str, str]:
    """ベンチマーク用のブリッジの環境変数（配信の保存先に触れない）。シェルの値が .env に勝つ。"""
    run_dir = run_dir.resolve()
    out = dict(base)
    out.update(
        {
            "MC_HOST": "localhost",
            "MC_PORT": str(config["server_port"]),
            "BRIDGE_PORT": str(config["bridge_port"]),
            "MIRROR_PORT": str(config["mirror_port"]),
            "STATE_FILE": str(run_dir / "bridge" / "state.json"),
            "SKILLS_DIR": str(run_dir / "bridge" / "skills"),
            "WORLDMAP_DIR": str(run_dir / "bridge" / "worldmap"),
            "BRIDGE_LOG_FILE": str(run_dir / "bridge" / "bridge.log"),
            "STUCK_WALKS_FILE": str(run_dir / "bridge" / "stuck-walks.jsonl"),
            "DROP_WATCH_FILE": str(run_dir / "bridge" / "drop-watch.jsonl"),
            "PLACE_REFUSALS_FILE": str(run_dir / "bridge" / "place-refusals.jsonl"),
        }
    )
    return out


def play_env(config: dict[str, Any], base: dict[str, str]) -> dict[str, str]:
    """`play` を呼ぶプロセスの環境変数（ブリッジの接続先をベンチマーク用にする）。"""
    out = dict(base)
    out.update({"BRIDGE_HOST": "localhost", "BRIDGE_PORT": str(config["bridge_port"])})
    return out


# ---------------------------------------------------------------- 結果の集計


def median(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def summarize_task(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """1 課題の繰り返しの結果。環境の不具合（error）は成否に数えない。"""
    valid = [r for r in rows if r.get("status") in (SUCCESS, TIMEOUT)]
    wins = [r for r in valid if r["status"] == SUCCESS]
    return {
        "runs": len(rows),
        "valid": len(valid),
        "errors": len(rows) - len(valid),
        "successes": len(wins),
        "success_rate": len(wins) / len(valid) if valid else None,
        "median_seconds": median([r["seconds_to_success"] for r in wins]),
        "median_seconds_after_design": median(
            [
                r["seconds_to_success_after_design"]
                for r in wins
                if r.get("seconds_to_success_after_design") is not None
            ]
        ),
        "median_steps": median([r["steps"] for r in valid if isinstance(r.get("steps"), int)]),
        "deaths": sum(r["deaths"] for r in valid if isinstance(r.get("deaths"), int)),
        # 道具の呼び出しのうち断られた数と、断らずに警告で通した数（BRIDGE_GUARDS が standard 以外）の中央値
        "median_refused": median([r["tool_refused"] for r in valid if isinstance(r.get("tool_refused"), int)]),
        "median_advised": median([r["tool_advised"] for r in valid if isinstance(r.get("tool_advised"), int)]),
    }


def by_task(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """課題ごとの結果（最初に現れた順）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("event") == "bench_run":
            grouped.setdefault(r["task"], []).append(r)
    return {t: summarize_task(rs) for t, rs in grouped.items()}


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _f(value: Optional[float], spec: str = ".0f") -> str:
    return "-" if value is None else format(value, spec)


def _width(text: str) -> int:
    return sum(2 if ord(c) > 0x2E80 else 1 for c in text)


def _table(rows: list[list[str]]) -> str:
    widths = [max(_width(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell + " " * (widths[i] - _width(cell)) for i, cell in enumerate(r)) for r in rows
    )


def render_report(summary: dict[str, dict[str, Any]]) -> str:
    """課題ごとの表。"""
    rows = [
        ["課題", "成功", "成功率", "秒(中央)", "設計後の秒", "歩(中央)", "断り(中央)", "警告(中央)", "死亡", "環境の不具合"]
    ]
    for task, s in summary.items():
        rate = "-" if s["success_rate"] is None else f"{s['success_rate'] * 100:.0f}%"
        rows.append(
            [
                task,
                f"{s['successes']}/{s['valid']}",
                rate,
                _f(s["median_seconds"]),
                _f(s["median_seconds_after_design"]),
                _f(s["median_steps"]),
                _f(s["median_refused"]),
                _f(s["median_advised"]),
                str(s["deaths"]),
                str(s["errors"]),
            ]
        )
    return _table(rows)


def render_compare(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> str:
    """同じ課題を前後で並べる。"""
    rows = [["課題", "成功(前)", "成功(後)", "秒(前)", "秒(後)", "歩(前)", "歩(後)", "断り(前→後)", "死亡(前→後)"]]
    for task in [t for t in before if t in after]:
        b, a = before[task], after[task]
        rows.append(
            [
                task,
                f"{b['successes']}/{b['valid']}",
                f"{a['successes']}/{a['valid']}",
                _f(b["median_seconds"]),
                _f(a["median_seconds"]),
                _f(b["median_steps"]),
                _f(a["median_steps"]),
                f"{_f(b['median_refused'])}→{_f(a['median_refused'])}",
                f"{b['deaths']}→{a['deaths']}",
            ]
        )
    return _table(rows)


def version_label(rows: list[dict[str, Any]]) -> str:
    """結果の行から、どの版か（commit と未コミットの変更の指紋）。"""
    for r in rows:
        git = (r.get("run") or {}).get("git") or {}
        if git.get("commit"):
            diff = f"+{git['diff']}" if git.get("dirty") and git.get("diff") else ""
            return f"{git['commit'][:7]}{diff}"
    return "?"


# ---------------------------------------------------------------- 1 回のプレイ（内部）


def decide_status(
    met: bool, play_ended: bool, play_exception: Optional[BaseException]
) -> tuple[str, Optional[str]]:
    """
    終わり方: 条件が満たされた = 成功。上限まで満たされなかった = 失敗（timeout）。プレイが自分で
    終わった（上限の前に、満たされないまま）のは環境の不具合で、成否に数えない。
    """
    if met:
        return SUCCESS, None
    if play_ended:
        return ERROR, f"play ended: {play_exception or 'without an error'}"
    return TIMEOUT, None


LLM_OUTAGE_CALLS = 3  # 終わりまで続けてこれだけ呼び出しが失敗していたら、LLM サーバーの停止とみなす


def llm_outage(run_dir: Path) -> int:
    """
    終わりの側で続けて失敗した LLM の呼び出しの数（設計書 90 の記録から）。サーバーが止まると、プレイは
    失敗を待って続けるだけで、成否にはタイムアウトとして出る（2026-10-06: cage の終わりの約 3 分）。
    """
    failed = 0
    for path in sorted((run_dir / "logs" / "generations").glob("gen-*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            failed = failed + 1 if entry.get("error") else 0
    return failed


def count_steps(run_dir: Path) -> int:
    """行動の数（logs/steps の結果の行）。"""
    total = 0
    for path in (run_dir / "logs" / "step_record_dir").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"event": "result"' in line:
                total += 1
    return total


def result_row(
    status: str,
    error: Optional[str],
    marks: dict[str, float],
    wall: float,
    cap: float,
    steps: int,
    deaths: Optional[int],
) -> dict[str, Any]:
    """測った時刻（プレイの始まりからの秒）から、結果の 1 件。"""
    success = marks.get("success")
    design = marks.get("design")
    return {
        "status": status,
        "error": error,
        "cap_seconds": cap,
        "wall_seconds": round(wall, 1),
        "design_seconds": None if design is None else round(design, 1),
        "first_goal_seconds": None
        if marks.get("first_goal") is None
        else round(marks["first_goal"], 1),
        "seconds_to_success": None if success is None else round(success, 1),
        # 家の設計（LLM の待ち）を除いた時間: 課題そのものに使った時間
        "seconds_to_success_after_design": (
            None if success is None else round(success - (design or 0.0), 1)
        ),
        "steps": steps,
        "deaths": deaths,
    }


async def play_one(
    task: Task, run_dir: Path, cap_seconds: float, check_seconds: float, bridge_port: int
) -> dict[str, Any]:
    """課題 1 つをプレイして、結果の 1 件を返す。"""
    from ailoveshen.application.use_cases.goal_vocabulary import parse_spec
    from ailoveshen.domain.entities import Conversation
    from ailoveshen.domain.events import (
        GoalSetEvent,
        HouseDesignedEvent,
    )
    from ailoveshen.factories.game import create_game_service
    from ailoveshen.factories.text import create_text_generator
    from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_client import (
        MineflayerBridgeClient,
    )
    from ailoveshen.infrastructure.adapters.storage import JsonlGenerationLog
    from ailoveshen.infrastructure.config import load_settings
    from ailoveshen.infrastructure.events import AsyncEventBus

    settings = load_settings(config_dir=REPO / "config")
    mc = settings.minecraft
    if mc.bridge_port != bridge_port:
        # 配信のブリッジ（3000）に繋いでしまわないための最後の砦
        raise RuntimeError(f"bridge port is {mc.bridge_port}, expected {bridge_port}")
    isolate(mc, run_dir)
    mc.mission.playbook = []
    mc.mission.objectives = [task.objective()]
    specs = [parse_spec(c) for c in task.conditions]

    marks: dict[str, float] = {}
    started = time.monotonic()
    bus = AsyncEventBus()

    async def on_design(_: HouseDesignedEvent) -> None:
        marks.setdefault("design", time.monotonic() - started)

    async def on_goal(event: GoalSetEvent) -> None:
        if event.objective == task.title:
            marks.setdefault("first_goal", time.monotonic() - started)

    bus.subscribe(HouseDesignedEvent, on_design)
    bus.subscribe(GoalSetEvent, on_goal)
    # LLM の呼び出しの全文も、実行ごとのディレクトリに残す（設計書 90。配信の logs/ には書かない）
    log = JsonlGenerationLog(str(run_dir / "logs" / "generations"), settings.llm.debug_log_size)
    shared = create_text_generator(settings.llm, log)
    game = create_game_service(
        jev=settings.jev,
        minecraft=mc,
        character=settings.character,
        event_publisher=bus,
        conversation=Conversation(),
        generation_log=log,
        llm=settings.llm,
        text_generator=shared,
        obs=settings.obs,
    )
    bridge = MineflayerBridgeClient(
        host=mc.bridge_host, port=mc.bridge_port, timeout_seconds=mc.request_timeout_seconds
    )
    met = asyncio.Event()

    async def watch() -> None:
        while True:
            await asyncio.sleep(check_seconds)
            try:
                statuses = await bridge.check(specs)
            except Exception:  # 一時的な失敗は次の周で
                continue
            if all(s.met for s in statuses):
                marks["success"] = time.monotonic() - started
                met.set()
                return

    play = asyncio.create_task(game.play(max_steps=None, keep_going=True))
    watcher = asyncio.create_task(watch())
    success_wait = asyncio.create_task(met.wait())
    status, error = TIMEOUT, None
    deaths: Optional[int] = None
    try:
        done, _ = await asyncio.wait(
            {play, success_wait}, timeout=cap_seconds, return_when=asyncio.FIRST_COMPLETED
        )
        status, error = decide_status(
            met.is_set(), play in done, play.exception() if play in done else None
        )
        try:
            obs = await bridge.observe()
            count = obs.state.get("deaths")
            deaths = count if isinstance(count, int) else None
        except Exception as e:
            if status == TIMEOUT:
                status, error = ERROR, f"the bridge did not answer at the end: {e}"
    finally:
        for t in (play, watcher, success_wait):
            t.cancel()
        await asyncio.gather(play, watcher, success_wait, return_exceptions=True)
        await game.close()
        await bridge.close()
    outage = llm_outage(run_dir)
    if status == TIMEOUT and outage >= LLM_OUTAGE_CALLS:
        # 環境の不具合（LLM サーバーの停止）で、成否に数えない
        status, error = ERROR, f"the LLM server was down at the end ({outage} calls in a row failed)"
    return result_row(
        status, error, marks, time.monotonic() - started, cap_seconds, count_steps(run_dir), deaths
    )


def cmd_play(args: argparse.Namespace) -> int:
    config = load_config()
    task = load_tasks(config, [args.task])[0]
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        row = asyncio.run(
            play_one(task, run_dir, args.cap_seconds, config["check_seconds"], args.bridge_port)
        )
    except Exception as e:
        row = result_row(ERROR, f"{type(e).__name__}: {e}", {}, 0.0, args.cap_seconds, 0, None)
        (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return 0


# ---------------------------------------------------------------- 実行の組み立て（サーバー・ブリッジ）


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def stream_running() -> list[str]:
    """動いている配信側のプロセス（ベンチマークの前に止めてもらう）。"""
    found = []
    for label, pattern in (("配信", "ailoveshen.stream"), ("ブリッジ", "node.*src/index.mjs")):
        done = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        if done.returncode == 0:
            found.append(label)
    return found


def preflight(config: dict[str, Any], allow_stream: bool) -> list[str]:
    """走らせられない理由（なければ空）。"""
    problems = []
    if not allow_stream:
        running = stream_running()
        if running:
            problems.append(
                f"{'と'.join(running)}が動いている。止めてから（ローカルの LLM を共有すると時間の測定が"
                "意味をなさず、ブリッジも取り合う）"
            )
    for name in ("server_port", "bridge_port", "mirror_port"):
        if port_in_use(config[name]):
            problems.append(f"ポート {config[name]}（{name}）がもう使われている")
    return problems


def compose(*args: str, env: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", COMPOSE_PROJECT, "-f", str(COMPOSE_FILE), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def start_server(level: str, seed: str, config: dict[str, Any], freeze_day: bool) -> None:
    """新しいワールドでサーバーを起動する（前のボリュームは捨てる）。"""
    env = {
        **os.environ,
        "BENCH_LEVEL": level,
        "BENCH_SEED": seed,
        "BENCH_MC_PORT": str(config["server_port"]),
    }
    compose("down", "-v", "--remove-orphans", env=env)
    up = compose("up", "-d", env=env)
    if up.returncode != 0:
        raise RuntimeError(f"サーバーを起動できない: {up.stderr.strip()[-300:]}")
    deadline = time.monotonic() + SERVER_WAIT_SECONDS
    while time.monotonic() < deadline:
        logs = subprocess.run(
            ["docker", "logs", "--tail", "200", CONTAINER], capture_output=True, text=True
        )
        if "Done (" in logs.stdout + logs.stderr:
            break
        time.sleep(3)
    else:
        raise RuntimeError(f"サーバーが {SERVER_WAIT_SECONDS} 秒たっても起動しない")
    if freeze_day:
        for rule in DAY_RULES:
            for _ in range(10):  # RCON は "Done" の少しあとから
                done = subprocess.run(
                    ["docker", "exec", CONTAINER, "rcon-cli", rule], capture_output=True, text=True
                )
                if done.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise RuntimeError(f"RCON が通らない: {rule}")


ITEM_NAME = re.compile(r"^[a-z0-9_]+$")


def give_commands(task: Task) -> list[str]:
    """課題の持ち物を渡す RCON コマンド（ベンチマークのサーバーにいるのはボットだけ）。"""
    commands = []
    for item, count in task.give:
        if not ITEM_NAME.match(item) or count < 1:
            raise ValueError(f"渡せない持ち物: {item} x {count}")
        commands.append(f"give @a minecraft:{item} {count}")
    return commands


# 状況づくりに使ってよい RCON の動詞。使い捨てのベンチマークのサーバーだけに送る
SETUP_VERBS = ("time", "weather", "fill", "setblock", "execute")


def setup_commands(task: Task) -> list[str]:
    """課題の状況づくりの RCON コマンド。1 行で、許した動詞で始まるものだけ。"""
    for command in task.setup:
        if "\n" in command or command.split(" ", 1)[0] not in SETUP_VERBS:
            raise ValueError(f"使えない状況づくりのコマンド: {command!r}（{', '.join(SETUP_VERBS)} だけ）")
    return list(task.setup)


def _rcon(command: str) -> str:
    done = subprocess.run(
        ["docker", "exec", CONTAINER, "rcon-cli", command], capture_output=True, text=True
    )
    if done.returncode != 0:
        raise RuntimeError(f"RCON が失敗した: {command}: {(done.stdout + done.stderr).strip()}")
    return (done.stdout + done.stderr).strip()


def run_setup(task: Task) -> None:
    """ボットがサーバーに入ってから、課題の状況をつくる。プレイヤーがいなければ例外（空振りさせない）。"""
    commands = setup_commands(task)
    if not commands:
        return
    if "passed" not in _rcon("execute if entity @a"):
        raise RuntimeError("状況をつくれない: サーバーにプレイヤーがいない")
    for command in commands:
        out = _rcon(command)
        # 失敗は終了コード 0 のまま文面で返る（"Unknown or incomplete command"、"Could not set the block" など）
        if re.search(r"unknown|incomplete|could not|expected|error|cannot", out, re.IGNORECASE):
            raise RuntimeError(f"状況づくりのコマンドが通らない: {command}: {out}")


def bot_origin(config: dict[str, Any]) -> tuple[int, int, int]:
    """ボットの足元のブロック。入った直後は落ちていることがあるので、2 回続けて同じ位置になるまで待つ。"""
    import urllib.request

    url = f"http://127.0.0.1:{config['bridge_port']}/observe"
    last: Optional[tuple[int, int, int]] = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with urllib.request.urlopen(url, timeout=5) as r:
            p = json.loads(r.read())["observation"]["self"]["position"]
        here = (int(p["x"] // 1), int(round(p["y"] - 0.01) // 1), int(p["z"] // 1))
        if here == last:
            return here
        last = here
        time.sleep(1.5)
    raise RuntimeError("ボットの位置が落ち着かない")


def run_fixture(task: Task, run_dir: Path, config: dict[str, Any], bridge: subprocess.Popen) -> subprocess.Popen:
    """
    場面づくり: ボットの足元を基準に世界を作り（RCON）、ブリッジを止めて state.json を書き換え、起動し直す。
    ブリッジの状態は起動のときにしか読まれない。起動し直したボットは、同じ場所に入り直す。
    """
    if task.fixture is None:
        return bridge
    commands, state = bench_fixtures.build(task.fixture, bot_origin(config))
    if "passed" not in _rcon("execute if entity @a"):
        raise RuntimeError("場面をつくれない: サーバーにプレイヤーがいない")
    for command in commands:
        if command.split(" ", 1)[0] not in SETUP_VERBS:
            raise ValueError(f"使えない場面づくりのコマンド: {command!r}")
        out = _rcon(command)
        if re.search(r"unknown|incomplete|could not|expected|error|cannot", out, re.IGNORECASE):
            raise RuntimeError(f"場面づくりのコマンドが通らない: {command}: {out}")
    stop_bridge(bridge)
    (run_dir / "bridge").mkdir(parents=True, exist_ok=True)
    (run_dir / "bridge" / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return start_bridge(run_dir, config)


def tool_counts(run_dir: Path) -> dict[str, int]:
    """ブリッジのログから、道具の呼び出しの数と、断られた数、断らずに警告で通した数（BRIDGE_GUARDS が standard 以外）。"""
    log = run_dir / "bridge" / "bridge.log"
    calls = refused = advised = 0
    if log.exists():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if "[tool] " not in line:
                continue
            calls += 1
            refused += "断った" in line
            advised += "(not enforced:" in line
    return {"tool_calls": calls, "tool_refused": refused, "tool_advised": advised}


def give_items(task: Task) -> None:
    """ボットがサーバーに入ってから、課題の持ち物を渡す（サーバーの応答が失敗なら例外）。"""
    for command in give_commands(task):
        done = subprocess.run(
            ["docker", "exec", CONTAINER, "rcon-cli", command], capture_output=True, text=True
        )
        # 該当するプレイヤーがいないと "No player was found" が返る（終了コードは 0 のまま）
        if done.returncode != 0 or "No player" in done.stdout + done.stderr:
            raise RuntimeError(f"持ち物を渡せない: {command}: {(done.stdout + done.stderr).strip()}")


def stop_server(config: dict[str, Any]) -> None:
    compose(
        "down",
        "-v",
        "--remove-orphans",
        env={**os.environ, "BENCH_MC_PORT": str(config["server_port"])},
    )


def start_bridge(run_dir: Path, config: dict[str, Any]) -> subprocess.Popen:
    """ベンチマーク用のブリッジを起動し、観測が返るまで待つ。"""
    (run_dir / "bridge").mkdir(parents=True, exist_ok=True)
    out = open(run_dir / "bridge" / "stdout.log", "wb")
    proc = subprocess.Popen(
        ["node", "--no-node-snapshot", "src/index.mjs"],
        cwd=BRIDGE_DIR,
        env=bridge_env(run_dir, config, dict(os.environ)),
        stdout=out,
        stderr=subprocess.STDOUT,
    )
    import urllib.request

    deadline = time.monotonic() + BRIDGE_WAIT_SECONDS
    url = f"http://127.0.0.1:{config['bridge_port']}/observe"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"ブリッジが終わった（{proc.returncode}）: {run_dir}/bridge/stdout.log"
            )
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return proc
        except OSError:
            pass
        time.sleep(2)
    proc.terminate()
    raise RuntimeError(f"ブリッジが {BRIDGE_WAIT_SECONDS} 秒たっても観測を返さない")


def stop_bridge(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_one(
    batch: str,
    task: Task,
    repeat: int,
    seed: str,
    config: dict[str, Any],
    cap_minutes: float,
    freeze_day: bool,
    out: Path,
) -> dict[str, Any]:
    """課題 1 回: 新しいワールド → ブリッジ → プレイ → 片づけ。"""
    run_dir = out / batch / f"{task.name}-{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)
    level = f"bench-{batch[-6:]}-{task.name}-{repeat}"
    cap = cap_minutes * 60
    bridge: Optional[subprocess.Popen] = None
    try:
        freeze_day = freeze_day and task.freeze_day is not False  # 夜の課題だけが、時間が進むことを求める
        start_server(level, seed, config, freeze_day)
        bridge = start_bridge(run_dir, config)
        bridge = run_fixture(task, run_dir, config, bridge)
        give_items(task)
        run_setup(task)
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "play",
                "--task",
                task.name,
                "--run-dir",
                str(run_dir),
                "--cap-seconds",
                str(cap),
                "--bridge-port",
                str(config["bridge_port"]),
            ],
            env=play_env(config, dict(os.environ)),
            stdout=open(run_dir / "play.log", "wb"),
            stderr=subprocess.STDOUT,
            timeout=cap + PLAY_GRACE_SECONDS,
            check=False,
        )
        result_file = run_dir / "result.json"
        if result_file.exists():
            row = json.loads(result_file.read_text(encoding="utf-8"))
        else:
            row = result_row(ERROR, "play left no result", {}, 0.0, cap, 0, None)
    except Exception as e:  # サーバー・ブリッジ・プロセスの不具合: 成否に数えない
        row = result_row(ERROR, f"{type(e).__name__}: {e}", {}, 0.0, cap, 0, None)
    finally:
        stop_bridge(bridge)
        stop_server(config)
    run_file = next((run_dir / "logs" / "run_record_dir").glob("*.jsonl"), None)
    run_info = next(iter(load_rows([run_file])), None) if run_file is not None else None
    return {
        "event": "bench_run",
        "batch": batch,
        "task": task.name,
        "repeat": repeat,
        "level": level,
        "seed": seed,
        "freeze_day": freeze_day,
        "guards": os.environ.get("BRIDGE_GUARDS") or "free",
        "at": datetime.now(timezone.utc).isoformat(),
        **row,
        **tool_counts(run_dir),
        "run": run_info,
    }


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    tasks = load_tasks(config, args.tasks.split(",") if args.tasks else None)
    repeats = args.repeats or config["repeats"]
    cap_minutes = args.cap_minutes or config["cap_minutes"]
    freeze_day = config["freeze_day"] and not args.no_freeze_day
    seed = args.seed or default_seed()
    batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # 絶対パスにする: ブリッジは minecraft-bridge/ を作業ディレクトリにして走るので、相対のままだと
    # STATE_FILE や BRIDGE_LOG_FILE がそちらの下に書かれる
    out = Path(args.out).resolve()
    total = len(tasks) * repeats
    print(
        f"ベンチマーク {batch}: {len(tasks)} 課題 x {repeats} 回 = {total} 回、1 回 最長 {cap_minutes:g} 分"
    )
    print(f"  課題: {', '.join(t.name for t in tasks)}")
    for t in tasks:
        if t.setup or t.freeze_day is False:
            print(f"    {t.name}: 状況づくり {list(t.setup)}{'、昼固定なし' if t.freeze_day is False else ''}")
    print(f"  シード: {seed}（毎回新しいワールド）、昼固定: {freeze_day}、素手から（`give` のある課題は持ち物つき）")
    print(
        f"  サーバー {config['server_port']}、ブリッジ {config['bridge_port']}、記録 {out}/{batch}"
    )
    if args.dry_run:
        print("(dry-run: 何も起動していない)")
        return 0
    problems = preflight(config, args.allow_stream)
    if problems:
        for p in problems:
            print(f"エラー: {p}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    results = out / f"{batch}.jsonl"
    rows = []
    for task in tasks:
        for n in range(1, repeats + 1):
            print(f"[{task.name} {n}/{repeats}] 始める", flush=True)
            row = run_one(batch, task, n, seed, config, cap_minutes, freeze_day, out)
            rows.append(row)
            with results.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                f"[{task.name} {n}/{repeats}] {row['status']}"
                f"{'' if row['seconds_to_success'] is None else f'（{row['seconds_to_success']:.0f} 秒）'}"
                f"{'' if not row['error'] else f'  {row['error']}'}",
                flush=True,
            )
    print()
    print(render_report(by_task(rows)))
    print(f"\n記録: {results}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    rows = load_rows([Path(p) for p in args.files])
    print(f"版: {version_label(rows)}")
    print(render_report(by_task(rows)))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    before, after = load_rows([Path(args.before)]), load_rows([Path(args.after)])
    print(f"前: {version_label(before)}   後: {version_label(after)}")
    print(render_compare(by_task(before), by_task(after)))
    print("\n1 課題 3 回では、差は目安にとどまる（成功の回数と、実行ごとの値を見る）。")
    return 0


def main() -> int:
    return main_with(sys.argv[1:])


SUBCOMMANDS = ("run", "report", "compare", "play")


def with_default_command(argv: list[str]) -> list[str]:
    """サブコマンドを省くと run（`mise run bench -- --dry-run` のように）。-h は全体の説明。"""
    if argv and argv[0] in SUBCOMMANDS + ("-h", "--help"):
        return argv
    return ["run", *argv]


def main_with(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="再現できる課題で変更の効果を測る")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="課題を走らせる（配信を止めてから）")
    run.add_argument("--tasks", help="課題の名前（カンマ区切り。既定は全部）")
    run.add_argument("--repeats", type=int, help="1 課題の繰り返し")
    run.add_argument("--cap-minutes", type=float, help="1 回の上限（分）")
    run.add_argument("--seed", help="ワールドのシード（既定は .env の MC_SEED）")
    run.add_argument("--out", default="logs/bench", help="記録の置き場")
    run.add_argument("--no-freeze-day", action="store_true", help="昼固定をやめる（夜が来る）")
    run.add_argument("--allow-stream", action="store_true", help="配信が動いていても走らせる")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run)

    report = sub.add_parser("report", help="結果の集計")
    report.add_argument("files", nargs="+")
    report.set_defaults(func=cmd_report)

    compare = sub.add_parser("compare", help="前後の結果を並べる")
    compare.add_argument("before")
    compare.add_argument("after")
    compare.set_defaults(func=cmd_compare)

    play = sub.add_parser("play", help="（内部）課題 1 回をこのプロセスでプレイする")
    play.add_argument("--task", required=True)
    play.add_argument("--run-dir", required=True)
    play.add_argument("--cap-seconds", type=float, required=True)
    play.add_argument("--bridge-port", type=int, required=True)
    play.set_defaults(func=cmd_play)

    args = parser.parse_args(with_default_command(argv))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
