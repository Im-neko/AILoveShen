"""tools/benchmark.py（再現できる課題での測定、設計書 83）のテスト。実際のサーバーは起動しない。"""

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "benchmark", Path(__file__).resolve().parents[3] / "tools" / "benchmark.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)

from ailoveshen.application.use_cases.goal_vocabulary import parse_spec  # noqa: E402
from ailoveshen.domain.value_objects import Objective  # noqa: E402
from ailoveshen.infrastructure.config import MinecraftSettings  # noqa: E402


def _row(task, status, seconds=None, after=None, steps=10, deaths=0, **kw):
    return {
        "event": "bench_run",
        "task": task,
        "status": status,
        "seconds_to_success": seconds,
        "seconds_to_success_after_design": after,
        "steps": steps,
        "deaths": deaths,
        **kw,
    }


class TestTasks:
    """課題は config/benchmark.yaml。"""

    def test_every_task_is_a_valid_objective_condition(self):
        config = tool.load_config()

        for task in tool.load_tasks(config):
            assert [parse_spec(c) for c in task.conditions]
            assert task.objective()["title"] == task.title
            Objective(id="t", title=task.title, conditions=tuple(parse_spec(c) for c in task.conditions))

    def test_the_agreed_tasks_are_there_with_the_agreed_settings(self):
        config = tool.load_config()

        assert [t.name for t in tool.load_tasks(config)] == ["food", "pickaxe", "build"]
        assert config["repeats"] == 3
        assert config["cap_minutes"] == 15

    def test_names_pick_and_order_the_tasks(self):
        config = tool.load_config()

        assert [t.name for t in tool.load_tasks(config, ["build", "food"])] == ["build", "food"]

    def test_only_the_build_task_hands_over_items(self):
        tasks = {t.name: t for t in tool.load_tasks(tool.load_config())}

        assert tasks["food"].give == () and tasks["pickaxe"].give == ()
        given = dict(tasks["build"].give)
        # 家は最大 7x7x4: 屋根 49 + 壁 4 面 x 6 x 4 = 約 150 ブロックに足りる板材と、扉
        assert given["oak_planks"] >= 150
        assert given["oak_door"] >= 1

    def test_the_give_commands_name_the_bot_only_through_the_selector(self):
        task = tool.Task("t", "t", (), give=(("oak_planks", 192), ("oak_door", 2)))

        assert tool.give_commands(task) == [
            "give @a minecraft:oak_planks 192",
            "give @a minecraft:oak_door 2",
        ]

    def test_an_item_name_that_is_not_plain_is_refused(self):
        task = tool.Task("t", "t", (), give=(("oak_planks; stop", 1),))

        with pytest.raises(ValueError):
            tool.give_commands(task)

    def test_scenarios_do_not_run_by_default_but_can_be_named(self):
        config = tool.load_config()

        assert not {"night", "pond", "cage"} & {t.name for t in tool.load_tasks(config)}
        named = tool.load_tasks(config, ["night", "pond", "cage"])
        assert [t.name for t in named] == ["night", "pond", "cage"]
        for task in named:
            assert [parse_spec(c) for c in task.conditions]
            assert tool.setup_commands(task) == list(task.setup)
            # 大目標の条件に使えない述語（through_night、explored）は、走らせて初めて断られる（2026-10-06）
            Objective(id="t", title=task.title, conditions=tuple(parse_spec(c) for c in task.conditions))

    def test_only_the_night_scenario_needs_time_to_run(self):
        tasks = {t.name: t for t in tool.load_tasks(tool.load_config(), ["night", "pond", "cage", "food"])}

        assert tasks["night"].freeze_day is False
        assert all(tasks[n].freeze_day is None for n in ("pond", "cage", "food"))

    def test_setup_commands_only_allow_the_listed_verbs_on_one_line(self):
        ok = tool.Task("t", "t", (), setup=("time set 12000", "execute at @a run fill ~ ~ ~ ~ ~ ~ minecraft:water"))
        assert tool.setup_commands(ok) == list(ok.setup)

        for bad in ("stop", "op @a", "give @a minecraft:diamond 64", "time set 1\nstop", ""):
            with pytest.raises(ValueError):
                tool.setup_commands(tool.Task("t", "t", (), setup=(bad,)))

    def test_llm_outage_counts_only_the_failures_at_the_end(self, tmp_path):
        directory = tmp_path / "logs" / "generations"
        directory.mkdir(parents=True)
        rows = [{"purpose": "tool"}, {"error": "x"}, {"purpose": "tool"}, {"error": "a"}, {"error": "b"}]
        (directory / "gen-1.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows) + "{broken", encoding="utf-8"
        )

        assert tool.llm_outage(tmp_path) == 2
        assert tool.llm_outage(tmp_path / "none") == 0

    def test_an_unknown_task_is_refused_with_the_known_ones(self):
        with pytest.raises(ValueError, match="food"):
            tool.load_tasks(tool.load_config(), ["nope"])

    def test_the_benchmark_ports_do_not_collide_with_the_stream(self):
        config = tool.load_config()

        assert config["server_port"] != 25565
        assert config["bridge_port"] != 3000
        assert config["mirror_port"] != 25578


class TestIsolation:
    """配信の保存先と記録先に書かない。"""

    @staticmethod
    def _storage_fields(obj, prefix=""):
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            if dataclasses.is_dataclass(value):
                yield from TestIsolation._storage_fields(value, f"{prefix}{f.name}.")
            elif isinstance(value, str) and (
                f.name.endswith("_path") or f.name.endswith("record_dir")
            ):
                yield f"{prefix}{f.name}", value

    def test_every_path_and_record_dir_moves_under_the_run_directory(self, tmp_path):
        settings = MinecraftSettings()

        tool.isolate(settings, tmp_path)

        fields = dict(self._storage_fields(settings))
        assert fields
        for name, value in fields.items():
            assert value.startswith(str(tmp_path)), name

    def test_nested_record_dirs_do_not_share_a_directory(self, tmp_path):
        settings = MinecraftSettings()

        tool.isolate(settings, tmp_path)

        assert settings.watch.record_dir != settings.reflex.record_dir

    def test_other_settings_are_left_alone(self, tmp_path):
        settings = MinecraftSettings()
        before = (settings.control, settings.bridge_port, settings.small_goals)

        tool.isolate(settings, tmp_path)

        assert (settings.control, settings.bridge_port, settings.small_goals) == before

    def test_the_bridge_writes_only_under_the_run_directory_on_its_own_ports(self, tmp_path):
        config = tool.load_config()

        env = tool.bridge_env(tmp_path, config, {"GEMINI_API_KEY": "kept"})

        assert env["GEMINI_API_KEY"] == "kept"
        assert env["BRIDGE_PORT"] == str(config["bridge_port"])
        assert env["MC_PORT"] == str(config["server_port"])
        assert env["MIRROR_PORT"] == str(config["mirror_port"])
        for name in (
            "STATE_FILE",
            "SKILLS_DIR",
            "WORLDMAP_DIR",
            "BRIDGE_LOG_FILE",
            "STUCK_WALKS_FILE",
            "DROP_WATCH_FILE",
            "PLACE_REFUSALS_FILE",
        ):
            assert env[name].startswith(str(tmp_path)), name

    def test_a_relative_run_directory_becomes_absolute_for_the_bridge(self, monkeypatch, tmp_path):
        """ブリッジは minecraft-bridge/ で走る: 相対のままだと、保存先がそちらの下に書かれる。"""
        monkeypatch.chdir(tmp_path)

        env = tool.bridge_env(Path("logs/bench/b/food-1"), tool.load_config(), {})

        for name in ("STATE_FILE", "SKILLS_DIR", "WORLDMAP_DIR", "BRIDGE_LOG_FILE"):
            assert Path(env[name]).is_absolute(), name
            assert env[name].startswith(str(tmp_path.resolve())), name

    def test_the_play_process_talks_to_the_benchmark_bridge(self):
        config = tool.load_config()

        env = tool.play_env(config, {"BRIDGE_PORT": "3000"})

        assert env["BRIDGE_PORT"] == str(config["bridge_port"])


class TestStatus:
    """成功・失敗・環境の不具合を分ける。"""

    def test_met_is_a_success_even_when_the_play_also_ended(self):
        assert tool.decide_status(True, True, None) == (tool.SUCCESS, None)

    def test_not_met_by_the_cap_is_a_failure(self):
        assert tool.decide_status(False, False, None) == (tool.TIMEOUT, None)

    def test_a_play_that_ended_by_itself_is_an_environment_error(self):
        status, error = tool.decide_status(False, True, RuntimeError("bridge gone"))

        assert status == tool.ERROR
        assert "bridge gone" in error

    def test_after_design_removes_the_house_design_wait(self):
        row = tool.result_row(
            tool.SUCCESS, None, {"design": 100.0, "success": 400.0}, 410, 900, 30, 1
        )

        assert row["seconds_to_success"] == 400.0
        assert row["seconds_to_success_after_design"] == 300.0
        assert row["design_seconds"] == 100.0

    def test_without_a_design_the_two_clocks_agree(self):
        row = tool.result_row(tool.SUCCESS, None, {"success": 90.0}, 95, 900, 5, 0)

        assert row["seconds_to_success_after_design"] == 90.0

    def test_a_timeout_has_no_success_time(self):
        row = tool.result_row(tool.TIMEOUT, None, {}, 900, 900, 50, 2)

        assert row["seconds_to_success"] is None
        assert row["seconds_to_success_after_design"] is None


class TestSummary:
    """環境の不具合は成否に数えない。"""

    def test_errors_are_left_out_of_the_rate(self):
        s = tool.summarize_task(
            [
                _row("food", "success", 100, 80),
                _row("food", "timeout"),
                _row("food", "error"),
            ]
        )

        assert s["runs"] == 3
        assert s["valid"] == 2
        assert s["errors"] == 1
        assert s["successes"] == 1
        assert s["success_rate"] == 0.5

    def test_medians_use_successes_for_time_and_all_valid_runs_for_steps(self):
        s = tool.summarize_task(
            [
                _row("food", "success", 100, 80, steps=10),
                _row("food", "success", 300, 250, steps=30),
                _row("food", "timeout", steps=100),
            ]
        )

        assert s["median_seconds"] == 200
        assert s["median_seconds_after_design"] == 165
        assert s["median_steps"] == 30

    def test_deaths_are_totalled_over_valid_runs(self):
        s = tool.summarize_task(
            [
                _row("food", "success", 1, 1, deaths=1),
                _row("food", "timeout", deaths=2),
                _row("food", "error", deaths=9),
            ]
        )

        assert s["deaths"] == 3

    def test_no_valid_run_has_no_rate(self):
        s = tool.summarize_task([_row("food", "error")])

        assert s["success_rate"] is None
        assert s["median_seconds"] is None

    def test_rows_are_grouped_by_task_in_order(self):
        summary = tool.by_task(
            [_row("pickaxe", "success", 1, 1), _row("food", "timeout"), _row("pickaxe", "timeout")]
        )

        assert list(summary) == ["pickaxe", "food"]
        assert summary["pickaxe"]["valid"] == 2


class TestReports:
    """表と前後の比較。"""

    def test_the_report_has_a_row_per_task(self):
        text = tool.render_report(tool.by_task([_row("food", "success", 120, 100)]))

        assert "food" in text
        assert "1/1" in text
        assert "100%" in text

    def test_compare_lines_up_tasks_present_in_both(self):
        before = tool.by_task([_row("food", "timeout"), _row("house", "timeout")])
        after = tool.by_task([_row("food", "success", 200, 200)])

        text = tool.render_compare(before, after)

        assert "food" in text
        assert "house" not in text
        assert "0/1" in text
        assert "1/1" in text

    def test_the_version_comes_from_the_run_record(self):
        rows = [
            _row(
                "food",
                "success",
                run={"git": {"commit": "abcdef123456", "dirty": True, "diff": "f00"}},
            )
        ]

        assert tool.version_label(rows) == "abcdef1+f00"

    def test_a_missing_version_is_marked(self):
        assert tool.version_label([_row("food", "success")]) == "?"

    def test_rows_are_read_from_files_skipping_broken_lines(self, tmp_path):
        path = tmp_path / "b.jsonl"
        path.write_text(
            json.dumps(_row("food", "success", 1, 1)) + "\nnot json\n", encoding="utf-8"
        )

        assert len(tool.load_rows([path])) == 1


class TestPreflight:
    """配信が動いている間、ポートが使われている間は走らせない。"""

    def test_a_running_stream_stops_the_run(self, monkeypatch):
        monkeypatch.setattr(tool, "stream_running", lambda: ["配信", "ブリッジ"])
        monkeypatch.setattr(tool, "port_in_use", lambda port: False)

        problems = tool.preflight(tool.load_config(), allow_stream=False)

        assert len(problems) == 1
        assert "配信" in problems[0]

    def test_allow_stream_skips_that_check(self, monkeypatch):
        monkeypatch.setattr(tool, "stream_running", lambda: ["配信"])
        monkeypatch.setattr(tool, "port_in_use", lambda port: False)

        assert tool.preflight(tool.load_config(), allow_stream=True) == []

    def test_a_used_port_is_named(self, monkeypatch):
        monkeypatch.setattr(tool, "stream_running", lambda: [])
        monkeypatch.setattr(tool, "port_in_use", lambda port: port == 3001)

        problems = tool.preflight(tool.load_config(), allow_stream=False)

        assert len(problems) == 1
        assert "3001" in problems[0]


class TestRun:
    """実行の組み立て（起動はしない）。"""

    def test_dry_run_starts_nothing(self, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise AssertionError("dry-run must not start anything")

        monkeypatch.setattr(tool.subprocess, "run", boom)
        monkeypatch.setattr(tool.subprocess, "Popen", boom)

        code = tool.main_with(["run", "--dry-run", "--tasks", "food", "--repeats", "2"])

        out = capsys.readouterr().out
        assert code == 0
        assert "1 課題 x 2 回" in out
        assert "dry-run" in out

    def test_the_run_command_can_be_left_out(self):
        assert tool.with_default_command(["--dry-run"]) == ["run", "--dry-run"]
        assert tool.with_default_command([]) == ["run"]
        assert tool.with_default_command(["report", "a.jsonl"]) == ["report", "a.jsonl"]
        assert tool.with_default_command(["--help"]) == ["--help"]

    def test_the_seed_is_the_streams_seed_from_env(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\nMC_SEED=12345\n", encoding="utf-8")

        assert tool.default_seed(env) == "12345"

    def test_without_a_seed_a_fixed_one_is_used(self, tmp_path):
        assert tool.default_seed(tmp_path / "none") == tool.DEFAULT_SEED

    def test_steps_are_counted_from_the_step_log(self, tmp_path):
        steps = tmp_path / "logs" / "step_record_dir"
        steps.mkdir(parents=True)
        (steps / "watch-1.jsonl").write_text(
            '{"event": "pick", "step": 1}\n{"event": "result", "step": 1}\n{"event": "result", "step": 2}\n',
            encoding="utf-8",
        )

        assert tool.count_steps(tmp_path) == 2
