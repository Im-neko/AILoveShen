"""tools/goal_outcomes.py（小目標の成績の集計、設計書 82）のテスト。"""

import importlib.util
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "goal_outcomes", Path(__file__).resolve().parents[3] / "tools" / "goal_outcomes.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def _entry(ended: str, category: str, kind="have(food)", seconds=60.0, steps=10, deaths=0, **kw):
    return {
        "event": "goal_end",
        "ended_at": f"2026-10-02T{ended}+00:00",
        "started_at": f"2026-10-02T{ended}+00:00",
        "category": category,
        "kind": kind,
        "predicate": kind.split("(")[0],
        "seconds": seconds,
        "steps": steps,
        "deaths": deaths,
        **kw,
    }


class TestSummarize:
    """成功率は 達成 ÷ (達成 + 失敗 + 死亡)。切った分は別に数える。"""

    def test_cut_goals_are_not_counted_as_losses(self):
        s = tool.summarize(
            [
                _entry("01:00:00", "met"),
                _entry("01:01:00", "failed"),
                _entry("01:02:00", "cut"),
                _entry("01:03:00", "cut"),
            ]
        )

        assert s["n"] == 4
        assert s["cut"] == 2
        assert s["success_rate"] == 0.5

    def test_deaths_count_as_failures_and_are_totalled_per_hour(self):
        s = tool.summarize(
            [
                _entry("01:00:00", "died", seconds=1800, deaths=1),
                _entry("01:30:00", "met", seconds=1800),
            ]
        )

        assert s["success_rate"] == 0.5
        assert s["deaths"] == 1
        assert s["deaths_per_hour"] == 1.0
        assert s["met_per_hour"] == 1.0

    def test_a_duration_that_spans_a_restart_is_not_used(self):
        """再起動をまたいだ古い記録の秒数（止まっていた間を含む）は、時間にも中央値にも使わない。"""
        s = tool.summarize(
            [_entry("01:00:00", "met", seconds=78991.4), _entry("01:01:00", "met", seconds=60)]
        )

        assert s["median_seconds"] == 60
        assert s["hours"] == 60 / 3600

    def test_rates_are_unknown_without_a_judged_goal(self):
        s = tool.summarize([_entry("01:00:00", "cut")])

        assert s["success_rate"] is None

    def test_medians_ignore_missing_steps(self):
        s = tool.summarize(
            [_entry("01:00:00", "met", steps=None), _entry("01:01:00", "met", steps=20)]
        )

        assert s["median_steps"] == 20


class TestKindOf:
    """目標の種類は、数や名前つきの引数を無視した鍵。"""

    def test_the_count_and_named_arguments_are_left_out(self):
        assert tool.kind_of("have(food, 16)") == "have(food)"
        assert tool.kind_of("at_home(dig_depth=4)") == "at_home"
        assert tool.kind_of("through_night()") == "through_night"


class TestGrouping:
    """見出しごとに分ける。"""

    def test_by_kind_ignores_the_number(self):
        groups = tool.analyse(
            [
                _entry("01:00:00", "met", kind="have(food)"),
                _entry("01:01:00", "failed", kind="have(food)"),
                _entry("01:02:00", "met", kind="built(house)"),
            ],
            [],
            "kind",
        )

        assert groups["have(food)"]["n"] == 2
        assert groups["built(house)"]["n"] == 1

    def test_by_run_uses_the_latest_run_that_started_before_the_goal_ended(self):
        runs = [
            {
                "event": "run",
                "at": "2026-10-02T00:00:00+00:00",
                "git": {"commit": "a" * 40},
                "control": "tools",
                "jev_model": "j",
            },
            {
                "event": "run",
                "at": "2026-10-02T02:00:00+00:00",
                "git": {"commit": "b" * 40, "dirty": True, "diff": "f00"},
                "control": "tools",
                "jev_model": "j",
            },
        ]
        groups = tool.analyse(
            [_entry("01:00:00", "met"), _entry("03:00:00", "failed")], runs, "run"
        )

        names = list(groups)
        assert len(names) == 2
        assert "aaaaaaa" in names[0]
        assert "bbbbbbb+f00" in names[1]

    def test_goals_before_any_run_record_are_labelled(self):
        groups = tool.analyse([_entry("01:00:00", "met")], [], "run")

        assert list(groups) == [tool.NO_RUN]

    def test_since_and_kind_narrow_the_goals(self):
        entries = [
            _entry("01:00:00", "met", kind="have(food)"),
            _entry("05:00:00", "met", kind="have(food)"),
            _entry("05:30:00", "met", kind="built(house)"),
        ]
        since = datetime.fromisoformat("2026-10-02T04:00:00+00:00")

        groups = tool.analyse(entries, [], "all", since=since, kind="have(food")

        assert groups["全部"]["n"] == 1


class TestParseLog:
    """記録の前の分を、メインログの目標の行から補う。"""

    LOG = (
        "2026-10-01 10:24:33 | INFO     | ailoveshen.application.use_cases.play:_start_goal:1508 - "
        "目標: have(wheat_seeds, 8)（小麦畑を作る のため）（goal through_night() is over: the night has passed (day)） - 種が要る; 残り 8\n"
        "2026-10-01 10:26:57 | INFO     | ailoveshen.application.use_cases.play:_start_goal:1508 - "
        "目標: have(wooden_hoe, 1)（小麦畑を作る (facility(wheat_farm_1)) のため）（goal have(wheat_seeds, 8) is met） - クワ; 残り 7\n"
        "2026-10-01 10:30:00 | INFO     | ailoveshen.application.use_cases.play:_start_goal:1508 - "
        "目標: through_night()（生存のため）（goal have(wooden_hoe, 1) is cut because the streamer died and respawned (x)） - 夜; 残り 1\n"
        "2026-10-01 10:31:00 | INFO     | other:thing:1 - 目標: not a start line\n"
    )

    def test_each_start_line_ends_the_goal_before_it(self):
        entries = tool.parse_log(self.LOG)

        assert [(e["goal"], e["category"]) for e in entries] == [
            ("have(wheat_seeds, 8)", "met"),
            ("have(wooden_hoe, 1)", "died"),
        ]
        assert entries[0]["seconds"] == 144.0
        assert entries[0]["kind"] == "have(wheat_seeds)"
        assert entries[1]["deaths"] == 1

    def test_the_first_line_has_no_goal_before_it(self):
        entries = tool.parse_log(self.LOG.splitlines()[0] + "\n")

        assert entries == []

    def test_a_goal_after_a_restart_is_not_ended_by_the_old_run(self):
        log = "2026-10-01 10:24:33 | INFO     | a:_start_goal:1 - 目標: have(log, 4)（生存のため）（no goal yet） - x; 残り 4\n"

        assert tool.parse_log(log) == []

    def test_a_rotated_zip_is_read(self, tmp_path):
        path = tmp_path / "old.log.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("old.log", self.LOG)

        assert len(tool.parse_log(tool.read_log(path))) == 2


class TestMerge:
    """記録のある期間は記録を使い、それより前のログ由来の分だけ足す。"""

    def test_log_goals_overlapping_the_records_are_dropped(self):
        recorded = [_entry("03:00:00", "met", started_at="2026-10-02T02:58:00+00:00")]
        from_log = [
            {
                "event": "goal_end",
                "ended_at": "2026-10-02T01:00:00+00:00",
                "started_at": "2026-10-02T00:59:00+00:00",
                "category": "met",
            },
            {
                "event": "goal_end",
                "ended_at": "2026-10-02T03:00:00+00:00",
                "started_at": "2026-10-02T02:58:00+00:00",
                "category": "met",
            },
        ]

        merged = tool.merge_log_entries(recorded, from_log)

        assert len(merged) == 2

    def test_without_records_the_log_is_used_whole(self):
        from_log = [{"ended_at": "2026-10-02T03:00:00+00:00"}]

        assert tool.merge_log_entries([], from_log) == from_log


class TestFiles:
    """ディレクトリから読む。"""

    def test_goal_end_rows_are_read_and_broken_lines_are_skipped(self, tmp_path):
        (tmp_path / "watch-1.jsonl").write_text(
            json.dumps(_entry("01:00:00", "met"))
            + "\nnot json\n"
            + json.dumps({"event": "other"})
            + "\n",
            encoding="utf-8",
        )

        assert len(tool.load_entries(tmp_path)) == 1

    def test_a_missing_directory_gives_nothing(self, tmp_path):
        assert tool.load_entries(tmp_path / "none") == []
        assert tool.load_runs(tmp_path / "none") == []


class TestRender:
    """表にする。"""

    def test_a_table_has_a_row_per_group(self):
        groups = tool.analyse(
            [_entry("01:00:00", "met"), _entry("01:01:00", "died", deaths=1)], [], "all"
        )

        text = tool.render(groups)

        assert "成功率" in text
        assert "全部" in text
        assert "50%" in text
